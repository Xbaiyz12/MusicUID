"""自建音源微服务与第三方 API 适配器（方案五实现）。"""

from __future__ import annotations

import json
from typing import Mapping

import httpx
from gsuid_core.logger import logger

from .base import SongInfo
from ...musicuid_config import music_config

_DEFAULT_TIMEOUT_SEC = 8.0


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


def _extract_url_from_json(data: object) -> str:
    """递归或模式匹配从 JSON 数据中提取音频流直链。"""
    if isinstance(data, str):
        trimmed = data.strip()
        if trimmed.startswith(("http://", "https://")):
            return trimmed
        return ""

    if isinstance(data, list):
        for item in data:
            found = _extract_url_from_json(item)
            if found:
                return found
        return ""

    if isinstance(data, dict):
        # 兼容 Netease / QQMusicApi 常见顶级字段
        keys_to_check = [
            "url",
            "play_url",
            "playUrl",
            "src",
            "musicUrl",
            "download_url",
            "purl",
            "data",
        ]
        for key in keys_to_check:
            val = data.get(key)
            if isinstance(val, str) and val.strip().startswith(("http://", "https://")):
                return val.strip()

        # 检查嵌套字典与列表（如 data.url 或 data: [...]）
        for key in ("data", "body", "req", "song", "result"):
            nested = data.get(key)
            if isinstance(nested, (dict, list)):
                found = _extract_url_from_json(nested)
                if found:
                    return found

        # 遍历所有值尝试寻找有效音频链接
        for v in data.values():
            if isinstance(v, (dict, list)):
                found = _extract_url_from_json(v)
                if found:
                    return found

    return ""


def _format_target_urls(base_url: str, song: SongInfo) -> list[str]:
    """根据 base_url 或占位符模板生成待尝试的请求端点列表。"""
    cleaned = base_url.strip()
    if not cleaned:
        return []

    # 包含占位符时直接按模板渲染
    if "{" in cleaned and "}" in cleaned:
        mapping: Mapping[str, str] = {
            "song_id": song.song_id,
            "songmid": song.song_id,
            "id": song.song_id,
            "platform": song.platform,
            "name": song.name,
            "artist": song.artists[0] if song.artists else "",
            "quality": "320",
        }
        rendered = cleaned
        for k, v in mapping.items():
            rendered = rendered.replace(f"{{{k}}}", v)
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

    token = music_config.get_config("custom_api_token").data
    headers = _build_auth_headers(token)
    targets = _format_target_urls(custom_url, song)

    try:
        async with httpx.AsyncClient(
            headers=headers, timeout=_DEFAULT_TIMEOUT_SEC, follow_redirects=True
        ) as client:
            for url in targets:
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        continue

                    # 若直接重定向至音频流或返回音频二进制
                    content_type = resp.headers.get("Content-Type", "").lower()
                    if "audio/" in content_type:
                        return str(resp.url)

                    # 尝试按 JSON 解析提取
                    res_json = resp.json()
                    play_url = _extract_url_from_json(res_json)
                    if play_url:
                        logger.info(
                            f"[MusicUID] 自建音源服务成功解析 {song.platform.upper()} 歌曲 "
                            f"《{song.name}》直链"
                        )
                        return play_url
                except (httpx.HTTPError, json.JSONDecodeError) as e:
                    logger.debug(f"[MusicUID] 请求自建音源端点 {url} 失败: {e}")
                    continue
    except Exception as e:
        logger.warning(f"[MusicUID] 请求自建音源服务异常: {e}")

    return ""


async def test_custom_api_connection(
    api_url: str, token: str = ""
) -> tuple[bool, str]:
    """测试指定自建 API 地址的连通性与健康状态。"""
    cleaned = api_url.strip()
    if not cleaned:
        return False, "API 地址不能为空"

    headers = _build_auth_headers(token)
    root = cleaned.rstrip("/")
    probe_url = (
        root
        if ("{" in cleaned and "}" in cleaned)
        else (f"{root}/health" if not root.endswith("/") else root)
    )

    try:
        async with httpx.AsyncClient(
            headers=headers, timeout=6.0, follow_redirects=True
        ) as client:
            resp = await client.get(probe_url)
            if resp.status_code in (200, 404, 400):
                return True, f"服务响应正常（HTTP {resp.status_code}）"
            return False, f"服务返回异常状态码: HTTP {resp.status_code}"
    except httpx.HTTPError as e:
        return False, f"网络连接失败: {e}"
