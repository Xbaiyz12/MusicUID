"""Shared async HTTP client for the music platforms."""

import socket
import asyncio
import ipaddress
from typing import Final
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse
from collections.abc import Mapping

import httpx

USER_AGENT: Final[str] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT: Final[float] = 20.0

# 判定「内网 / 环回 / 链路本地」用的显式网段。刻意不用 ``ip.is_private``：
# 它会把 RFC 2544 的 198.18.0.0/15（本地代理 / VPN 常用的 fake-IP 段）也算进去，
# 开着代理的机器上所有外部域名都解析到该段，会被整片误判成内网、下载全部被拒。
_BLOCKED_NETWORKS: Final[tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]] = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)

# 下载不可信直链时手动跟随重定向的最大跳数（每跳都会重新校验主机）
_MAX_REDIRECTS: Final[int] = 3

# 查询参数名里出现这些片段就按敏感参数处理（access_token / api_key / sign 等都要盖住）
_SENSITIVE_PARTS: Final[tuple[str, ...]] = (
    "token",
    "cookie",
    "password",
    "secret",
    "sign",
    "auth",
    "key",
    "userid",
    "session",
)

_client: httpx.AsyncClient | None = None


class MusicRequestError(Exception):
    """Raised when a music platform request cannot be completed."""


def get_client() -> httpx.AsyncClient:
    """Return the process-wide async HTTP client, creating it on first use.

    Returns:
        The shared client; callers must not close it directly.
    """
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(DEFAULT_TIMEOUT),
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
    return _client


async def close_client() -> None:
    """Close the shared client; called when the plugin is unloaded."""
    global _client
    client, _client = _client, None
    if client is not None and not client.is_closed:
        await client.aclose()


async def points_to_private_host(url: str) -> bool:
    """判断 URL 是否指向内网 / 环回 / 链路本地地址。

    自建音源与平台返回的直链都属于不可信输入，直接请求会让 core 变成内网探测器，
    所以解析 DNS 后逐个地址校验；解析不出来时按不安全处理。

    Args:
        url: 待检查的绝对 URL。

    Returns:
        ``True`` 表示指向内网 / 不可解析，应拒绝请求。
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
        if any(parsed in net for net in _BLOCKED_NETWORKS):
            return True
    return False


def clean_url_for_log(url: str) -> str:
    """Strip sensitive query params from URL for safe logging/error messages."""
    try:
        parsed = urlparse(url)
        if not parsed.query:
            return url
        params: list[tuple[str, str]] = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            if any(part in key.lower() for part in _SENSITIVE_PARTS):
                params.append((key, "***"))
            else:
                params.append((key, value))
        return urlunparse(parsed._replace(query=urlencode(params)))
    except ValueError:
        return url.split("?")[0]


async def get_json(
    url: str,
    params: dict[str, str | int] | None = None,
    headers: dict[str, str] | None = None,
) -> object:
    """GET a JSON document from a platform endpoint.

    Args:
        url: Absolute endpoint URL.
        params: Optional query parameters.
        headers: Optional per-request headers (e.g. platform Referer).

    Returns:
        The decoded JSON payload, unvalidated.

    Raises:
        MusicRequestError: The transport failed or the body is not JSON.
    """
    try:
        res = await get_client().get(url, params=params, headers=headers)
        res.raise_for_status()
    except httpx.HTTPError as e:
        safe_url = clean_url_for_log(str(e.request.url if hasattr(e, "request") and e.request else url))
        raise MusicRequestError(f"请求失败（{safe_url}）：{type(e).__name__}") from e
    try:
        return res.json()
    except ValueError as e:
        safe_url = clean_url_for_log(url)
        raise MusicRequestError(f"接口返回了非 JSON 数据（{safe_url}）") from e


async def post_json(
    url: str,
    data: Mapping[str, object],
    headers: dict[str, str] | None = None,
    as_json: bool = False,
) -> object:
    """POST a body and decode the JSON response.

    Args:
        url: Absolute endpoint URL.
        data: Form fields, or the whole JSON document when ``as_json`` is set.
        headers: Optional per-request headers (Cookie / Referer).
        as_json: Send ``data`` as a JSON body instead of form encoding.

    Returns:
        The decoded JSON payload, unvalidated.

    Raises:
        MusicRequestError: The transport failed or the body is not JSON.
    """
    try:
        if as_json:
            res = await get_client().post(url, json=dict(data), headers=headers)
        else:
            res = await get_client().post(url, data=dict(data), headers=headers)
        res.raise_for_status()
    except httpx.HTTPError as e:
        safe_url = clean_url_for_log(str(e.request.url if hasattr(e, "request") and e.request else url))
        raise MusicRequestError(f"请求失败（{safe_url}）：{type(e).__name__}") from e
    try:
        return res.json()
    except ValueError as e:
        safe_url = clean_url_for_log(url)
        raise MusicRequestError(f"接口返回了非 JSON 数据（{safe_url}）") from e


async def get_text(
    url: str,
    params: dict[str, str | int] | None = None,
    headers: dict[str, str] | None = None,
) -> str:
    """GET a plain-text document from a platform endpoint.

    Args:
        url: Absolute endpoint URL.
        params: Optional query parameters.
        headers: Optional per-request headers.

    Returns:
        The decoded response body.

    Raises:
        MusicRequestError: The transport failed.
    """
    try:
        res = await get_client().get(url, params=params, headers=headers)
        res.raise_for_status()
    except httpx.HTTPError as e:
        raise MusicRequestError(f"请求失败（{clean_url_for_log(url)}）：{e}") from e
    return res.text


async def get_location(url: str) -> tuple[int, str]:
    """Follow no redirects and report the status code plus the ``Location`` header.

    调用方必须同时看状态码：只看 Location 会把 403/429 之类的前置拒绝
    误判成「拿到了可播放地址」。

    Args:
        url: Absolute endpoint URL that is expected to redirect.

    Returns:
        ``(status_code, location)``; ``location`` is empty when there is none.

    Raises:
        MusicRequestError: The transport failed.
    """
    try:
        res = await get_client().get(url, follow_redirects=False)
    except httpx.HTTPError as e:
        raise MusicRequestError(f"请求失败（{clean_url_for_log(url)}）：{e}") from e
    return res.status_code, res.headers.get("location", "")


async def _get_checked(url: str, timeout: float) -> httpx.Response:
    """GET 一个不可信 URL，手动逐跳跟随重定向，每一跳都校验主机。

    共享 client 默认跟随重定向，若只在入口校验主机，攻击者用 302 指向 127.0.0.1
    即可绕过。这里改为手动跟随，逐跳校验。

    Args:
        url: 待请求的绝对 URL。
        timeout: 单跳超时（秒）。

    Returns:
        最终（未重定向）的响应。

    Raises:
        MusicRequestError: 目标指向内网 / 不可解析，或重定向次数过多。
    """
    client = get_client()
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        if await points_to_private_host(current):
            raise MusicRequestError(f"请求被拒：目标指向内网地址（{clean_url_for_log(current)}）")
        res = await client.get(current, timeout=httpx.Timeout(timeout), follow_redirects=False)
        if not res.is_redirect:
            return res
        location = res.headers.get("location")
        if not location:
            return res
        current = urljoin(current, location)
    raise MusicRequestError(f"请求被拒：重定向次数过多（{clean_url_for_log(url)}）")


async def fetch_bytes_checked(url: str, timeout: float) -> tuple[bytes, str]:
    """下载不可信 URL 的原始字节与内容类型。

    Args:
        url: 待请求的绝对 URL。
        timeout: 单跳超时（秒）。

    Returns:
        ``(响应体, content-type)``；content-type 缺失时回退 ``image/jpeg``。

    Raises:
        MusicRequestError: 目标不可信、传输失败或返回错误状态码。
    """
    try:
        res = await _get_checked(url, timeout)
        res.raise_for_status()
    except httpx.HTTPError as e:
        raise MusicRequestError(f"下载失败（{clean_url_for_log(url)}）：{e}") from e
    mime = res.headers.get("content-type", "").split(";")[0].strip() or "image/jpeg"
    return res.content, mime


async def download(url: str, timeout: float) -> bytes:
    """Download raw bytes (audio files) from a CDN URL.

    这里的 URL 可能来自不可信的自建音源，因此走 :func:`fetch_bytes_checked`：
    手动逐跳跟随重定向并逐跳校验主机，避免被 302 到内网。

    Args:
        url: Absolute resource URL.
        timeout: Total timeout in seconds.

    Returns:
        The downloaded payload.

    Raises:
        MusicRequestError: The transport failed, the body is empty, or the URL
            points at a private/loopback address.
    """
    content, _ = await fetch_bytes_checked(url, timeout)
    if not content:
        raise MusicRequestError("音频下载失败：返回内容为空")
    return content
