"""Card payload builders: turn per-platform search results into images or text."""

from __future__ import annotations

import base64
import asyncio
from dataclasses import field, dataclass

from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.segment import MessageSegment

from ..utils.http import get_client
from ..utils.render import render_card
from ..utils.provider import SongInfo, get_provider
from ..musicuid_config import music_config

CARD_TEMPLATE = "song_list.html"
HELP_TEMPLATE = "help.html"
STATUS_TEMPLATE = "status.html"
TEXT_TEMPLATE = "text.html"

# 多平台卡片与单平台卡片共用同一模板，只有 hero 主题不同
MULTI_THEME = "multi"

# 封面内联的体积上限：超大图会把整张卡片的 HTML 撑到几十 MB
MAX_COVER_BYTES = 2 * 1024 * 1024

# 超过这个字数就渲染成图片，免得群里刷屏
LONG_TEXT_THRESHOLD = 50


@dataclass(frozen=True, slots=True)
class HelpItem:
    """帮助里的一条指令。

    Attributes:
        cmd: 指令写法。
        desc: 一句话说明。
        tag: 右侧角标，留空则用所在分组的角标。
        tag_type: 角标配色（``""`` / ``perm`` / ``vip``），留空则用分组的。
    """

    cmd: str
    desc: str
    tag: str = ""
    tag_type: str = ""


@dataclass(frozen=True, slots=True)
class HelpSection:
    """帮助里的一个分组。

    Attributes:
        title: 分组标题。
        items: 组内指令。
        tag: 组内条目的默认角标文案。
        tag_type: 组内条目的默认角标配色。
    """

    title: str
    items: tuple[HelpItem, ...]
    tag: str = ""
    tag_type: str = ""


@dataclass(frozen=True, slots=True)
class PlatformResult:
    """One platform's outcome for a keyword.

    Attributes:
        platform: Provider key.
        songs: Results in display order; empty when nothing matched or the
            search failed.
        error: User-facing failure reason, empty on success.
    """

    platform: str
    songs: list[SongInfo] = field(default_factory=list)
    error: str = ""

    @property
    def display_name(self) -> str:
        """Human readable platform name."""
        return get_provider(self.platform).display_name

    @property
    def playable(self) -> bool:
        """Whether this platform can actually deliver audio."""
        return get_provider(self.platform).supports_play


def _song_row(index: int, song: SongInfo) -> dict[str, object]:
    """Build one template row for a song.

    Args:
        index: 1-based position inside the whole card (numbers keep running
            across platforms so 听N stays unambiguous).
        song: Song metadata.

    Returns:
        Template-ready mapping.
    """
    return {
        "index": index,
        "cover": song.cover_url,
        "songName": song.name,
        "singerName": song.singers,
        "albumName": song.album,
        "duration": song.duration_text,
        "payplay": song.payplay,
    }


def plain_song_list(keyword: str, results: list[PlatformResult], play_hint: str) -> str:
    """Render every platform's results as plain text.

    Args:
        keyword: The keyword the user searched for.
        results: Per-platform outcomes, in display order.
        play_hint: Command shown to the user for picking a song.

    Returns:
        A multi-line message body.
    """
    lines = [f"🎵 {keyword}"]
    index = 0
    playable_names: list[str] = []
    for result in results:
        lines.append(f"\n【{result.display_name}】")
        if result.error:
            lines.append(f"  {result.error}")
            continue
        if not result.songs:
            lines.append("  没有搜到相关歌曲")
            continue
        for song in result.songs:
            index += 1
            lines.append(f"  {index}. {song.name} - {song.singers}")
        if result.playable:
            playable_names.append(result.display_name)
    if playable_names:
        lines.append(f"\n发送「{play_hint}」播放第 N 首（编号全平台连续，当前可播放：{'、'.join(playable_names)}）")
    else:
        lines.append("\n当前列出的平台暂时都取不到音源")
    return "\n".join(lines)


async def _inline_covers(rows: list[dict[str, object]]) -> None:
    """Download covers and inline them as data URIs.

    pytakumi 渲染器不联网取图，留着远程 URL 会画成空白，所以先把封面抓回来内联。
    单张失败只清空该行封面，绝不影响整张卡片。

    Args:
        rows: Template rows produced by :func:`_song_row`, modified in place.
    """
    sem = asyncio.Semaphore(5)

    async def one(row: dict[str, object]) -> None:
        url = row.get("cover")
        if not isinstance(url, str) or not url.startswith("http"):
            return
        async with sem:
            try:
                resp = await get_client().get(url, timeout=3.0)
                resp.raise_for_status()
                if len(resp.content) > MAX_COVER_BYTES:
                    logger.debug(f"[MusicUID] 封面过大已跳过（{len(resp.content)} bytes）")
                    row["cover"] = ""
                    return
                mime = resp.headers.get("content-type", "image/jpeg").split(";")[0]
                row["cover"] = f"data:{mime};base64,{base64.b64encode(resp.content).decode()}"
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[MusicUID] 封面内联失败（{url[:60]}）：{e}")
                row["cover"] = ""

    await asyncio.gather(*(one(row) for row in rows))


async def send_song_list(
    bot: Bot,
    keyword: str,
    results: list[PlatformResult],
    play_hint: str,
) -> None:
    """Send per-platform results as one image card, falling back to plain text.

    Args:
        bot: Bot wrapper bound to the current event.
        keyword: The keyword the user searched for.
        results: Per-platform outcomes, in display order.
        play_hint: Command shown to the user for picking a song.
    """
    if not any(result.songs for result in results):
        failed = [result for result in results if result.error]
        if failed:
            # 接口失败和「没有这首歌」不是一回事，不能把错误说成没搜到
            detail = "\n".join(f"• {item.display_name}：{item.error}" for item in failed)
            await bot.send(f"搜索失败，暂时查不到「{keyword}」：\n{detail}")
            return
        names = "、".join(result.display_name for result in results)
        await bot.send(f"{names} 都没有搜到「{keyword}」相关的歌曲")
        return

    if music_config.get_config("render_card").data:
        single = len(results) == 1
        total = sum(len(result.songs) for result in results)
        playable_names = [r.display_name for r in results if r.playable and r.songs]
        index = 0
        groups: list[dict[str, object]] = []
        all_rows: list[dict[str, object]] = []
        for result in results:
            rows: list[dict[str, object]] = []
            for song in result.songs:
                index += 1
                rows.append(_song_row(index, song))
            all_rows.extend(rows)
            groups.append(
                {
                    "platform": result.platform,
                    "platform_name": result.display_name,
                    "songs": rows,
                    "playable": result.playable,
                    "empty_text": result.error or "没有搜到相关歌曲",
                }
            )
        # pytakumi 渲染器不联网取图，封面必须先内联成 data URI
        await _inline_covers(all_rows)
        hit_names = "、".join(r.display_name for r in results if r.songs)
        if playable_names:
            tip = (
                f"发送「{play_hint}」播放第 N 首（编号全平台连续，"
                f"当前可播放：{'、'.join(playable_names)}），列表 10 分钟内有效"
            )
        else:
            tip = "当前列出的平台暂时都取不到音源"
        data: dict[str, object] = {
            "theme": results[0].platform if single else MULTI_THEME,
            "eyebrow": f"{results[0].display_name} · SEARCH" if single else "全平台 · SEARCH",
            "keyword": keyword,
            "total": total,
            "subtitle": f"{hit_names} 共 {total} 首",
            "groups": groups,
            "tip": tip,
            "footer_left": f"{hit_names} · 数据来自公开接口",
        }
        png = await render_card(CARD_TEMPLATE, data)
        if png is not None:
            await bot.send(MessageSegment.image(png))
            return
        logger.debug("[MusicUID] 卡片渲染不可用，回退纯文本列表")

    await send_text_card(bot, plain_song_list(keyword, results, play_hint), "搜索结果")


async def send_help_card(bot: Bot, sections: tuple[HelpSection, ...], fallback_text: str) -> None:
    """Send help instructions as a rendered image card, falling back to plain text.

    Args:
        bot: Bot wrapper bound to the current event.
        sections: 结构化帮助分组，内容由 ``musicuid_help`` 单一维护。
        fallback_text: 渲染不可用时发送的纯文本。
    """
    if music_config.get_config("render_card").data:
        data: dict[str, object] = {
            "sections": [
                {
                    "type": "cmd",
                    "title": section.title,
                    "commands": [
                        {
                            "cmd": item.cmd,
                            "desc": item.desc,
                            "tag": item.tag or section.tag,
                            "tag_type": item.tag_type or section.tag_type,
                        }
                        for item in section.items
                    ],
                }
                for section in sections
            ],
        }
        png = await render_card(HELP_TEMPLATE, data)
        if png is not None:
            await bot.send(MessageSegment.image(png))
            return
        logger.debug("[MusicUID] 帮助卡片渲染不可用，回退纯文本")

    await bot.send(fallback_text)


async def send_status_card(bot: Bot, data: dict[str, object], fallback_text: str) -> None:
    """Send the credential status card, falling back to plain text.

    Args:
        bot: Bot wrapper bound to the current event.
        data: Template payload for ``status.html``.
        fallback_text: Plain text used when rendering is unavailable.
    """
    if music_config.get_config("render_card").data:
        png = await render_card(STATUS_TEMPLATE, data)
        if png is not None:
            await bot.send(MessageSegment.image(png))
            return
        logger.debug("[MusicUID] 状态卡片渲染不可用，回退纯文本")

    await bot.send(fallback_text)


@dataclass(frozen=True, slots=True)
class TextSection:
    """长文本里的一个段落。

    Attributes:
        heading: 段落标题（``【xxx】`` 里的内容），没有则为空串。
        lines: 段落内的原始行。
    """

    heading: str
    lines: tuple[str, ...]


def split_sections(text: str) -> tuple[TextSection, ...]:
    """把纯文本按 ``【小标题】`` 拆成段落。

    Args:
        text: 原始文本，空行会被跳过。

    Returns:
        段落元组；没有小标题时整段作为一段（heading 为空）。
    """
    sections: list[TextSection] = []
    heading = ""
    lines: list[str] = []

    def flush() -> None:
        if heading or lines:
            sections.append(TextSection(heading=heading, lines=tuple(lines)))

    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("【") and stripped.endswith("】") and len(stripped) > 2:
            flush()
            heading = stripped[1:-1]
            lines = []
            continue
        lines.append(raw.rstrip())
    flush()
    return tuple(sections)


async def send_text_card(bot: Bot, text: str, title: str = "MusicUID", subtitle: str = "") -> None:
    """发送文本：短的直接发，超过阈值的渲染成图片。

    渲染不可用或开关关闭时自动回退成纯文本，绝不让消息发不出去。

    Args:
        bot: Bot wrapper bound to the current event.
        text: 要发送的文本。
        title: 卡片标题。
        subtitle: 卡片副标题。
    """
    if len(text) <= LONG_TEXT_THRESHOLD or not music_config.get_config("render_card").data:
        await bot.send(text)
        return
    sections = split_sections(text)
    if not sections:
        await bot.send(text)
        return
    data: dict[str, object] = {
        "title": title,
        "subtitle": subtitle,
        "sections": [{"heading": section.heading, "lines": list(section.lines)} for section in sections],
    }
    png = await render_card(TEXT_TEMPLATE, data)
    if png is None:
        logger.debug("[MusicUID] 长文本卡片渲染不可用，回退纯文本")
        await bot.send(text)
        return
    await bot.send(MessageSegment.image(png))
