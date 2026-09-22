"""点歌 / 播放 / 选歌 / 歌词 指令。"""

from __future__ import annotations

import asyncio
from typing import Final

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event

from .session import get_session, save_session
from ..utils.http import MusicRequestError
from ..musicuid_card import PlatformResult, send_song_list
from ..utils.delivery import deliver_audio
from ..utils.provider import PROVIDERS, DEFAULT_PLATFORM, SongInfo, get_provider, resolve_platform
from ..musicuid_config import music_config

sv_song_request = SV("点歌", priority=5)
sv_song_pick = SV("选歌播放", priority=5)
sv_song_lyric = SV("歌词", priority=5)

# 歌词与列表提示类文案的长度上限，避免超出平台单条消息限制
LYRIC_LIMIT = 1500

REQUEST_TIP = "请输入歌名，例如：点歌 晴天"
PLATFORM_TIP = "默认同时搜三个平台；也可只搜一个：点歌 QQ 晴天 / 点歌 酷狗 晴天 / 点歌 网易 晴天"

# 未指定平台时的搜索顺序，与卡片上的分组顺序一致
SEARCH_ORDER: Final[tuple[str, ...]] = ("netease", "qq", "kugou")


def default_platform() -> str:
    """Read the configured default platform, falling back when invalid.

    Returns:
        A provider key that exists in the registry.
    """
    value = music_config.get_config("default_platform").data
    return value if value in PROVIDERS else DEFAULT_PLATFORM


def parse_platform(text: str, fallback: str) -> tuple[str, str]:
    """Split an optional leading platform word from the command text.

    Args:
        text: Event text after the command keyword.
        fallback: Platform used when no platform word is present.

    Returns:
        A ``(platform_key, remaining_text)`` tuple.
    """
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return fallback, ""
    key = resolve_platform(parts[0])
    if not key:
        return fallback, text.strip()
    return key, parts[1].strip() if len(parts) > 1 else ""


async def search_songs(platform: str, keyword: str) -> list[SongInfo]:
    """Search a platform with the configured list size.

    Args:
        platform: Provider key.
        keyword: User supplied keywords.

    Returns:
        Matching songs, possibly empty.

    Raises:
        MusicRequestError: The platform request failed.
    """
    limit = music_config.get_config("max_list").data
    return await get_provider(platform).search(keyword, limit)


async def search_platform(platform: str, keyword: str) -> PlatformResult:
    """Search one platform, turning a request failure into a displayable result.

    Args:
        platform: Provider key.
        keyword: User supplied keywords.

    Returns:
        The platform outcome; never raises for a plain search failure.
    """
    try:
        songs = await search_songs(platform, keyword)
    except MusicRequestError as e:
        logger.warning(f"[MusicUID] {platform} 搜索失败：{e}")
        return PlatformResult(platform=platform, error=f"搜索失败（{e}）")
    return PlatformResult(platform=platform, songs=songs)


async def play_song(bot: Bot, song: SongInfo) -> None:
    """Resolve and deliver one song, reporting failures to the user.

    Args:
        bot: Bot wrapper bound to the current event.
        song: Song chosen by the user.
    """
    provider = get_provider(song.platform)
    if not provider.supports_play:
        await bot.send(f"{provider.display_name} 暂不支持音频下发，只提供搜索与信息展示")
        return
    try:
        audio_url = await provider.play_url(song)
    except MusicRequestError as e:
        await bot.send(f"获取播放地址失败：{e}")
        return
    if not audio_url:
        await bot.send(f"《{song.name}》无法播放：可能无版权、需要 VIP 或该音质不可用")
        return

    header = f"🎵 {song.name} - {song.singers}"
    if song.album:
        header += f"\n💽 {song.album}"
    reason = await deliver_audio(
        bot,
        song,
        audio_url,
        header=header,
        send_voice=music_config.get_config("send_voice").data,
        send_file=music_config.get_config("send_file").data,
        local_ref=music_config.get_config("local_file_ref").data,
        timeout=float(music_config.get_config("download_timeout").data),
        keep_sec=music_config.get_config("keep_temp_sec").data,
        voice_max_mb=music_config.get_config("voice_max_mb").data,
        voice_bitrate=music_config.get_config("voice_bitrate").data,
    )
    if reason:
        await bot.send(reason)


@sv_song_request.on_command(("点歌", "搜歌", "搜索歌曲"))
async def song_request(bot: Bot, ev: Event) -> None:
    """搜索歌曲并列出结果：未指定平台时三平台并发搜索。

    Args:
        bot: Bot wrapper bound to the current event.
        ev: The triggering event.
    """
    platform, keyword = parse_platform(ev.text, "")
    if not keyword:
        await bot.send(f"{REQUEST_TIP}\n{PLATFORM_TIP}")
        return
    if platform:
        results = [await search_platform(platform, keyword)]
    else:
        results = list(await asyncio.gather(*(search_platform(p, keyword) for p in SEARCH_ORDER)))
    merged = [song for result in results for song in result.songs]
    if merged:
        # 编号全平台连续，会话里存合并后的列表即可让 听N 直接命中
        save_session(ev, platform or "multi", keyword, merged)
    await send_song_list(bot, keyword, results, "听1")


@sv_song_request.on_command(("播放", "点播"))
async def song_play(bot: Bot, ev: Event) -> None:
    """搜索并直接播放第一首结果。

    Args:
        bot: Bot wrapper bound to the current event.
        ev: The triggering event.
    """
    platform, keyword = parse_platform(ev.text, default_platform())
    if not keyword:
        await bot.send(f"请输入歌名，例如：播放 晴天\n{PLATFORM_TIP}")
        return
    try:
        songs = await search_songs(platform, keyword)
    except MusicRequestError as e:
        await bot.send(f"搜索失败：{e}")
        return
    if not songs:
        await bot.send(f"没有搜到「{keyword}」相关的歌曲")
        return
    save_session(ev, platform, keyword, songs)
    await play_song(bot, songs[0])


@sv_song_pick.on_regex(r"^听\s*(?P<index>\d+)$", block=True)
async def song_pick(bot: Bot, ev: Event) -> None:
    """播放当前列表中的第 N 首。

    Args:
        bot: Bot wrapper bound to the current event.
        ev: The triggering event.
    """
    session = get_session(ev)
    if session is None:
        await bot.send("当前没有可选的歌曲列表，请先发送「点歌 歌名」")
        return
    index = int(ev.regex_dict["index"])
    if index < 1 or index > len(session.songs):
        await bot.send(f"序号超出范围，当前列表共 {len(session.songs)} 首")
        return
    await play_song(bot, session.songs[index - 1])


@sv_song_lyric.on_command("歌词")
async def song_lyric(bot: Bot, ev: Event) -> None:
    """查询并返回歌曲歌词。

    Args:
        bot: Bot wrapper bound to the current event.
        ev: The triggering event.
    """
    platform, keyword = parse_platform(ev.text, default_platform())
    if not keyword:
        await bot.send(f"请输入歌名，例如：歌词 晴天\n{PLATFORM_TIP}")
        return
    try:
        songs = await search_songs(platform, keyword)
        if not songs:
            await bot.send(f"没有搜到「{keyword}」相关的歌曲")
            return
        lyrics = await get_provider(platform).lyric(songs[0])
    except MusicRequestError as e:
        await bot.send(f"歌词查询失败：{e}")
        return
    if not lyrics.strip():
        await bot.send(f"《{songs[0].name}》暂无歌词（{get_provider(platform).display_name}）")
        return
    body = lyrics.strip()
    if len(body) > LYRIC_LIMIT:
        body = body[:LYRIC_LIMIT] + "\n……（歌词过长已截断）"
    await bot.send(f"🎵 {songs[0].name} - {songs[0].singers}\n\n{body}")
