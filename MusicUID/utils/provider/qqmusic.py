"""QQ Music provider: public search plus anonymous / logged-in url resolution."""

import re
from typing import Final

from gsuid_core.logger import logger

from .base import SongInfo, SongCollection
from ..http import get_json, post_json
from ..json_tools import to_obj, get_int, get_obj, get_str, get_list, join_names
from ...musicuid_config import music_config

SEARCH_URL: Final[str] = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
VKEY_URL: Final[str] = "https://u.y.qq.com/cgi-bin/musicu.fcg"
COVER_URL: Final[str] = "https://y.qq.com/music/photo_new/T002R300x300M000{}.jpg"
HEADERS: Final[dict[str, str]] = {"Referer": "https://y.qq.com/portal/player.html"}

# 音质档位（前缀, 后缀）：先试 320kbps mp3，匿名或免费曲目拿不到时退到 m4a
QUALITY_TIERS: Final[tuple[tuple[str, str], ...]] = (("M800", "mp3"), ("C400", "m4a"))

# 取流固定使用 guid=10000；登录态缺失或曲目权限不足时返回该 code
LOGIN_REQUIRED: Final[int] = 104003


class QqMusicProvider:
    """QQ音乐：搜索接口 + 匿名 / 登录两条取流路径。"""

    platform = "qq"
    display_name = "QQ音乐"
    supports_play = True

    async def search(self, keyword: str, limit: int) -> list[SongInfo]:
        """Search songs through the public ``client_search_cp`` endpoint.

        填了 ``qqmusic_cookie`` 时搜索也会带上登录态：QQ音乐对未登录的搜索请求会返回
        空结果，带上 Cookie 才稳定。

        Args:
            keyword: User supplied keywords.
            limit: Maximum number of results.

        Returns:
            Matching songs, possibly empty.

        Raises:
            MusicRequestError: The platform request failed.
        """
        headers = dict(HEADERS)
        cookie = music_config.get_config("qqmusic_cookie").data
        if cookie:
            headers["Cookie"] = cookie
        payload = await get_json(
            SEARCH_URL,
            params={
                "w": keyword,
                "n": limit,
                "p": 1,
                "cr": 1,
                "aggr": 1,
                "format": "json",
                "inCharset": "utf8",
                "outCharset": "utf-8",
                "platform": "yqq.json",
                "needNewCode": 0,
            },
            headers=headers,
        )
        song_node = get_obj(get_obj(to_obj(payload), "data"), "song")
        songs: list[SongInfo] = []
        for raw in get_list(song_node, "list"):
            item = to_obj(raw)
            song_mid = get_str(item, "songmid")
            if not song_mid:
                continue
            album_mid = get_str(item, "albummid")
            songs.append(
                SongInfo(
                    platform=self.platform,
                    song_id=song_mid,
                    name=get_str(item, "songname", "未知歌曲"),
                    singers=join_names(item.get("singer")) or "未知歌手",
                    album=get_str(item, "albumname"),
                    duration_sec=get_int(item, "interval"),
                    cover_url=COVER_URL.format(album_mid) if album_mid else "",
                    payplay=get_int(get_obj(item, "pay"), "payplay") > 0,
                )
            )
        return songs

    async def detail(self, song_id: str) -> SongInfo | None:
        """Return ``None``: QQ音乐分享链接解析尚未接入。

        Args:
            song_id: QQ音乐 songmid.

        Returns:
            Always ``None``.
        """
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
        免费曲目能拿到地址（会员曲目一律返回 :data:`LOGIN_REQUIRED`）；填入
        ``qqmusic_cookie`` 后改用 Cookie 里的真实 QQ 号并带上登录态，会员曲目与 320kbps
        档位才会下发。

        Args:
            song: A song returned by :meth:`search`.

        Returns:
            A downloadable URL, or an empty string when nothing is playable.

        Raises:
            MusicRequestError: The platform request failed.
        """
        headers = dict(HEADERS)
        cookie = music_config.get_config("qqmusic_cookie").data
        uin = "0"
        if cookie:
            headers["Cookie"] = cookie
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
        return ""
