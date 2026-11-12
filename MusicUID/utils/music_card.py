"""Sign and send a QQ Music JSON card through OneBotUID."""

from __future__ import annotations

import asyncio
from typing import Final
from dataclasses import dataclass
from urllib.parse import urlsplit
from collections.abc import Callable, Awaitable

from gsuid_core.bot import Bot
from gsuid_core.logger import logger

from .http import get_json
from .provider import SongInfo
from ..musicuid_config import music_config

# 签名服务不可用时也不能拖住点歌的音频下发
CARD_REQUEST_TIMEOUT_SEC: Final[float] = 8.0

# 卡片点击后跳转的平台页；audio_url 才是签名服务要的 url
SOURCE_URLS: Final[dict[str, str]] = {
    "qq": "https://y.qq.com/n/ryqq/songDetail/{song_id}",
    "netease": "https://music.163.com/song?id={song_id}",
    "kugou": "https://www.kugou.com/song/#hash={song_id}",
}


@dataclass(frozen=True, slots=True)
class MusicCardInfo:
    """签名 QQ 音乐卡片所需的歌曲信息。

    Attributes:
        audio_url: 解析后的音频直链，签名服务的 ``url``。
        source_url: 卡片点击跳转的平台歌曲页。
        title: 歌曲标题。
        artist: 歌手。
        cover_url: 封面链接，可为空。
    """

    audio_url: str
    source_url: str
    title: str
    artist: str
    cover_url: str = ""


CallBotApi = Callable[[str, dict[str, object], str], Awaitable[object]]


def _load_call_bot_api() -> CallBotApi | None:
    """懒加载 OneBotUID 的接口调用函数；未安装返回 ``None``。"""
    try:
        from gsuid_core.plugins.OneBotUID.api import call_bot_api
    except ImportError:
        try:
            from OneBotUID.api import call_bot_api
        except ImportError:
            return None

    async def invoke(action: str, params: dict[str, object], self_id: str) -> object:
        return await call_bot_api(action, params, self_id=self_id)

    return invoke


def song_source_url(song: SongInfo) -> str:
    """返回卡片跳转用的平台歌曲页；不支持的平台返回空串。"""
    template = SOURCE_URLS.get(song.platform)
    return template.format(song_id=song.song_id) if template else ""


def build_card_info(song: SongInfo, audio_url: str) -> MusicCardInfo:
    """组装签名参数所需的歌曲信息。"""
    return MusicCardInfo(
        audio_url=audio_url,
        source_url=song_source_url(song),
        title=song.name,
        artist=song.singers,
        cover_url=song.cover_url,
    )


def _is_http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() in {"http", "https"}
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def _signed_message(payload: object) -> str:
    """取出签名接口返回的原始卡片 JSON。"""
    if not isinstance(payload, dict):
        return ""
    code = payload.get("code")
    if code not in (0, 1):
        return ""
    message = payload.get("message")
    return message.strip() if isinstance(message, str) else ""


def _validate_card(card_json: str, info: MusicCardInfo) -> bool:
    """校验签名结果确实对应这首歌，避免把错误卡片发出去。"""
    try:
        import json

        card = json.loads(card_json)
    except ValueError:
        return False
    if not isinstance(card, dict):
        return False
    if card.get("app") != "com.tencent.music.lua" or card.get("view") != "music":
        return False
    config = card.get("config")
    if not isinstance(config, dict) or not config.get("token"):
        return False
    meta = card.get("meta")
    music = meta.get("music") if isinstance(meta, dict) else None
    if not isinstance(music, dict):
        return False
    music_url = music.get("musicUrl")
    jump_url = music.get("jumpUrl")
    if not isinstance(music_url, str) or not isinstance(jump_url, str):
        return False
    return music_url.replace(" ", "+") == info.audio_url and jump_url == info.source_url


async def sign_music_card(sign_url: str, info: MusicCardInfo) -> str:
    """请求签名服务并返回原样可发送的卡片 JSON。"""
    if not sign_url or not _is_http_url(info.audio_url) or not _is_http_url(info.source_url):
        return ""
    params: dict[str, str | int] = {
        "url": info.audio_url,
        "jump": info.source_url,
    }
    if info.title:
        params["song"] = info.title
    if info.artist:
        params["singer"] = info.artist
    if info.cover_url:
        params["cover"] = info.cover_url
    payload = await asyncio.wait_for(
        get_json(sign_url, params=params),
        timeout=CARD_REQUEST_TIMEOUT_SEC,
    )
    card = _signed_message(payload)
    return card if card and _validate_card(card, info) else ""


async def send_music_card(bot: Bot, song: SongInfo, audio_url: str) -> bool:
    """按配置独立发送音乐卡片，失败只记日志。

    Args:
        bot: 当前事件绑定的 Bot。
        song: 已解析的平台歌曲。
        audio_url: 已解析的音频直链。

    Returns:
        ``True`` 表示卡片已被协议端接受。
    """
    if not music_config.get_config("send_music_card").data:
        return False
    direct = bot.ev.user_type == "direct"
    target_id = bot.ev.user_id if direct else bot.ev.group_id
    if not target_id:
        return False
    call_bot_api = _load_call_bot_api()
    if call_bot_api is None:
        return False
    sign_url = str(music_config.get_config("music_card_sign_url").data).strip()
    try:
        card = await sign_music_card(sign_url, build_card_info(song, audio_url))
    except Exception as e:  # noqa: BLE001 - 签名失败不能影响音频与卡片开关
        logger.debug(f"[MusicUID] 音乐卡片签名失败：{e}")
        return False
    if not card:
        logger.debug(f"[MusicUID] 音乐卡片签名结果为空（{song.platform}）")
        return False
    try:
        await call_bot_api(
            "send_private_msg" if direct else "send_group_msg",
            {
                "user_id" if direct else "group_id": int(target_id) if target_id.isdigit() else target_id,
                "message": [{"type": "json", "data": {"data": card}}],
            },
            bot.bot_self_id,
        )
    except Exception as e:  # noqa: BLE001 - 适配器异常不能中断音频主链路
        logger.debug(f"[MusicUID] OneBotUID 音乐卡片发送失败：{e}")
        return False
    logger.info(f"[MusicUID] 已通过 OneBotUID 下发音乐卡片：{song.name}")
    return True
