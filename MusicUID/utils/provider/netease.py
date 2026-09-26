"""Netease Cloud Music provider: direct public endpoints, no API service needed."""

from typing import Final
from dataclasses import replace

from gsuid_core.logger import logger

from .base import SongInfo, SongCollection
from .custom_api import resolve_custom_api
from ..http import MusicRequestError, get_json, post_json, get_location
from ..json_tools import get_id, to_obj, get_int, get_obj, get_str, to_list, get_list, join_names
from ...musicuid_config import music_config

PLATFORM: Final[str] = "netease"

SEARCH_URL: Final[str] = "https://music.163.com/api/search/get/web"
DETAIL_URL: Final[str] = "https://music.163.com/api/song/detail"
LYRIC_URL: Final[str] = "https://music.163.com/api/song/lyric"
OUTER_URL: Final[str] = "https://music.163.com/song/media/outer/url"
PLAYLIST_URL: Final[str] = "https://music.163.com/api/v6/playlist/detail"
ALBUM_URL: Final[str] = "https://music.163.com/api/album"
WEAPI_PLAY_URL: Final[str] = "https://music.163.com/weapi/song/enhance/player/url/v1"

# 可选档位（由配置项 netease_level 选择），standard 始终作为兜底
QUALITY_LEVELS: Final[tuple[str, ...]] = ("standard", "exhigh", "lossless")

# 网易 web 接口对无 Referer 的请求可能返回 403
HEADERS: Final[dict[str, str]] = {"Referer": "https://music.163.com/"}

# fee 字段：1 = VIP，4 = 需购买专辑；0 / 8 可免费播放
PAID_FEES: Final[frozenset[int]] = frozenset({1, 4})

# 歌单动辄上千首，只取前面一段（卡片本身也只列 max_list 首）
PLAYLIST_LIMIT: Final[int] = 30

# 歌单/专辑链接的类型标记，与 resolve 模块共用
KIND_PLAYLIST: Final[str] = "playlist"
KIND_ALBUM: Final[str] = "album"


def parse_song(item: dict[str, object]) -> SongInfo | None:
    """Build a song from one API item.

    搜索、详情、专辑接口返回全名字段（artists / album / duration），而歌单接口
    返回简写字段（ar / al / dt），两套字段名都要认。

    Args:
        item: One element of a songs or tracks array.

    Returns:
        The parsed song, or ``None`` when the item carries no id.
    """
    song_id = get_id(item, "id")
    if not song_id:
        return None
    artists = item["artists"] if "artists" in item else item.get("ar")
    album = get_obj(item, "album") if "album" in item else get_obj(item, "al")
    duration_ms = get_int(item, "duration") or get_int(item, "dt")
    return SongInfo(
        platform=PLATFORM,
        song_id=song_id,
        name=get_str(item, "name", "未知歌曲"),
        singers=join_names(artists) or "未知歌手",
        album=get_str(album, "name"),
        duration_sec=duration_ms // 1000,
        cover_url=get_str(album, "picUrl"),
        payplay=get_int(item, "fee") in PAID_FEES,
    )


def parse_songs(value: object) -> list[SongInfo]:
    """Parse an array of song items, dropping entries without an id.

    Args:
        value: Raw array from a platform payload.

    Returns:
        The parsed songs, in payload order.
    """
    parsed = (parse_song(to_obj(raw)) for raw in to_list(value))
    return [song for song in parsed if song is not None]


class NeteaseProvider:
    """网易云音乐：公开 web 接口直连，支持搜索、歌词、专辑/歌单与外链播放。"""

    platform = PLATFORM
    display_name = "网易云音乐"
    supports_play = True

    async def search(self, keyword: str, limit: int) -> list[SongInfo]:
        """Search songs through the public web search endpoint.

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
            params={"s": keyword, "type": 1, "offset": 0, "limit": limit, "total": "true"},
            headers=HEADERS,
        )
        result = get_obj(to_obj(payload), "result")
        return await self._fill_covers(parse_songs(result.get("songs")))

    async def _fill_covers(self, songs: list[SongInfo]) -> list[SongInfo]:
        """Fill in cover URLs that the search payload does not carry.

        搜索接口的 album 只返回 picId，封面需要批量详情接口补齐。

        Args:
            songs: Songs parsed from the search response.

        Returns:
            The same songs, with covers added where the detail API has them.
        """
        if not songs:
            return songs
        ids = ",".join(song.song_id for song in songs)
        payload = await get_json(DETAIL_URL, params={"ids": f"[{ids}]"}, headers=HEADERS)
        covers: dict[str, str] = {}
        for raw in get_list(to_obj(payload), "songs"):
            item = to_obj(raw)
            song_id = get_id(item, "id")
            cover = get_str(get_obj(item, "album"), "picUrl")
            if song_id and cover:
                covers[song_id] = cover
        return [
            replace(song, cover_url=covers[song.song_id] if song.song_id in covers else song.cover_url)
            for song in songs
        ]

    async def detail(self, song_id: str) -> SongInfo | None:
        """Fetch one song by id through the public detail endpoint.

        Args:
            song_id: Netease song id taken from a share link.

        Returns:
            The song, or ``None`` when the id cannot be resolved.

        Raises:
            MusicRequestError: The platform request failed.
        """
        payload = await get_json(DETAIL_URL, params={"ids": f"[{song_id}]"}, headers=HEADERS)
        for raw in get_list(to_obj(payload), "songs"):
            song = parse_song(to_obj(raw))
            if song is not None:
                return song
        return None

    async def collection(self, kind: str, collection_id: str) -> SongCollection | None:
        """Fetch a playlist or album together with its songs.

        Args:
            kind: ``playlist`` or ``album``.
            collection_id: Netease playlist / album id from a share link.

        Returns:
            The collection, or ``None`` for an unknown kind or empty response.

        Raises:
            MusicRequestError: The platform request failed.
        """
        if kind == KIND_PLAYLIST:
            payload = await get_json(
                PLAYLIST_URL,
                params={"id": collection_id, "n": PLAYLIST_LIMIT, "s": 0},
                headers=HEADERS,
            )
            node = get_obj(to_obj(payload), "playlist")
            return self._build_collection(node, "tracks")
        if kind == KIND_ALBUM:
            payload = await get_json(f"{ALBUM_URL}/{collection_id}", headers=HEADERS)
            node = get_obj(to_obj(payload), "album")
            return self._build_collection(node, "songs")
        return None

    def _build_collection(self, node: dict[str, object], songs_key: str) -> SongCollection | None:
        """Assemble a collection from a playlist / album object.

        Args:
            node: The ``playlist`` or ``album`` object.
            songs_key: Name of the member array inside ``node``.

        Returns:
            The collection, or ``None`` when the payload holds nothing useful.
        """
        name = get_str(node, "name")
        songs = parse_songs(node.get(songs_key))
        if not name and not songs:
            return None
        return SongCollection(name=name or "未知歌单", songs=songs)

    async def lyric(self, song: SongInfo) -> str:
        """Fetch the plain-text lyric of a song.

        Args:
            song: A song returned by :meth:`search`.

        Returns:
            Lyric text, or an empty string when the platform has none.

        Raises:
            MusicRequestError: The platform request failed.
        """
        payload = await get_json(
            LYRIC_URL,
            params={"id": song.song_id, "lv": 1, "kv": 1, "tv": -1},
            headers=HEADERS,
        )
        return get_str(get_obj(to_obj(payload), "lrc"), "lyric")

    async def play_url(self, song: SongInfo) -> str:
        """Resolve a playable audio URL, preferring the weapi player endpoint.

        weapi 会带上登录态，VIP 歌曲与 320kbps 高档位只有它能拿到；拿不到时回退
        公开外链（128kbps，仅免费曲目）。

        Args:
            song: A song returned by :meth:`search`.

        Returns:
            A downloadable URL, or an empty string when nothing is playable.

        Raises:
            MusicRequestError: The fallback request failed.
        """
        priority = music_config.get_config("custom_api_priority").data
        if priority == "custom_first":
            custom_res = await resolve_custom_api(song)
            if custom_res:
                return custom_res

        url = await self._weapi_play_url(song)
        if url:
            return url
        target = f"{OUTER_URL}?id={song.song_id}.mp3"
        location = await get_location(target)
        # 可播放响应有两种：302 跳到 CDN，或直接 200 返回音频流（此时没有 Location）。
        # 所以只有明确跳到 404 页才算不可播放，其余交给下载阶段判断。
        if "404" not in location:
            return target

        if priority != "custom_first":
            custom_res = await resolve_custom_api(song)
            if custom_res:
                logger.info(
                    f"[MusicUID] 网易云官方未下发《{song.name}》，触发自建 API 兜底取流"
                )
                return custom_res

        return ""

    async def _weapi_play_url(self, song: SongInfo) -> str:
        """Ask the weapi player endpoint for an audio URL.

        逐级降档尝试；加密依赖缺失或请求失败都返回空串，由公开外链兜底。

        Args:
            song: A song returned by :meth:`search`.

        Returns:
            The audio URL, or an empty string when weapi cannot provide one.
        """
        try:
            from Crypto.Cipher import AES  # noqa: F401

            from .netease_crypto import weapi_form, weapi_cookie
        except ImportError as e:
            logger.debug(f"[MusicUID] weapi 不可用（缺 pycryptodome）：{e}")
            return ""
        headers = dict(HEADERS)
        cookie = weapi_cookie(music_config.get_config("netease_cookie").data)
        if cookie:
            headers["Cookie"] = cookie
        preferred = music_config.get_config("netease_level").data
        # 优先用户选的档位，拿不到再落到标准档
        levels = (preferred, "standard") if preferred in QUALITY_LEVELS and preferred != "standard" else ("standard",)
        for level in levels:
            payload: dict[str, object] = {
                "ids": f"[{song.song_id}]",
                "level": level,
                "encodeType": "mp3",
                "csrf_token": "",
            }
            try:
                data = await post_json(WEAPI_PLAY_URL, weapi_form(payload), headers=headers)
            except (MusicRequestError, ImportError, ValueError, Exception) as e:
                logger.debug(f"[MusicUID] weapi 取流失败（{level}）：{e}")
                continue
            for raw in get_list(to_obj(data), "data"):
                url = get_str(to_obj(raw), "url")
                if url:
                    logger.debug(f"[MusicUID] weapi 命中 {level}：{song.name}")
                    return url
        return ""
