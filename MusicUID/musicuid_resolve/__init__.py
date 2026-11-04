"""自动解析聊天里出现的网易云 / QQ音乐分享链接（单曲 / 歌单 / 专辑）。"""

from __future__ import annotations

import re
import json
from dataclasses import dataclass
from urllib.parse import urlparse

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event

from ..utils.http import MusicRequestError, get_text, get_location
from ..musicuid_card import PlatformResult, send_song_list
from ..musicuid_play import play_song, search_songs
from ..utils.provider import PROVIDERS
from ..musicuid_config import music_config
from ..utils.json_tools import to_obj, get_obj, get_str
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

# QQ音乐 App 分享到群里的是 json 卡片（app 名固定）。第三方协议能拿到原始 json，
# 但 GsCore 只把 text 段拼进 raw_text，所以卡片必须单独扫 content
JSON_CARD_APP = "com.tencent.music.lua"
JSON_CARD_TYPES = ("json", "light_app", "ark")
JSON_CARD_TEXT_RE = re.compile(r"\[json:data=(.+?)\]", re.S)


@dataclass(frozen=True)
class MusicCard:
    """QQ音乐卡片里抽出的曲目信息。

    Attributes:
        songmid: QQ音乐 songmid，卡片没带时为空。
        title: 歌名。
        singers: 歌手。
    """

    songmid: str
    title: str
    singers: str


def decode_card(raw: str) -> dict[str, object]:
    """解析一段卡片 json，取出 QQ音乐卡片的 ``meta.music``。

    Args:
        raw: 消息段里的原始 json 文本。

    Returns:
        卡片内容对象；不是 QQ音乐卡片时返回空对象。
    """
    try:
        payload = to_obj(json.loads(raw))
    except (json.JSONDecodeError, TypeError):
        return {}
    if get_str(payload, "app") != JSON_CARD_APP:
        return {}
    return get_obj(get_obj(payload, "meta"), "music")


def iter_music_cards(ev: Event) -> list[MusicCard]:
    """从一条消息里找出所有 QQ音乐卡片。

    卡片可能以 json 段上报，也可能被适配器塞进 raw_text 的 ``[json:data=...]``，
    两种都收。

    Args:
        ev: The triggering event.

    Returns:
        解析出的卡片；非卡片消息返回空列表。
    """
    raws: list[str] = []
    for msg in ev.content:
        if msg.type not in JSON_CARD_TYPES:
            continue
        data = msg.data
        if isinstance(data, str):
            raws.append(data)
        elif isinstance(data, dict):
            inner = get_str(to_obj(data), "data")
            if inner:
                raws.append(inner)
    raws.extend(match.group(1) for match in JSON_CARD_TEXT_RE.finditer(ev.raw_text))

    cards: list[MusicCard] = []
    for raw in raws:
        music = decode_card(raw)
        if not music:
            continue
        # 卡片不一定带 songmid，jumpUrl 里的 songDetail/{mid} 是更可靠的来源
        songmid = get_str(music, "songmid")
        if not songmid:
            match = QQ_SONGMID_RE.search(get_str(music, "jumpUrl"))
            songmid = match.group(1) if match else ""
        title = get_str(music, "title")
        if not songmid and not title:
            continue
        cards.append(MusicCard(songmid=songmid, title=title, singers=get_str(music, "desc")))
    return cards


def has_card_segment(ev: Event) -> bool:
    """快速判断消息里有没有卡片段，避免每条消息都去解析 json。

    Args:
        ev: The triggering event.

    Returns:
        Whether the message may carry a QQ music card.
    """
    if "[json:data=" in ev.raw_text:
        return True
    return any(msg.type in JSON_CARD_TYPES for msg in ev.content)


async def expand_short_link(url: str) -> str:
    """Expand a known short link into its full target URL.

    Args:
        url: The URL found in the message.

    Returns:
        The expanded URL, or the input when it is not a short link.

    Raises:
        MusicRequestError: The redirect request failed.
    """
    hostname = urlparse(url).hostname or ""
    if not any(hostname == host or hostname.endswith(f".{host}") for host in SHORT_HOSTS):
        return url
    status, location = await get_location(url)
    if 300 <= status < 400 and location:
        return location
    return url


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


async def resolve_card(bot: Bot, card: MusicCard) -> None:
    """播放卡片里的歌：优先按 songmid 精确取，退化到按歌名搜索。

    Args:
        bot: Bot wrapper bound to the current event.
        card: Card parsed out of the message.
    """
    if card.songmid:
        await resolve_song(bot, "qq", card.songmid)
        return
    keyword = f"{card.title} {card.singers}".strip()
    songs = await search_songs("qq", keyword)
    if not songs:
        await bot.send(f"没能找到《{card.title}》")
        return
    await play_song(bot, songs[0])


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
    # 一条消息里可能既有普通链接、又有音乐链接，只取第一个会把后者静默漏掉，
    # 所以逐个 URL 尝试识别，命中音乐链接就处理并结束
    for match in URL_RE.finditer(ev.raw_text):
        try:
            target = await expand_short_link(match.group(0))
        except MusicRequestError as e:
            logger.debug(f"[MusicUID] 短链展开失败：{e}")
            continue

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
                continue
            if not song_hash:
                await bot.send("没能从这条酷狗分享链接里解析出歌曲")
                return
            await resolve_song(bot, "kugou", song_hash)
            return

        collection_match = NETEASE_COLLECTION_RE.search(target)
        if collection_match is not None:
            await resolve_collection(bot, ev, collection_match.group(1), collection_match.group(2))
            return


@sv_resolve.on_message(block=False)
async def resolve_music_card(bot: Bot, ev: Event) -> None:
    """解析 App 分享的 QQ音乐卡片并直接播放里面的歌。

    Args:
        bot: Bot wrapper bound to the current event.
        ev: The triggering event.
    """
    if not music_config.get_config("enable_resolve").data:
        return
    if not has_card_segment(ev):
        return
    cards = iter_music_cards(ev)
    if cards:
        await resolve_card(bot, cards[0])
