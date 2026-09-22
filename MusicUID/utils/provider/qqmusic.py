"""QQ Music provider: public search endpoint (search and info only for now)."""

from typing import Final

from .base import SongInfo, SongCollection
from ..http import get_json, post_json
from ..json_tools import to_obj, get_int, get_obj, get_str, get_list, join_names

SEARCH_URL: Final[str] = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
VKEY_URL: Final[str] = "https://u.y.qq.com/cgi-bin/musicu.fcg"
COVER_URL: Final[str] = "https://y.qq.com/music/photo_new/T002R300x300M000{}.jpg"
HEADERS: Final[dict[str, str]] = {"Referer": "https://y.qq.com/portal/player.html"}

# 匿名取流的音质档位（前缀, 后缀）：m4a 体积最小，128kbps mp3 兜底
QUALITY_TIERS: Final[tuple[tuple[str, str], ...]] = (("C400", "m4a"), ("M500", "mp3"))

# 匿名取流固定使用 guid=10000、uin=0；会员曲目会返回该 code
LOGIN_REQUIRED: Final[int] = 104003


class QqMusicProvider:
    """QQ音乐：公开搜索接口 + 匿名取流（免费曲目）。"""

    platform = "qq"
    display_name = "QQ音乐"
    supports_play = True

    async def search(self, keyword: str, limit: int) -> list[SongInfo]:
        """Search songs through the public ``client_search_cp`` endpoint.

        Args:
            keyword: User supplied keywords.
            limit: Maximum number of results.

        Returns:
            Matching songs, possibly empty.

        Raises:
            MusicRequestError: The platform request failed.
        """
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
            headers=HEADERS,
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
        """Resolve a playable audio URL anonymously through ``CgiGetVkey``.

        匿名取流用固定 ``guid=10000`` 与 ``uin=0``，只有 ``pay_play`` 为 0 的免费曲目
        能拿到地址；会员曲目返回 :data:`LOGIN_REQUIRED`，此时只能提示用户该曲需会员。
        ``filename`` 必须写成「前缀 + songmid + songmid + 后缀」，songmid 要拼两遍。

        Args:
            song: A song returned by :meth:`search`.

        Returns:
            A downloadable URL, or an empty string when nothing is playable.

        Raises:
            MusicRequestError: The platform request failed.
        """
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
                            "uin": "0",
                            "loginflag": 1,
                            "platform": "20",
                        },
                    },
                    "loginUin": "0",
                    "comm": {"uin": "0", "format": "json", "ct": 24, "cv": 0},
                },
                headers=HEADERS,
                as_json=True,
            )
            data = get_obj(get_obj(to_obj(payload), "req_1"), "data")
            infos = [to_obj(item) for item in get_list(data, "midurlinfo")]
            purl = get_str(infos[0], "purl") if infos else ""
            hosts = [item for item in get_list(data, "sip") if isinstance(item, str) and item]
            if purl and hosts:
                return f"{hosts[0]}{purl}"
        return ""
