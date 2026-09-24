"""Per-session search results so 听N can pick a song later."""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import field, dataclass

from gsuid_core.models import Event

from ..utils.provider import SongInfo

# 列表有效期：与原插件一致，10 分钟内可继续选歌
SESSION_TTL_SEC = 600
# 最大会话容量，防止群多长期运行内存泄漏
MAX_SESSION_COUNT = 500


@dataclass
class SongListSession:
    """The most recent search results of one chat session.

    Attributes:
        platform: Provider key the list came from.
        keyword: Keyword that produced the list.
        songs: The results, in display order.
        created_at: Monotonic timestamp used for expiry.
    """

    platform: str
    keyword: str
    songs: list[SongInfo] = field(default_factory=list)
    created_at: float = 0.0


_sessions: OrderedDict[str, SongListSession] = OrderedDict()


def session_key(ev: Event) -> str:
    """Pick the cache key of a session (group chat shares one list).

    Args:
        ev: The current event.

    Returns:
        The group id for group chats, otherwise the user id.
    """
    return ev.group_id or ev.user_id


def save_session(ev: Event, platform: str, keyword: str, songs: list[SongInfo]) -> None:
    """Store the search results of the current session.

    Args:
        ev: The current event.
        platform: Provider key the list came from.
        keyword: Keyword that produced the list.
        songs: The results, in display order.
    """
    key = session_key(ev)
    if key in _sessions:
        _sessions.pop(key)
    elif len(_sessions) >= MAX_SESSION_COUNT:
        _sessions.popitem(last=False)

    _sessions[key] = SongListSession(
        platform=platform,
        keyword=keyword,
        songs=songs,
        created_at=time.monotonic(),
    )


def get_session(ev: Event) -> SongListSession | None:
    """Return the live list of the current session.

    Args:
        ev: The current event.

    Returns:
        The cached list, or ``None`` when missing or expired.
    """
    key = session_key(ev)
    session = _sessions.get(key)
    if session is None:
        return None
    if time.monotonic() - session.created_at > SESSION_TTL_SEC:
        _sessions.pop(key, None)
        return None
    _sessions.move_to_end(key)
    return session
