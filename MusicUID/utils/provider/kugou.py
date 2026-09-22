"""Kugou Music provider: public search plus two url paths (anonymous / logged in)."""

import re
import time
import random
import hashlib
from typing import Final

from gsuid_core.logger import logger

from .base import SongInfo, SongCollection
from ..http import get_json
from ..json_tools import to_obj, get_int, get_obj, get_str, get_list
from ...musicuid_config import music_config

# 仅 mobiles.kugou.com 的证书与域名匹配，mobilecdn / msearchcdn 均报 Hostname mismatch
SEARCH_URL: Final[str] = "https://mobiles.kugou.com/api/v3/search/song"

# 匿名取流：旧版 CDN 只校验 md5(hash + 固定盐) 签名，不需要登录态与设备指纹
CDN_URL: Final[str] = "http://trackercdn.kugou.com/i/v2/"
CDN_SALT: Final[str] = "kgcloudv2"

# 登录取流：网关 v5/url 不校验 signature，key 由登录态参与派生
GATEWAY_URL: Final[str] = "https://gateway.kugou.com/v5/url"
GATEWAY_ROUTER: Final[str] = "trackercdn.kugou.com"
KEY_SALT: Final[str] = "57ae12eb6890223e355ccfcb74edf70d"
SIGN_SALT: Final[str] = "OIlwieks28dk2k092lksi2UIkp"
APP_ID: Final[int] = 1005
CLIENT_VER: Final[int] = 11430
PAGE_ID: Final[int] = 151369488
PPAGE_ID: Final[str] = "463467626,350369493,788954147"
# mid 只要求是稳定的十进制大整数串，与登录态无关；dfid 每次随机即可
MID_SEED: Final[str] = "MusicUID"
DFID_CHARS: Final[str] = "1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZ"
# 网关按安卓客户端校验请求头：dfid / mid / clienttime 必须与 URL 参数一致
ANDROID_UA: Final[str] = "Android15-1070-11083-46-0-DiscoveryDRADProtocol-wifi"
EXTRA_HEADERS: Final[dict[str, str]] = {
    "kg-rc": "1",
    "kg-thash": "5d816a0",
    "kg-rec": "1",
    "kg-rf": "B9EDA08A64250DEFFBCADDEE00F8F25F",
}


class KugouProvider:
    """酷狗音乐：公开搜索接口 + 免登录 / 登录两条取流链路。"""

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
        """Resolve a playable audio URL, preferring the logged-in gateway.

        填入 ``kugou_cookie`` 后先走网关 ``v5/url``：免费曲目返回完整版，需要单独购买
        专辑的曲目（周杰伦等原唱）返回 60 秒试听片段。没有 Cookie、Cookie 不完整或
        网关拒绝时回退旧版 CDN，只对免费曲目有效。

        Args:
            song: A song returned by :meth:`search`.

        Returns:
            A downloadable URL, or an empty string when both paths refuse.

        Raises:
            MusicRequestError: The platform request failed.
        """
        cookie = music_config.get_config("kugou_cookie").data
        if cookie:
            url = await self._logged_play_url(song, cookie)
            if url:
                return url
        return await self._anonymous_play_url(song)

    async def _logged_play_url(self, song: SongInfo, cookie: str) -> str:
        """Ask the gateway for a url with the account login state attached.

        网关要两样东西：``key`` = md5(hash + 盐 + appid + mid + userid)，以及
        ``signature`` = md5(盐 + 按 key 排序的 k=v 拼接 + 盐)。注意上游 KuGouMusicApi
        写的是 ``notSign``，而它的 request.js 判断的是 ``notSignature``，拼写不一致导致
        签名其实仍会生成——照字面省掉签名会被服务端以 ``err signature`` 拒绝。
        ``free_part`` 固定为 0，让服务端自行决定下发完整版还是试听片段（传 1 会把免费
        曲目也截断成试听）。

        Args:
            song: A song returned by :meth:`search`.
            cookie: Raw KuGou cookie string copied from the browser.

        Returns:
            The audio URL, or an empty string when the gateway refuses.
        """
        token = re.search(r"(?:^|;\s*)t=([^;]*)", cookie)
        userid = re.search(r"(?:^|;\s*)KugooID=([^;]*)", cookie)
        if token is None or userid is None:
            logger.warning("[MusicUID] 酷狗 Cookie 缺少 t / KugooID，已回退免登录取流")
            return ""
        file_hash = song.song_id.lower()
        # 设备指纹必须与 Cookie 里的登录态解耦：直接沿用 Cookie 的 mid / dfid 会被服务端
        # 判 20018，用固定派生的 mid + 每次随机的 dfid 才与上游客户端行为一致
        mid = str(int(hashlib.md5(MID_SEED.encode()).hexdigest(), 16))
        dfid = "".join(random.choice(DFID_CHARS) for _ in range(24))
        key = hashlib.md5(f"{file_hash}{KEY_SALT}{APP_ID}{mid}{userid.group(1)}".encode()).hexdigest()
        clienttime = int(time.time())
        params: dict[str, str | int] = {
            "album_id": 0,
            "area_code": 1,
            "hash": file_hash,
            "ssa_flag": "is_fromtrack",
            "version": CLIENT_VER,
            "page_id": PAGE_ID,
            "quality": 128,
            "album_audio_id": 0,
            "behavior": "play",
            "pid": 2,
            "cmd": 26,
            "pidversion": 3001,
            "IsFreePart": 0,
            "ppage_id": PPAGE_ID,
            "cdnBackup": 1,
            "module": "",
            "clientver": CLIENT_VER,
            "dfid": dfid,
            "mid": mid,
            "uuid": "-",
            "appid": APP_ID,
            "clienttime": clienttime,
            "token": token.group(1),
            "userid": userid.group(1),
            "key": key,
        }
        # 签名覆盖除 signature 自身以外的全部参数，拼接顺序按参数名排序
        digest = "".join(f"{name}={params[name]}" for name in sorted(params))
        params["signature"] = hashlib.md5(f"{SIGN_SALT}{digest}{SIGN_SALT}".encode()).hexdigest()
        payload = await get_json(
            GATEWAY_URL,
            params=params,
            headers={
                "User-Agent": ANDROID_UA,
                "x-router": GATEWAY_ROUTER,
                "dfid": dfid,
                "mid": mid,
                "clienttime": str(clienttime),
                "Cookie": cookie,
                **EXTRA_HEADERS,
            },
        )
        body = to_obj(payload)
        for raw in get_list(body, "url"):
            if isinstance(raw, str) and raw:
                return raw
        # 20018 = 未购买该专辑（只给试听），20028 = 触发人机验证；两者都回退免登录 CDN
        logger.debug(f"[MusicUID] 酷狗网关未下发 {song.name}：status={body.get('status')} err={body.get('error_code')}")
        return ""

    async def _anonymous_play_url(self, song: SongInfo) -> str:
        """Resolve a url through the login-free legacy CDN.

        只校验 ``md5(hash + kgcloudv2)`` 签名；hash 必须转小写，否则签名对不上。

        Args:
            song: A song returned by :meth:`search`.

        Returns:
            The audio URL, or an empty string when the CDN refuses the track.
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
