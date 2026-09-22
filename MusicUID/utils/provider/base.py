"""Platform-agnostic song model and provider protocol."""

from typing import Protocol
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SongInfo:
    """One song resolved from a platform search.

    Attributes:
        platform: Provider key (``netease`` / ``qq`` / ``kugou``).
        song_id: Platform primary key used by every follow-up request.
        name: Song title.
        singers: Artist names joined with ``/``.
        album: Album name, may be empty.
        duration_sec: Track length in seconds, ``0`` when unknown.
        cover_url: Cover image URL, may be empty.
        payplay: Whether the platform marks the track as paid/VIP.
    """

    platform: str
    song_id: str
    name: str
    singers: str
    album: str = ""
    duration_sec: int = 0
    cover_url: str = ""
    payplay: bool = False

    @property
    def duration_text(self) -> str:
        """Render the duration as ``mm:ss``; empty when unknown."""
        if self.duration_sec <= 0:
            return ""
        return f"{self.duration_sec // 60:02d}:{self.duration_sec % 60:02d}"


@dataclass(frozen=True, slots=True)
class SongCollection:
    """A playlist or album resolved from a share link.

    Attributes:
        name: Collection title shown to the user.
        songs: Member songs, in the platform's order.
    """

    name: str
    songs: list[SongInfo]


class MusicProvider(Protocol):
    """A music platform the plugin can search.

    Attributes:
        platform: Stable provider key used in configuration and commands.
        display_name: Human readable platform name for user-facing text.
        supports_play: Whether the provider can resolve a playable audio URL.
    """

    platform: str
    display_name: str
    supports_play: bool

    async def search(self, keyword: str, limit: int) -> list[SongInfo]:
        """Search the platform.

        Args:
            keyword: User supplied keywords.
            limit: Maximum number of results to request.

        Returns:
            Matching songs, possibly empty.

        Raises:
            MusicRequestError: The platform request failed.
        """
        ...

    async def detail(self, song_id: str) -> SongInfo | None:
        """Fetch one song by its platform id (used by share-link parsing).

        Args:
            song_id: Platform primary key taken from a share link.

        Returns:
            The song, or ``None`` when the platform cannot resolve it.
        """
        ...

    async def collection(self, kind: str, collection_id: str) -> SongCollection | None:
        """Fetch a playlist or album together with its songs.

        Args:
            kind: ``playlist`` or ``album``.
            collection_id: Platform id taken from the share link.

        Returns:
            The collection, or ``None`` when the platform cannot resolve it.
        """
        ...

    async def lyric(self, song: SongInfo) -> str:
        """Fetch plain-text lyrics.

        Args:
            song: A song previously returned by :meth:`search`.

        Returns:
            Lyrics text, or an empty string when unavailable.
        """
        ...

    async def play_url(self, song: SongInfo) -> str:
        """Resolve a directly downloadable audio URL.

        Args:
            song: A song previously returned by :meth:`search`.

        Returns:
            The audio URL, or an empty string when it cannot be resolved.
        """
        ...
