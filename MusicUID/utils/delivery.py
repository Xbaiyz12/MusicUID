"""Download resolved audio and push it to the current session."""

from __future__ import annotations

import asyncio
from uuid import uuid4
from typing import Final
from pathlib import Path

from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Message
from gsuid_core.segment import MessageSegment

from .http import download
from .provider import SongInfo
from .resource.RESOURCE_PATH import TEMP_PATH

# 清理任务必须留引用，否则可能被 GC 掉导致临时文件不删除
_pending_cleanup: set[asyncio.Task[None]] = set()

# 正常歌曲远超 10KB；更小的响应是无版权占位页或试听片段
MIN_AUDIO_BYTES = 10 * 1024

ILLEGAL_NAME_CHARS = '<>:"/\\|?*\n\r\t'


def _safe_file_name(song: SongInfo, suffix: str) -> str:
    """Build a readable, filesystem-safe file name for a song.

    Args:
        song: The song being delivered.
        suffix: File extension including the dot.

    Returns:
        A sanitised ``歌名 - 歌手`` file name; ``歌名`` alone when there is no
        artist, and ``music`` when both fields are empty.
    """
    # 歌手为空时不留「歌名 - 」这种尾巴，两边都空则回退成通用名
    raw = f"{song.name} - {song.singers}" if song.singers else song.name
    cleaned = "".join("_" if ch in ILLEGAL_NAME_CHARS else ch for ch in raw).strip()
    return f"{cleaned[:60].strip() or 'music'}{suffix}"


# 这些渠道的适配器会把「无后缀的 base64 音频」当成 WAV 解析：AstrBot 的 qqofficial
# 适配器传 default_suffix=".wav" 落盘后直接报 "file does not start with RIFF id"。
# 对它们改发带 .mp3 后缀的 file:// 路径，适配器即可按真实格式转码成 silk。
LOCAL_PATH_BOTS: Final[frozenset[str]] = frozenset({"qq_official"})


def voice_segment(path: Path, bot_id: str, force_local: bool) -> Message:
    """Build the voice segment for a downloaded audio file.

    默认发 base64（跨适配器最兼容）；QQ 官方这类无法识别无后缀 base64 音频的渠道
    改发 file:// 本机路径（要求 core 与适配器同机）。

    Args:
        path: Downloaded audio file on this machine.
        bot_id: Current channel id (``ev.bot_id``).
        force_local: Force the ``file://`` form regardless of channel.

    Returns:
        The outgoing voice segment.
    """
    if force_local or bot_id in LOCAL_PATH_BOTS:
        return MessageSegment.record(path.as_uri())
    return MessageSegment.record(path)


async def compress_for_voice(path: Path, bitrate: int) -> Path | None:
    """Shrink an audio file with ffmpeg so it fits platform voice limits.

    整首 320kbps 的歌（10MB 级）作为语音会被 QQ 静默丢弃，压成单声道低码率后
    体积只有原来的几分之一。

    Args:
        path: Downloaded audio file.
        bitrate: Target mp3 bitrate in kbps.

    Returns:
        The compressed file, or ``None`` when ffmpeg is unavailable or failed
        (callers then keep using the original file).
    """
    out = path.with_name(f"{path.stem}_v.mp3")
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-ac",
            "1",
            "-b:a",
            f"{bitrate}k",
            str(out),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        code = await proc.wait()
    except OSError as e:
        logger.warning(f"[MusicUID] ffmpeg 调用失败：{e}")
        return None
    if code != 0 or not out.exists():
        logger.warning(f"[MusicUID] ffmpeg 压缩失败（rc={code}）")
        out.unlink(missing_ok=True)
        return None
    return out


def _schedule_cleanup(path: Path, keep_sec: int) -> None:
    """Delete a temporary file later, never before the sender read it.

    Args:
        path: File to remove.
        keep_sec: Requested retention in seconds; clamped to at least 5.
    """

    async def _cleanup() -> None:
        await asyncio.sleep(max(5, keep_sec))
        path.unlink(missing_ok=True)

    task = asyncio.create_task(_cleanup())
    _pending_cleanup.add(task)
    task.add_done_callback(_pending_cleanup.discard)


async def deliver_audio(
    bot: Bot,
    song: SongInfo,
    audio_url: str,
    *,
    header: str,
    send_voice: bool,
    send_file: bool,
    local_ref: bool,
    timeout: float,
    keep_sec: int,
    voice_max_mb: int,
    voice_bitrate: int,
) -> str:
    """Download the audio and send it as voice and/or file.

    Args:
        bot: Bot wrapper bound to the current event.
        song: Song metadata, used for the outgoing file name.
        audio_url: Directly downloadable audio URL.
        header: Song description merged into the first outgoing message.
        send_voice: Whether to deliver the audio as a voice message.
        send_file: Whether to deliver the audio as a file attachment.
        local_ref: Whether the voice segment references the local file by URI.
        timeout: Download timeout in seconds.
        keep_sec: How long the temporary file is kept after sending.
        voice_max_mb: Compress for voice when the file exceeds this size.
        voice_bitrate: Target mp3 bitrate (kbps) used by that compression.

    Returns:
        An empty string when the audio was delivered, otherwise a short
        user-facing reason describing what failed.
    """
    suffix = ".mp3"
    audio_path = TEMP_PATH / f"{uuid4().hex}{suffix}"
    try:
        payload = await download(audio_url, timeout)
    except Exception as e:
        logger.warning(f"[MusicUID] 音频下载失败：{e}")
        return "音频下载失败，可能该歌曲无版权或接口受限"

    audio_path.write_bytes(payload)
    # 无版权曲目的外链只返回很小的占位响应，不该当成歌曲下发
    if len(payload) < MIN_AUDIO_BYTES:
        audio_path.unlink(missing_ok=True)
        return "音频下载失败：该歌曲可能无版权或需要 VIP"
    file_name = _safe_file_name(song, suffix)
    # QQ 等平台对语音消息的体积限制很严：整首 320kbps 会被静默丢弃，先压小再发
    voice_path = audio_path
    if send_voice and len(payload) > voice_max_mb * 1024 * 1024:
        compressed = await compress_for_voice(audio_path, voice_bitrate)
        if compressed is not None:
            voice_path = compressed
            logger.info(
                f"[MusicUID] 语音压缩 {len(payload)} → {compressed.stat().st_size} bytes（{voice_bitrate}kbps 单声道）"
            )
    sent = False
    voice_ok = False
    file_ok = False
    failures: list[str] = []

    # 文案与音频同一条消息发出，避免在 QQ 官方等通道多消耗一次回复额度
    if send_voice:
        try:
            await bot.send([MessageSegment.text(header), voice_segment(voice_path, bot.bot_id, local_ref)])
            sent = True
            voice_ok = True
        except Exception as e:
            failures.append(f"语音发送失败（{e}）")
    if send_file:
        attachment = MessageSegment.file(audio_path, file_name=file_name)
        try:
            if sent:
                await bot.send(attachment)
            else:
                await bot.send([MessageSegment.text(header), attachment])
            sent = True
            file_ok = True
        except Exception as e:
            failures.append(f"文件发送失败（{e}）")

    if not sent:
        audio_path.unlink(missing_ok=True)
        if voice_path != audio_path:
            voice_path.unlink(missing_ok=True)
        return "；".join(failures) or "音频发送失败"

    _schedule_cleanup(audio_path, keep_sec)
    if voice_path != audio_path:
        _schedule_cleanup(voice_path, keep_sec)
    # 记清语音/文件各自的结果与真实发送体积，否则线上只能看出「已下发」
    sent_size = voice_path.stat().st_size if voice_path.exists() else 0
    logger.info(
        f"[MusicUID] 已下发 {song.platform} 歌曲 {song.name}"
        f"（下载 {len(payload)} bytes，语音发出 {sent_size} bytes"
        f"{'，已压缩' if voice_path != audio_path else ''}，"
        f"语音{'成功' if voice_ok else ('失败' if send_voice else '未启用')}，"
        f"文件{'成功' if file_ok else ('失败' if send_file else '未启用')}）"
    )
    return ""
