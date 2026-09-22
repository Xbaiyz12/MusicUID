"""Per-session search results so 听N can pick a song later."""

from __future__ import annotations

import time
from dataclasses import field, dataclass

from gsuid_core.models import Event

from ..utils.provider import SongInfo

# 列表有效期：与原插件一致，10 分钟内可继续选歌
SESSION_TTL_SEC = 600


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


_sessions: dict[str, SongListSession] = {}


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
    _sessions[session_key(ev)] = SongListSession(
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
    session = _sessions.get(session_key(ev))
    if session is None:
        return None
    if time.monotonic() - session.created_at > SESSION_TTL_SEC:
        _sessions.pop(session_key(ev), None)
        return None
    return session
