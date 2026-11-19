"""Sign and send a QQ Music JSON card through OneBotUID."""

from __future__ import annotations

import json
import asyncio
from typing import Final
from dataclasses import dataclass
from urllib.parse import urlsplit
from collections.abc import Callable, Awaitable

import httpx

from gsuid_core.bot import Bot
from gsuid_core.logger import logger

from .http import USER_AGENT, get_json
from .provider import SongInfo
from ..musicuid_config import music_config

# 签名服务不可用时也不能拖住点歌的音频下发
CARD_REQUEST_TIMEOUT_SEC: Final[float] = 8.0

# 卡片点击后跳转的平台页；audio_url 才是签名服务要的 url
SOURCE_URLS: Final[dict[str, str]] = {
    "qq": "https://y.qq.com/n/ryqq/songDetail/{song_id}",
    "netease": "https://music.163.com/song?id={song_id}",
    "kugou": "https://www.kugou.com/song/#hash={song_id}",
}

# 配置列表按位置对应：qq、网易云、酷狗
CARD_FORMAT_ORDER: Final[tuple[str, ...]] = ("qq", "netease", "kugou")
DEFAULT_CARD_FORMATS: Final[tuple[str, ...]] = ("qq", "163", "kugou")


@dataclass(frozen=True, slots=True)
class MusicCardInfo:
    """签名 QQ 音乐卡片所需的歌曲信息。

    Attributes:
        audio_url: 解析后的音频直链，签名服务的 ``url``。
        source_url: 卡片点击跳转的平台歌曲页。
        title: 歌曲标题。
        artist: 歌手。
        cover_url: 封面链接，可为空。
        format: 签名服务的平台标识。
    """

    audio_url: str
    source_url: str
    title: str
    artist: str
    cover_url: str = ""
    format: str = ""


CallBotApi = Callable[[str, dict[str, object], str], Awaitable[object]]


def _load_call_bot_api() -> CallBotApi | None:
    """懒加载 OneBotUID 的接口调用函数；未安装返回 ``None``。"""
    try:
        from gsuid_core.plugins.OneBotUID.api import call_bot_api
    except ImportError:
        try:
            from OneBotUID.api import call_bot_api
        except ImportError:
            return None

    async def invoke(action: str, params: dict[str, object], self_id: str) -> object:
        return await call_bot_api(action, params, self_id=self_id)

    return invoke


def song_source_url(song: SongInfo) -> str:
    """返回卡片跳转用的平台歌曲页；不支持的平台返回空串。"""
    template = SOURCE_URLS.get(song.platform)
    return template.format(song_id=song.song_id) if template else ""


def platform_format(platform: str, formats: object = None) -> str:
    """按配置列表位置取平台对应的 format。"""
    values: list[str] = []
    if isinstance(formats, list):
        values = [str(item).strip() for item in formats]
    if len(values) < len(DEFAULT_CARD_FORMATS):
        values = list(DEFAULT_CARD_FORMATS)
    try:
        index = CARD_FORMAT_ORDER.index(platform)
    except ValueError:
        return ""
    value = values[index] if index < len(values) else DEFAULT_CARD_FORMATS[index]
    return value or DEFAULT_CARD_FORMATS[index]


def build_card_info(song: SongInfo, audio_url: str, formats: object = None) -> MusicCardInfo:
    """组装签名参数所需的歌曲信息。

    Args:
        song: 已解析的平台歌曲。
        audio_url: 解析后的音频直链。
        formats: ``签名服务器平台format`` 配置值。
    """
    return MusicCardInfo(
        audio_url=audio_url,
        source_url=song_source_url(song),
        title=song.name,
        artist=song.singers,
        cover_url=song.cover_url,
        format=platform_format(song.platform, formats),
    )


def _is_http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() in {"http", "https"}
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def _signed_message(payload: object) -> str:
    """取出签名接口返回的卡片 JSON，兼容 message 与 data 两种形态。"""
    if not isinstance(payload, dict):
        return ""
    code = payload.get("code")
    if code not in (0, 1, 200):
        return ""
    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    data = payload.get("data")
    if isinstance(data, dict):
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return ""


def _validate_card(card_json: str, info: MusicCardInfo) -> bool:
    """校验签名结果确实对应这首歌，避免把错误卡片发出去。"""
    try:
        card = json.loads(card_json)
    except ValueError:
        return False
    if not isinstance(card, dict):
        return False
    if card.get("app") != "com.tencent.music.lua" or card.get("view") != "music":
        return False
    config = card.get("config")
    if not isinstance(config, dict) or not config.get("token"):
        return False
    meta = card.get("meta")
    music = meta.get("music") if isinstance(meta, dict) else None
    if not isinstance(music, dict):
        return False
    music_url = music.get("musicUrl")
    jump_url = music.get("jumpUrl")
    if not isinstance(music_url, str) or not isinstance(jump_url, str):
        return False
    return music_url.replace(" ", "+") == info.audio_url and jump_url == info.source_url


def parse_custom_params(raw: object) -> dict[str, str]:
    """解析 ``{key}:{value}`` 形式的自定义请求参数。

    支持逗号或换行分隔；值可含冒号。空项忽略，键名重复时后者覆盖前者。

    Args:
        raw: 配置里的原始文本。

    Returns:
        解析后的参数映射；格式异常或空配置返回空字典。
    """
    text = str(raw or "").strip()
    if not text:
        return {}
    params: dict[str, str] = {}
    for item in text.replace("，", ",").replace("\r", "\n").split("\n"):
        for chunk in item.split(","):
            key, sep, value = chunk.partition(":")
            key = key.strip()
            if sep and key:
                params[key] = value.strip()
    return params


async def sign_music_card(
    sign_url: str,
    info: MusicCardInfo,
    custom_params: dict[str, str] | None = None,
) -> str:
    """请求签名服务并返回原样可发送的卡片 JSON。

    Args:
        sign_url: 签名服务地址。
        info: 歌曲与音频直链信息。
        custom_params: 额外查询参数，如 ``{"key": "..."}``；不覆盖内置参数。
    """
    if not sign_url or not _is_http_url(info.audio_url) or not _is_http_url(info.source_url):
        return ""
    params: dict[str, str | int] = {
        "url": info.audio_url,
        "jump": info.source_url,
    }
    if info.title:
        params["song"] = info.title
    if info.artist:
        params["singer"] = info.artist
    if info.cover_url:
        params["cover"] = info.cover_url
    if info.format:
        params["format"] = info.format
    for key, value in (custom_params or {}).items():
        params.setdefault(key, value)
    try:
        payload = await asyncio.wait_for(
            get_json(sign_url, params=params),
            timeout=CARD_REQUEST_TIMEOUT_SEC,
        )
    except Exception as first_error:
        logger.debug(f"[MusicUID] 音乐卡片签名首次请求失败，改用独立连接重试：{first_error}")
        payload = await asyncio.wait_for(
            _get_json_direct(sign_url, params),
            timeout=CARD_REQUEST_TIMEOUT_SEC,
        )
    card = _signed_message(payload)
    return card if card and _validate_card(card, info) else ""


async def _get_json_direct(url: str, params: dict[str, str | int]) -> object:
    """部分签名服务会打断共享连接，换独立客户端重试一次。"""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(CARD_REQUEST_TIMEOUT_SEC),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        trust_env=False,
    ) as client:
        res = await client.get(url, params=params)
        res.raise_for_status()
        return res.json()


async def send_music_card(bot: Bot, song: SongInfo, audio_url: str) -> bool:
    """按配置独立发送音乐卡片，失败只记日志。

    Args:
        bot: 当前事件绑定的 Bot。
        song: 已解析的平台歌曲。
        audio_url: 已解析的音频直链。

    Returns:
        ``True`` 表示卡片已被协议端接受。
    """
    if not music_config.get_config("send_music_card").data:
        return False
    direct = bot.ev.user_type == "direct"
    target_id = bot.ev.user_id if direct else bot.ev.group_id
    if not target_id:
        logger.warning("[MusicUID] 音乐卡片缺少群号/用户号，已跳过")
        return False
    call_bot_api = _load_call_bot_api()
    if call_bot_api is None:
        logger.warning("[MusicUID] 未安装 OneBotUID，音乐卡片已跳过")
        return False
    sign_url = str(music_config.get_config("music_card_sign_url").data).strip()
    custom_params = parse_custom_params(music_config.get_config("music_card_params").data)
    formats = music_config.get_config("music_card_formats").data
    try:
        card = await sign_music_card(sign_url, build_card_info(song, audio_url, formats), custom_params)
    except Exception as e:  # noqa: BLE001 - 签名失败不能影响音频与卡片开关
        logger.warning(f"[MusicUID] 音乐卡片签名失败：{e}")
        return False
    if not card:
        logger.warning(f"[MusicUID] 音乐卡片签名结果为空（{song.platform}）")
        return False
    try:
        await call_bot_api(
            "send_private_msg" if direct else "send_group_msg",
            {
                "user_id" if direct else "group_id": int(target_id) if target_id.isdigit() else target_id,
                "message": [{"type": "json", "data": {"data": card}}],
            },
            bot.bot_self_id,
        )
    except Exception as e:  # noqa: BLE001 - 适配器异常不能中断音频主链路
        logger.warning(f"[MusicUID] OneBotUID 音乐卡片发送失败：{e}")
        return False
    logger.info(f"[MusicUID] 已通过 OneBotUID 下发音乐卡片：{song.name}")
    return True
