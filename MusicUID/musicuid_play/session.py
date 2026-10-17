"""按（群 + 用户）隔离的搜索列表，供「听N」稍后选歌。"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import field, dataclass

from gsuid_core.models import Event

from ..utils.provider import SongInfo

# 列表有效期：与原插件一致，10 分钟内可继续选歌；每个用户各自计时
SESSION_TTL_SEC = 600
# 最大会话容量：按用户隔离后条目会明显变多，适当放大以防长期运行内存泄漏
MAX_SESSION_COUNT = 2000


@dataclass
class SongListSession:
    """The most recent search results of one user in one chat.

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
    """Pick the cache key of a session.

    群聊里按「群号 + 用户号」隔离：早先整群共用一个列表，谁后点歌就会把前一个人
    的列表顶掉，前一个人再发「听N」时命中的是别人的结果。改为按用户隔离后，
    每个人的列表与有效期都互不影响。

    Args:
        ev: The current event.

    Returns:
        群聊为 ``群号:用户号``，私聊为 ``用户号``。
    """
    if ev.group_id:
        return f"{ev.group_id}:{ev.user_id}"
    return ev.user_id


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
