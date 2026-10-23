"""Kugou Music provider: public search plus two url paths (anonymous / logged in)."""

import re
import time
import random
import asyncio
import hashlib
from typing import Final
from dataclasses import replace

from gsuid_core.logger import logger

from .base import SongInfo, SongCollection
from ..http import get_json
from ..accounts import iter_cookies, reset_failure, record_failure
from .custom_api import resolve_custom_api
from ..json_tools import to_obj, get_int, get_obj, get_str, get_list

# 仅 mobiles.kugou.com 的证书与域名匹配，mobilecdn / msearchcdn 均报 Hostname mismatch
SEARCH_URL: Final[str] = "https://mobiles.kugou.com/api/v3/search/song"

# 分享链接解析：这个老接口接受裸 hash，返回歌名/歌手与实际可用的 hash
DETAIL_URL: Final[str] = "https://m.kugou.com/app/i/getSongInfo.php"
HEADERS: Final[dict[str, str]] = {"Referer": "https://m.kugou.com/"}

# 匿名取流：旧版 CDN 只校验 md5(hash + 固定盐) 签名，不需要登录态与设备指纹
CDN_URL: Final[str] = "https://trackercdn.kugou.com/i/v2/"
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

# 搜索结果补封面时的并发与耗时上限：避免为了一张封面把整次点歌拖慢，
# 也避免对上游 detail 接口短时间打出太多请求。缓存按专辑名复用结果。
_COVER_FILL_CONCURRENCY: Final[int] = 4
_COVER_FILL_TIMEOUT_S: Final[float] = 3.0
_COVER_FILL_TOTAL_TIMEOUT_S: Final[float] = 5.0
_COVER_CACHE_MAX: Final[int] = 512
_cover_cache: dict[str, str] = {}
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
                    # privilege > 0 表示需要会员或单独购买专辑（周杰伦、烟火这类原唱）
                    payplay=get_int(item, "privilege") > 0,
                )
            )
        return await self._fill_covers(songs)

    async def _fill_covers(self, songs: list[SongInfo]) -> list[SongInfo]:
        """为搜索结果补齐专辑封面。

        v3 搜索接口本身不返回封面字段，而 imge.kugou.com/stdmusic/{album_id}.jpg
        这类拼 URL 的规律现在只会命中默认占位图；唯一的稳定来源是 detail 接口
        （getSongInfo.php）响应里的 ``album_img``。这里按专辑名去重后并发请求，
        同专辑结果共用一张封面，避免每次点歌都放大请求数。为控制对上游的压力与
        自身延迟，并发放到 ``_COVER_FILL_CONCURRENCY`` 个、单请求限时
        ``_COVER_FILL_TIMEOUT_S`` 秒、整批限时 ``_COVER_FILL_TOTAL_TIMEOUT_S`` 秒，
        并按专辑名缓存结果供后续复用。

        Args:
            songs: 刚从搜索接口构造出的结果列表（SongInfo 为冻结数据类）。

        Returns:
            封面补齐后的新列表；``SongInfo`` 均不可变，未命中封面的项原样保留。
        """
        probes: dict[str, SongInfo] = {}
        for song in songs:
            if song.cover_url or not song.album or song.album in probes:
                continue
            if song.album in _cover_cache:
                continue
            probes[song.album] = song

        if probes:
            sem = asyncio.Semaphore(_COVER_FILL_CONCURRENCY)

            async def fetch(album: str, probe: SongInfo) -> None:
                async with sem:
                    try:
                        detail = await asyncio.wait_for(self.detail(probe.song_id), timeout=_COVER_FILL_TIMEOUT_S)
                    except Exception:  # noqa: BLE001 - 封面补齐失败不应影响搜索结果
                        return
                    if detail and detail.cover_url:
                        _cover_cache[album] = detail.cover_url

            try:
                await asyncio.wait_for(
                    asyncio.gather(*(fetch(album, probe) for album, probe in probes.items())),
                    timeout=_COVER_FILL_TOTAL_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                pass
            # 简单的容量截断，避免缓存无限增长
            if len(_cover_cache) > _COVER_CACHE_MAX:
                for stale in list(_cover_cache)[: len(_cover_cache) - _COVER_CACHE_MAX]:
                    _cover_cache.pop(stale, None)

        def resolve(song: SongInfo) -> SongInfo:
            if song.cover_url:
                return song
            cached = _cover_cache.get(song.album, "")
            return replace(song, cover_url=cached) if cached else song

        return [resolve(song) for song in songs]

    async def detail(self, song_id: str) -> SongInfo | None:
        """Fetch one song by its file hash.

        分享链接直接带 ``hash``，但服务端可能把它换成另一个等价 hash：``req_hash`` 是请求
        值，``hash`` 才是实际可用值，取流必须用返回的那个。这个老接口不返回专辑名，时长也
        恒为 0，所以这两项留空。

        Args:
            song_id: 酷狗 song hash taken from the share link.

        Returns:
            The song, or ``None`` when the hash cannot be resolved.

        Raises:
            MusicRequestError: The platform request failed.
        """
        payload = await get_json(
            DETAIL_URL,
            params={"cmd": "playInfo", "hash": song_id},
            headers=HEADERS,
        )
        item = to_obj(payload)
        name = get_str(item, "songName")
        if not name:
            return None
        return SongInfo(
            platform=self.platform,
            song_id=get_str(item, "hash") or song_id,
            name=name,
            singers=get_str(item, "author_name") or "未知歌手",
            duration_sec=get_int(item, "timeLength"),
            cover_url=get_str(item, "album_img").replace("{size}", "480"),
            payplay=get_int(item, "privilege") > 0,
        )

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
        网关拒绝时回退旧版 CDN，只对免费曲目有效；两条链路都没结果时再交给自建音源兜底。

        Args:
            song: A song returned by :meth:`search`.

        Returns:
            A downloadable URL, or an empty string when every path refuses.

        Raises:
            MusicRequestError: The platform request failed.
        """
        # 逐号重试：某个号失效就换下一个，全部失败再退回免登录链路
        for cookie in iter_cookies("kugou"):
            url, credential_problem = await self._logged_play_url(song, cookie)
            if url:
                reset_failure("kugou", cookie)
                return url
            # 只有明确是凭据问题（如 Cookie 缺 t / KugooID）才记失败：网关对
            # 「未购买专辑」这类歌曲级拒绝同样返回空 url，若一并按账号失败记账，
            # 连点几首受限歌就会把健康账号逐个挂起。
            if credential_problem:
                record_failure("kugou", cookie)
        url = await self._anonymous_play_url(song)
        if url:
            return url
        # 与网易云 / QQ 保持一致：官方链路都没下发时交给自建音源兜底
        return await resolve_custom_api(song)

    async def _logged_play_url(self, song: SongInfo, cookie: str) -> tuple[str, bool]:
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
            ``(url, credential_problem)``：``url`` 为空表示网关未下发；
            ``credential_problem`` 为 ``True`` 仅当失败可明确归因于凭据本身
            （缺少 ``t`` / ``KugooID``），而不是歌曲级拒绝。
        """
        token = re.search(r"(?:^|;\s*)t=([^;]*)", cookie)
        userid = re.search(r"(?:^|;\s*)KugooID=([^;]*)", cookie)
        if token is None or userid is None:
            logger.warning("[MusicUID] 酷狗 Cookie 缺少 t / KugooID，已回退免登录取流")
            return "", True
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
                return raw, False
        # 20018 = 未购买该专辑（只给试听），20028 = 触发人机验证；两者都回退免登录 CDN
        logger.debug(f"[MusicUID] 酷狗网关未下发 {song.name}：status={body.get('status')} err={body.get('error_code')}")
        return "", False

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
