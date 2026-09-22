"""自动解析聊天里出现的网易云 / QQ音乐分享链接（单曲 / 歌单 / 专辑）。"""

from __future__ import annotations

import re

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event

from ..utils.http import MusicRequestError, get_text, get_location
from ..musicuid_card import PlatformResult, send_song_list
from ..musicuid_play import play_song
from ..utils.provider import PROVIDERS
from ..musicuid_config import music_config
from ..musicuid_play.session import save_session

sv_resolve = SV("链接解析", priority=6)

URL_RE = re.compile(r"https?://[\w\-./?=&%#:@+~]+")
# 网易云：单曲 song?id= / 歌单 playlist?id= / 专辑 album?id=
NETEASE_SONG_RE = re.compile(r"song\?id=(\d+)")
NETEASE_COLLECTION_RE = re.compile(r"(playlist|album)\?id=(\d+)")
# QQ音乐三种分享形态：playsong.html?songid= 给数字 id；App 卡片 jump_url 给 ?songmid=；
# 网页版给 songDetail/{mid}。三个都要认，且 songid 与 songmid 不能互相误匹配。
QQ_SONGID_RE = re.compile(r"[?&]songid=(\d+)")
QQ_SONGMID_RE = re.compile(r"(?:[?&]songmid=|songDetail/)([0-9A-Za-z]+)")
# 酷狗两种分享形态：卡片/长链直接带 32 位 hash（它就是取流用的 song_id）；
# App 分享的纯链接只带 chain 标识，hash 藏在移动版页面内嵌的 JSON 里，需要先换出来
KUGOU_HASH_RE = re.compile(r"[?&]hash=([0-9A-Fa-f]{32})")
KUGOU_CHAIN_RE = re.compile(r"[?&]chain=([A-Za-z0-9]+)")
KUGOU_CHAIN_URL = "https://m.kugou.com/share/song.html"
KUGOU_HASH_JSON_RE = re.compile(r'"hash"\s*:\s*"([0-9A-Fa-f]{32})"')
# 酷狗桌面 UA 会被 302 到另一个页面，必须用移动端 UA 才能直接拿到内嵌 JSON
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)
# 需要先跟随 302 才能拿到真实链接的短链域名
SHORT_HOSTS = ("163cn.tv", "c6.y.qq.com")


async def expand_short_link(url: str) -> str:
    """Expand a known short link into its full target URL.

    Args:
        url: The URL found in the message.

    Returns:
        The expanded URL, or the input when it is not a short link.

    Raises:
        MusicRequestError: The redirect request failed.
    """
    if not any(host in url for host in SHORT_HOSTS):
        return url
    location = await get_location(url)
    return location or url


async def resolve_kugou_chain(chain: str) -> str:
    """Expand a KuGou ``chain`` share link into a file hash.

    纯链接形如 ``m.kugou.com/share/song.html?chain=xxx``，hash 不在 query 里，而是嵌在
    移动版页面的 JSON 中，所以这里抓 HTML 再正则提取。

    Args:
        chain: The chain identifier taken from the share link.

    Returns:
        The 32-char file hash, or an empty string when it cannot be extracted.

    Raises:
        MusicRequestError: The page request failed.
    """
    html = await get_text(KUGOU_CHAIN_URL, params={"chain": chain}, headers={"User-Agent": MOBILE_UA})
    match = KUGOU_HASH_JSON_RE.search(html)
    return match.group(1) if match else ""


async def resolve_song(bot: Bot, platform: str, song_id: str) -> None:
    """Resolve one shared single track and play it.

    Args:
        bot: Bot wrapper bound to the current event.
        platform: Provider key the link belongs to.
        song_id: Platform song id taken from the link.
    """
    provider = PROVIDERS[platform]
    try:
        song = await provider.detail(song_id)
    except MusicRequestError as e:
        await bot.send(f"解析失败：{e}")
        return
    if song is None:
        await bot.send("没有找到这首歌曲的信息，可能已下架")
        return
    await play_song(bot, song)


async def resolve_collection(bot: Bot, ev: Event, kind: str, collection_id: str) -> None:
    """Resolve a shared playlist / album, list it and remember it for 听N.

    Args:
        bot: Bot wrapper bound to the current event.
        ev: The triggering event, used as the session key.
        kind: ``playlist`` or ``album``.
        collection_id: Netease collection id taken from the link.
    """
    provider = PROVIDERS["netease"]
    try:
        collection = await provider.collection(kind, collection_id)
    except MusicRequestError as e:
        await bot.send(f"解析失败：{e}")
        return
    if collection is None or not collection.songs:
        await bot.send("没有解析到该歌单/专辑的歌曲，可能已下架或需要登录")
        return
    limit = music_config.get_config("max_list").data
    songs = collection.songs[:limit]
    save_session(ev, provider.platform, collection.name, songs)
    await send_song_list(bot, collection.name, [PlatformResult(platform=provider.platform, songs=songs)], "听1")


@sv_resolve.on_regex(r"(music\.163\.com|163cn\.tv|y\.qq\.com|kugou\.com)", block=False)
async def resolve_share_link(bot: Bot, ev: Event) -> None:
    """解析分享链接：单曲直接播放，歌单/专辑列出歌曲并可通过 听N 选播。

    Args:
        bot: Bot wrapper bound to the current event.
        ev: The triggering event.
    """
    if not music_config.get_config("enable_resolve").data:
        return
    match = URL_RE.search(ev.raw_text)
    if match is None:
        return
    try:
        target = await expand_short_link(match.group(0))
    except MusicRequestError as e:
        logger.debug(f"[MusicUID] 短链展开失败：{e}")
        return

    netease_song = NETEASE_SONG_RE.search(target)
    if netease_song is not None:
        await resolve_song(bot, "netease", netease_song.group(1))
        return

    # QQ音乐三种分享格式：数字 songid、App 卡片的 songmid 参数、songDetail 路径，detail() 都认
    qq_song = QQ_SONGID_RE.search(target) or QQ_SONGMID_RE.search(target)
    if qq_song is not None:
        await resolve_song(bot, "qq", qq_song.group(1))
        return

    # 酷狗分享链接的两种形态：长链/卡片直接带 hash，App 纯链接只带 chain
    kugou_hash = KUGOU_HASH_RE.search(target)
    if kugou_hash is not None:
        await resolve_song(bot, "kugou", kugou_hash.group(1))
        return

    kugou_chain = KUGOU_CHAIN_RE.search(target)
    if kugou_chain is not None:
        try:
            song_hash = await resolve_kugou_chain(kugou_chain.group(1))
        except MusicRequestError as e:
            logger.debug(f"[MusicUID] 酷狗 chain 展开失败：{e}")
            return
        if not song_hash:
            await bot.send("没能从这条酷狗分享链接里解析出歌曲")
            return
        await resolve_song(bot, "kugou", song_hash)
        return

    collection_match = NETEASE_COLLECTION_RE.search(target)
    if collection_match is None:
        return
    await resolve_collection(bot, ev, collection_match.group(1), collection_match.group(2))
