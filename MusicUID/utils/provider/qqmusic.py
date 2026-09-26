"""QQ Music provider: public search, share-link detail and url resolution."""

import re
from typing import Final

from gsuid_core.logger import logger

from .base import SongInfo, SongCollection
from .custom_api import resolve_custom_api
from ..http import get_json, post_json
from ..json_tools import to_obj, get_int, get_obj, get_str, get_list, join_names
from ...musicuid_config import music_config
from ..login.qqmusic import load_qq_credential, is_credential_expiring, refresh_qq_credential

# musicu.fcg 同时承担搜索与取流；旧的 client_search_cp 已被腾讯关闭（一律返回 HTTP 500）
MUSICU_URL: Final[str] = "https://u.y.qq.com/cgi-bin/musicu.fcg"
DETAIL_URL: Final[str] = "https://c.y.qq.com/v8/fcg-bin/fcg_play_single_song.fcg"
VKEY_URL: Final[str] = MUSICU_URL
SEARCH_MODULE: Final[str] = "music.search.SearchCgiService"
SEARCH_METHOD: Final[str] = "DoSearchForQQMusicDesktop"
COVER_URL: Final[str] = "https://y.qq.com/music/photo_new/T002R300x300M000{}.jpg"
HEADERS: Final[dict[str, str]] = {"Referer": "https://y.qq.com/portal/player.html"}

# 音质档位（前缀, 后缀）：先试 320kbps mp3，匿名或免费曲目拿不到时退到 m4a
QUALITY_TIERS: Final[tuple[tuple[str, str], ...]] = (("M800", "mp3"), ("C400", "m4a"))

# 取流固定使用 guid=10000；登录态缺失或曲目权限不足时返回该 code
LOGIN_REQUIRED: Final[int] = 104003


async def _ensure_active_cookie() -> str:
    """确保 QQ 音乐凭据处于有效状态（若临近过期则自动触发静默续期）。"""
    cred = load_qq_credential()
    if cred is not None and is_credential_expiring(cred):
        logger.info("[MusicUID] QQ 音乐移动凭据临近过期，点歌调用前自动触发静默续签...")
        await refresh_qq_credential(cred)
    return music_config.get_config("qqmusic_cookie").data


async def _cookie_headers() -> dict[str, str]:
    """Build request headers, attaching the login cookie when configured.

    QQ音乐对未登录请求很苛刻：搜索会直接返回空列表，取流一律回 ``104003``，
    所以三个接口都走这里拿头部。

    Returns:
        Header dictionary with ``Referer`` and, when available, ``Cookie``.
    """
    headers = dict(HEADERS)
    cookie = await _ensure_active_cookie()
    if cookie:
        headers["Cookie"] = cookie
    return headers


class QqMusicProvider:
    """QQ音乐：搜索 / 分享链接详情 / 匿名与登录两条取流路径。"""

    platform = "qq"
    display_name = "QQ音乐"
    supports_play = True

    async def search(self, keyword: str, limit: int) -> list[SongInfo]:
        """Search songs through the desktop search CGI on ``musicu.fcg``.

        旧的 ``client_search_cp``（``c.y.qq.com/soso/...``）已被腾讯关闭，任何请求都返回
        HTTP 500，所以改走 ``DoSearchForQQMusicDesktop``。这个接口**必须带登录态**：
        不带 Cookie 时它返回 200 但列表为空，所以 ``qqmusic_cookie`` 是搜索可用的前提。

        Args:
            keyword: User supplied keywords.
            limit: Maximum number of results.

        Returns:
            Matching songs, possibly empty.

        Raises:
            MusicRequestError: The platform request failed.
        """
        headers = await _cookie_headers()
        payload = await post_json(
            MUSICU_URL,
            {
                "comm": {"ct": "19", "cv": "1859", "uin": "0"},
                "req": {
                    "method": SEARCH_METHOD,
                    "module": SEARCH_MODULE,
                    "param": {
                        "grp": 1,
                        "num_per_page": limit,
                        "page_num": 1,
                        "query": keyword,
                        "search_type": 0,
                    },
                },
            },
            headers=headers,
            as_json=True,
        )
        song_node = get_obj(get_obj(get_obj(get_obj(to_obj(payload), "req"), "data"), "body"), "song")
        songs: list[SongInfo] = []
        for raw in get_list(song_node, "list"):
            item = to_obj(raw)
            song_mid = get_str(item, "mid")
            if not song_mid:
                continue
            album = get_obj(item, "album")
            album_mid = get_str(album, "mid")
            songs.append(
                SongInfo(
                    platform=self.platform,
                    song_id=song_mid,
                    name=get_str(item, "title", "未知歌曲"),
                    singers=join_names(item.get("singer")) or "未知歌手",
                    album=get_str(album, "name"),
                    duration_sec=get_int(item, "interval"),
                    cover_url=COVER_URL.format(album_mid) if album_mid else "",
                    payplay=get_int(get_obj(item, "pay"), "pay_play") > 0,
                )
            )
        return songs

    async def detail(self, song_id: str) -> SongInfo | None:
        """Fetch one song by its numeric songid or its songmid.

        分享链接给的是数字 songid（``playsong.html?songid=...``），而取流要 songmid，
        所以统一经这个接口换一次；两种参数它都认。返回的 ``mid`` 就是 songmid。

        Args:
            song_id: QQ音乐数字 songid，或 ``songDetail/{songmid}`` 里的 songmid.

        Returns:
            The song, or ``None`` when the id cannot be resolved.

        Raises:
            MusicRequestError: The platform request failed.
        """
        params: dict[str, str | int] = {
            "format": "json",
            "platform": "yqq",
            "inCharset": "utf8",
            "outCharset": "utf-8",
        }
        params["songid" if song_id.isdigit() else "songmid"] = song_id
        headers = await _cookie_headers()
        payload = await get_json(DETAIL_URL, params=params, headers=headers)
        for raw in get_list(to_obj(payload), "data"):
            item = to_obj(raw)
            song_mid = get_str(item, "mid")
            if not song_mid:
                continue
            album = get_obj(item, "album")
            album_mid = get_str(album, "mid")
            return SongInfo(
                platform=self.platform,
                song_id=song_mid,
                name=get_str(item, "name", "未知歌曲"),
                singers=join_names(item.get("singer")) or "未知歌手",
                album=get_str(album, "name"),
                duration_sec=get_int(item, "interval"),
                cover_url=COVER_URL.format(album_mid) if album_mid else "",
                payplay=get_int(get_obj(item, "pay"), "pay_play") > 0,
            )
        return None

    async def collection(self, kind: str, collection_id: str) -> SongCollection | None:
        """Return ``None``: QQ音乐歌单/专辑链接解析尚未接入。

        Args:
            kind: ``playlist`` or ``album``.
            collection_id: QQ音乐歌单 / 专辑 id.

        Returns:
            Always ``None``.
        """
        return None

    async def lyric(self, song: SongInfo) -> str:
        """Return an empty lyric: QQ音乐歌词尚未接入。

        Args:
            song: A song returned by :meth:`search`.

        Returns:
            Always an empty string.
        """
        return ""

    async def play_url(self, song: SongInfo) -> str:
        """Resolve a playable audio URL through ``CgiGetVkey``.

        取流固定 ``guid=10000``，且 ``filename`` 必须写成「前缀 + songmid + songmid +
        后缀」——songmid 要拼两遍。匿名时 ``uin`` 传 ``0``，只有 ``pay_play`` 为 0 的
        免费曲目能拿到地址（会员曲目一律返回 :data:`LOGIN_REQUIRED`）；填了
        ``qqmusic_cookie`` 后改用 Cookie 里的真实 QQ 号并带上登录态，会员曲目与 320kbps
        档位才会下发。

        Args:
            song: A song returned by :meth:`search`.

        Returns:
            A downloadable URL, or an empty string when nothing is playable.

        Raises:
            MusicRequestError: The platform request failed.
        """
        priority = music_config.get_config("custom_api_priority").data
        if priority == "custom_first":
            custom_res = await resolve_custom_api(song)
            if custom_res:
                return custom_res

        headers = await _cookie_headers()
        cookie = headers.get("Cookie", "")
        uin = "0"
        if cookie:
            match = re.search(r"(?:^|;\s*)uin=([^;]*)", cookie)
            if match:
                uin = match.group(1)
            else:
                logger.warning("[MusicUID] QQ音乐 Cookie 缺少 uin 字段，已按匿名取流")
        for prefix, suffix in QUALITY_TIERS:
            payload = await post_json(
                VKEY_URL,
                {
                    "req_1": {
                        "module": "vkey.GetVkeyServer",
                        "method": "CgiGetVkey",
                        "param": {
                            "filename": [f"{prefix}{song.song_id}{song.song_id}.{suffix}"],
                            "guid": "10000",
                            "songmid": [song.song_id],
                            "songtype": [0],
                            "uin": uin,
                            "loginflag": 1,
                            "platform": "20",
                        },
                    },
                    "loginUin": uin,
                    "comm": {"uin": uin, "format": "json", "ct": 24, "cv": 0},
                },
                headers=headers,
                as_json=True,
            )
            data = get_obj(get_obj(to_obj(payload), "req_1"), "data")
            infos = [to_obj(item) for item in get_list(data, "midurlinfo")]
            purl = get_str(infos[0], "purl") if infos else ""
            hosts = [item for item in get_list(data, "sip") if isinstance(item, str) and item]
            if purl and hosts:
                return f"{hosts[0]}{purl}"
            result = get_int(infos[0], "result") if infos else "none"
            logger.debug(f"[MusicUID] QQ音乐 {suffix} 档未下发 {song.name}：result={result}")

        # 官方接口未下发有效直链（如 VIP 受限或无权播放）时，自动触发自建微服务兜底
        if priority != "custom_first":
            custom_res = await resolve_custom_api(song)
            if custom_res:
                logger.info(f"[MusicUID] 官方未下发 QQ 音乐《{song.name}》，触发自建 API 兜底取流")
                return custom_res

        return ""
