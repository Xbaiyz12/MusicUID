"""Provider registry: platform keys, implementations and alias resolution."""

from typing import Final

from .base import SongInfo, MusicProvider
from .kugou import KugouProvider
from .netease import NeteaseProvider
from .qqmusic import QqMusicProvider

PROVIDERS: Final[dict[str, MusicProvider]] = {
    "netease": NeteaseProvider(),
    "qq": QqMusicProvider(),
    "kugou": KugouProvider(),
}

DEFAULT_PLATFORM: Final[str] = "netease"

# 用户可在指令里写平台词（大小写不敏感）来临时切换平台
PLATFORM_ALIASES: Final[dict[str, str]] = {
    "netease": "netease",
    "ncm": "netease",
    "wyy": "netease",
    "网易": "netease",
    "网易云": "netease",
    "网易云音乐": "netease",
    "qq": "qq",
    "qqmusic": "qq",
    "qq音乐": "qq",
    "扣扣音乐": "qq",
    "kugou": "kugou",
    "kg": "kugou",
    "酷狗": "kugou",
    "酷狗音乐": "kugou",
}

__all__ = ["DEFAULT_PLATFORM", "PLATFORM_ALIASES", "PROVIDERS", "MusicProvider", "SongInfo", "resolve_platform"]


def resolve_platform(word: str) -> str:
    """Map a user-written platform word to a provider key.

    Args:
        word: Raw word taken from the command text.

    Returns:
        The provider key, or an empty string when it is not recognised.
    """
    return PLATFORM_ALIASES.get(word.strip().lower(), "")


def get_provider(platform: str) -> MusicProvider:
    """Return the provider registered for a platform key.

    Args:
        platform: A provider key that already passed :func:`resolve_platform`.

    Returns:
        The matching provider; falls back to the default when unknown.
    """
    return PROVIDERS.get(platform, PROVIDERS[DEFAULT_PLATFORM])
