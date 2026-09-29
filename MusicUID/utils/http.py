"""Shared async HTTP client for the music platforms."""

from typing import Final
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
from collections.abc import Mapping

import httpx

USER_AGENT: Final[str] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT: Final[float] = 20.0

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


def _clean_url_for_log(url: str) -> str:
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
        safe_url = _clean_url_for_log(str(e.request.url if hasattr(e, "request") and e.request else url))
        raise MusicRequestError(f"请求失败（{safe_url}）：{type(e).__name__}") from e
    try:
        return res.json()
    except ValueError as e:
        safe_url = _clean_url_for_log(url)
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
        safe_url = _clean_url_for_log(str(e.request.url if hasattr(e, "request") and e.request else url))
        raise MusicRequestError(f"请求失败（{safe_url}）：{type(e).__name__}") from e
    try:
        return res.json()
    except ValueError as e:
        safe_url = _clean_url_for_log(url)
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
        raise MusicRequestError(f"请求失败（{_clean_url_for_log(url)}）：{e}") from e
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
        raise MusicRequestError(f"请求失败（{_clean_url_for_log(url)}）：{e}") from e
    return res.status_code, res.headers.get("location", "")


async def download(url: str, timeout: float) -> bytes:
    """Download raw bytes (audio files) from a CDN URL.

    Args:
        url: Absolute resource URL.
        timeout: Total timeout in seconds.

    Returns:
        The downloaded payload.

    Raises:
        MusicRequestError: The transport failed or the body is empty.
    """
    try:
        res = await get_client().get(url, timeout=httpx.Timeout(timeout))
        res.raise_for_status()
    except httpx.HTTPError as e:
        raise MusicRequestError(f"音频下载失败（{_clean_url_for_log(url)}）：{e}") from e
    if not res.content:
        raise MusicRequestError("音频下载失败：返回内容为空")
    return res.content
