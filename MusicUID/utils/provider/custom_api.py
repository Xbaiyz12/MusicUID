"""自建音源微服务与第三方 API 适配器（方案五实现）。"""

from __future__ import annotations

import json
import socket
import asyncio
import ipaddress
from typing import Final
from urllib.parse import quote, urlparse

import httpx

from gsuid_core.logger import logger

from .base import SongInfo
from ..http import get_client
from ...musicuid_config import music_config

_DEFAULT_TIMEOUT_SEC = 8.0
_PROBE_TIMEOUT_SEC = 6.0

# 模板地址支持的占位符；出现表外的花括号说明模板写错，端点必然失败
_TEMPLATE_KEYS: Final[tuple[str, ...]] = (
    "song_id",
    "songmid",
    "id",
    "platform",
    "name",
    "artist",
    "quality",
)

# 只认这些字段名，避免把封面/头像/分享页当音频直链
_URL_KEYS = ("url", "play_url", "playUrl", "src", "musicUrl", "download_url", "purl")
_NESTED_KEYS = ("data", "body", "req", "song", "result", "songs", "list")


def _build_auth_headers(token: str) -> dict[str, str]:
    """构造携带认证信息的请求头字典。"""
    headers = {
        "User-Agent": "MusicUID-CustomResolver/1.0",
        "Accept": "application/json, audio/*, */*",
    }
    cleaned = token.strip()
    if cleaned:
        if cleaned.lower().startswith("bearer "):
            headers["Authorization"] = cleaned
        else:
            headers["Authorization"] = f"Bearer {cleaned}"
            headers["X-API-Key"] = cleaned
            headers["Token"] = cleaned
    return headers


def _looks_like_http_url(value: str) -> bool:
    """判断字符串是否是 http(s) 链接（只做格式判断，不解 DNS）。"""
    trimmed = value.strip()
    parsed = urlparse(trimmed)
    return parsed.scheme in ("http", "https") and bool(parsed.hostname)


async def _points_to_private_host(url: str) -> bool:
    """判断直链是否指向内网 / 环回 / 链路本地地址。

    自建音源服务的响应属于不可信输入，直接下载会让 core 变成内网探测器，
    所以解析 DNS 后逐个地址校验；解析不出来时按不安全处理。
    """
    host = urlparse(url).hostname
    if not host:
        return True
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except OSError:
        return True
    for info in infos:
        address = info[4][0]
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            return True
        if parsed.is_private or parsed.is_loopback or parsed.is_link_local or parsed.is_reserved or parsed.is_multicast:
            return True
    return False


def _extract_url_from_json(data: object) -> str:
    """从 JSON 响应里按已知字段名提取音频直链。"""
    if isinstance(data, str):
        return data.strip() if _looks_like_http_url(data) else ""

    if isinstance(data, list):
        for item in data:
            found = _extract_url_from_json(item)
            if found:
                return found
        return ""

    if isinstance(data, dict):
        for key in _URL_KEYS:
            value = data.get(key)
            if isinstance(value, str) and _looks_like_http_url(value):
                return value.strip()

        for key in _NESTED_KEYS:
            nested = data.get(key)
            if isinstance(nested, (dict, list)):
                found = _extract_url_from_json(nested)
                if found:
                    return found

    return ""


def _format_target_urls(base_url: str, song: SongInfo) -> list[str]:
    """根据 base_url 或占位符模板生成待尝试的请求端点列表。"""
    cleaned = base_url.strip()
    if not cleaned:
        return []

    # 含占位符时按模板渲染：值必须转义，否则歌名里的 & # 会改写查询串
    if "{" in cleaned and "}" in cleaned:
        values: dict[str, str] = {
            "song_id": song.song_id,
            "songmid": song.song_id,
            "id": song.song_id,
            "platform": song.platform,
            "name": song.name,
            "artist": song.singers.split("/")[0] if song.singers else "",
            "quality": "320",
        }
        rendered = cleaned
        for key in _TEMPLATE_KEYS:
            rendered = rendered.replace(f"{{{key}}}", quote(values[key], safe=""))
        if "{" in rendered or "}" in rendered:
            logger.warning(
                f"[MusicUID] 自建音源地址含未识别的占位符，支持：{'、'.join(_TEMPLATE_KEYS)}；实际地址：{rendered}"
            )
            return []
        return [rendered]

    root = cleaned.rstrip("/")
    # 未带占位符时按主流开源微服务路由规则组合候选列表
    if song.platform == "qq":
        return [
            f"{root}/song/url?id={song.song_id}",
            f"{root}/song/url?id={song.song_id}&type=320",
            f"{root}/api/qq/url?id={song.song_id}",
            f"{root}/url?platform=qq&id={song.song_id}",
        ]
    if song.platform == "netease":
        return [
            f"{root}/song/url?id={song.song_id}",
            f"{root}/song/url?id={song.song_id}&br=320000",
            f"{root}/api/netease/url?id={song.song_id}",
            f"{root}/url?platform=netease&id={song.song_id}",
        ]
    return [
        f"{root}/song/url?id={song.song_id}&platform={song.platform}",
        f"{root}/url?platform={song.platform}&id={song.song_id}",
    ]


async def resolve_custom_api(song: SongInfo) -> str:
    """通过配置的自建/第三方音源微服务获取音频真实播放直链。"""
    custom_url = music_config.get_config("custom_api_url").data
    if not custom_url or not custom_url.strip():
        return ""

    headers = _build_auth_headers(music_config.get_config("custom_api_token").data)

    try:
        targets = _format_target_urls(custom_url, song)
    except Exception as e:  # noqa: BLE001 - 模板来自用户配置，不能让点歌整体失败
        logger.warning(f"[MusicUID] 解析自建音源地址失败：{e}")
        return ""

    client = get_client()
    for url in targets:
        try:
            # stream 模式只为拿最终 URL，避免把整首歌读进内存
            async with client.stream("GET", url, headers=headers, timeout=_DEFAULT_TIMEOUT_SEC) as resp:
                if resp.status_code != 200:
                    continue
                content_type = resp.headers.get("Content-Type", "").lower()
                if "audio/" in content_type:
                    return str(resp.url)
                body = await resp.aread()
        except (httpx.HTTPError, OSError) as e:
            logger.debug(f"[MusicUID] 请求自建音源端点 {url} 失败: {e}")
            continue

        try:
            res_json = json.loads(body)
        except json.JSONDecodeError:
            continue
        play_url = _extract_url_from_json(res_json)
        if not play_url:
            continue
        if await _points_to_private_host(play_url):
            logger.warning("[MusicUID] 自建音源返回了指向内网/环回地址的直链，已忽略")
            continue
        logger.info(f"[MusicUID] 自建音源服务成功解析 {song.platform.upper()} 歌曲 《{song.name}》直链")
        return play_url

    return ""


async def test_custom_api_connection(api_url: str, token: str = "") -> tuple[bool, str]:
    """测试指定自建 API 地址的连通性与健康状态。"""
    cleaned = api_url.strip()
    if not cleaned:
        return False, "API 地址不能为空"

    headers = _build_auth_headers(token)
    probe_url = cleaned if ("{" in cleaned and "}" in cleaned) else f"{cleaned.rstrip('/')}/health"

    try:
        resp = await get_client().get(probe_url, headers=headers, timeout=_PROBE_TIMEOUT_SEC)
    except httpx.HTTPError as e:
        return False, f"网络连接失败: {e}"
    if resp.status_code in (200, 400, 404):
        return True, f"服务响应正常（HTTP {resp.status_code}）"
    return False, f"服务返回异常状态码: HTTP {resp.status_code}"
