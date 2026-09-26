"""登录模块导出入口。"""

from __future__ import annotations

from .base import LoginStatus, LoginSession, BaseLoginProvider, make_qr_image
from .kugou import KugouLoginProvider
from .netease import NeteaseLoginProvider
from .qqmusic import (
    QQMusicLoginProvider,
    auto_refresh_qq_job,
    format_qq_cookie,
    load_qq_credential,
    parse_qq_cookie,
    refresh_qq_credential,
)

LOGIN_PROVIDERS: dict[str, BaseLoginProvider] = {
    "netease": NeteaseLoginProvider(),
    "163": NeteaseLoginProvider(),
    "网易云": NeteaseLoginProvider(),
    "网易云音乐": NeteaseLoginProvider(),
    "kugou": KugouLoginProvider(),
    "kg": KugouLoginProvider(),
    "酷狗": KugouLoginProvider(),
    "酷狗音乐": KugouLoginProvider(),
    "qq": QQMusicLoginProvider(),
    "qqmusic": QQMusicLoginProvider(),
    "QQ": QQMusicLoginProvider(),
    "QQ音乐": QQMusicLoginProvider(),
}


def get_login_provider(name: str) -> BaseLoginProvider | None:
    """根据名称获取对应的登录 Provider。"""
    cleaned = name.strip().lower()
    return LOGIN_PROVIDERS.get(cleaned) or LOGIN_PROVIDERS.get(name.strip())


__all__ = [
    "BaseLoginProvider",
    "KugouLoginProvider",
    "LOGIN_PROVIDERS",
    "LoginSession",
    "LoginStatus",
    "NeteaseLoginProvider",
    "QQMusicLoginProvider",
    "auto_refresh_qq_job",
    "format_qq_cookie",
    "get_login_provider",
    "load_qq_credential",
    "make_qr_image",
    "parse_qq_cookie",
    "refresh_qq_credential",
]
