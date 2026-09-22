"""Kugou Music provider: public search plus login-free CDN url resolution."""

import hashlib
from typing import Final

from .base import SongInfo, SongCollection
from ..http import get_json
from ..json_tools import to_obj, get_int, get_obj, get_str, get_list

# 仅 mobiles.kugou.com 的证书与域名匹配，mobilecdn / msearchcdn 均报 Hostname mismatch
SEARCH_URL: Final[str] = "https://mobiles.kugou.com/api/v3/search/song"

# 旧版 CDN 只校验 md5(hash + 固定盐) 签名，不需要登录态与设备指纹
CDN_URL: Final[str] = "http://trackercdn.kugou.com/i/v2/"
CDN_SALT: Final[str] = "kgcloudv2"


class KugouProvider:
    """酷狗音乐：公开搜索接口 + 免登录 CDN 取流。"""

    platform = "kugou"
    display_name = "酷狗音乐"
    supports_play = True

    async def search(self, keyword: str, limit: int) -> list[SongInfo]:
        """Search songs through the public mobile CDN endpoint.

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
            params={"format": "json", "keyword": keyword, "page": 1, "pagesize": limit, "showtype": 1},
        )
        data = get_obj(to_obj(payload), "data")
        songs: list[SongInfo] = []
        for raw in get_list(data, "info"):
            item = to_obj(raw)
            song_hash = get_str(item, "hash")
            if not song_hash:
                continue
            songs.append(
                SongInfo(
                    platform=self.platform,
                    song_id=song_hash,
                    name=get_str(item, "songname", "未知歌曲"),
                    singers=get_str(item, "singername") or "未知歌手",
                    album=get_str(item, "album_name"),
                    duration_sec=get_int(item, "duration"),
                )
            )
        return songs

    async def detail(self, song_id: str) -> SongInfo | None:
        """Return ``None``: 酷狗分享链接解析尚未接入。

        Args:
            song_id: 酷狗 song hash.

        Returns:
            Always ``None``.
        """
        return None

    async def collection(self, kind: str, collection_id: str) -> SongCollection | None:
        """Return ``None``: 酷狗歌单/专辑链接解析尚未接入。

        Args:
            kind: ``playlist`` or ``album``.
            collection_id: 酷狗歌单 / 专辑 id.

        Returns:
            Always ``None``.
        """
        return None

    async def lyric(self, song: SongInfo) -> str:
        """Return an empty lyric: 酷狗歌词尚未接入。

        Args:
            song: A song returned by :meth:`search`.

        Returns:
            Always an empty string.
        """
        return ""

    async def play_url(self, song: SongInfo) -> str:
        """Resolve a playable audio URL through the login-free CDN endpoint.

        酷狗旧版 CDN 只校验 ``md5(hash + kgcloudv2)`` 签名，既不需要登录态也不需要
        设备指纹，付费曲目同样按 128kbps 下发试听。

        Args:
            song: A song returned by :meth:`search`.

        Returns:
            A downloadable URL, or an empty string when the CDN refuses the track.

        Raises:
            MusicRequestError: The platform request failed.
        """
        file_hash = song.song_id.lower()
        key = hashlib.md5(f"{file_hash}{CDN_SALT}".encode()).hexdigest()
        payload = await get_json(
            CDN_URL,
            params={
                "key": key,
                "hash": file_hash,
                "br": "hq",
                "appid": 1005,
                "pid": 2,
                "cmd": 25,
                "behavior": "play",
            },
        )
        for raw in get_list(to_obj(payload), "url"):
            if isinstance(raw, str) and raw:
                return raw
        return ""
