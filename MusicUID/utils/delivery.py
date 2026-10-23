"""Download resolved audio and push it to the current session."""

from __future__ import annotations

import asyncio
from uuid import uuid4
from typing import Final, Protocol
from pathlib import Path

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

# mutagen 识别出的 MIME 到实际后缀；同一格式可能有多个 MIME 别名
AUDIO_MIME_SUFFIXES: Final[dict[str, str]] = {
    "audio/mp3": ".mp3",
    "audio/x-mp3": ".mp3",
    "audio/mpeg": ".mp3",
    "audio/mpg": ".mp3",
    "audio/x-mpeg": ".mp3",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/mpeg4": ".m4a",
    "audio/vorbis": ".ogg",
    "audio/x-vorbis": ".ogg",
    "audio/ogg": ".ogg",
    "application/ogg": ".ogg",
    "application/x-ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/wave": ".wav",
    "audio/x-wav": ".wav",
    "audio/aiff": ".aiff",
    "audio/x-aiff": ".aiff",
    "audio/aac": ".aac",
    "audio/x-aac": ".aac",
    "audio/x-ape": ".ape",
    "audio/x-wavpack": ".wv",
    "audio/x-ms-wma": ".wma",
    "audio/ac3": ".ac3",
}


def sniff_audio_suffix(payload: bytes) -> str:
    """按文件头签名判断音频容器，作为 mutagen 识别失败时的兜底。

    Args:
        payload: 下载到的音频字节。

    Returns:
        带点的后缀；无法识别时返回空字符串。
    """
    head = payload[:12]
    if head[:4] == b"fLaC":
        return ".flac"
    if head[4:8] == b"ftyp":
        return ".m4a"
    if head[:4] == b"OggS":
        return ".ogg"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return ".wav"
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return ".aiff"
    if head[:3] == b"ID3":
        return ".mp3"
    if len(head) >= 2 and head[0] == 0xFF:
        # ADTS 的 layer 位固定为 00；MPEG 音频（MP3）则使用 01/10/11
        if head[1] & 0xF6 == 0xF0:
            return ".aac"
        if head[1] & 0xE0 == 0xE0:
            return ".mp3"
    if head[:4] == b"MAC ":
        return ".ape"
    if head[:4] == b"wvpk":
        return ".wv"
    if head[:4] == b"\x30\x26\xb2\x75":
        return ".wma"
    if head[:2] == b"\x0b\x77":
        return ".ac3"
    return ""


def detect_audio_suffix(path: Path) -> str:
    """按文件实际内容识别音频后缀，优先使用 mutagen，签名兜底。"""
    try:
        from mutagen import File as MutagenFile, MutagenError

        try:
            audio = MutagenFile(path)
        except (MutagenError, OSError):
            audio = None
        if audio is not None:
            for mime in audio.mime:
                suffix = AUDIO_MIME_SUFFIXES.get(mime)
                if suffix:
                    return suffix
    except ImportError:
        pass
    try:
        with path.open("rb") as audio_file:
            return sniff_audio_suffix(audio_file.read(12))
    except OSError:
        return ""


# ffmpeg 压缩上限：卡住时必须回收，否则协程永久挂起、孤儿进程继续写盘
COMPRESS_TIMEOUT_SEC: Final[float] = 120.0

# 语音最低码率：再低就不是「音质差」而是听不清了
MIN_VOICE_KBPS: Final[int] = 64

# 从这个码率起保留立体声（低码率下立体声会摊薄每声道的信息量）
STEREO_MIN_KBPS: Final[int] = 128

ILLEGAL_NAME_CHARS = '<>:"/\\|?*\n\r\t'


def _resolve_ffmpeg() -> str | None:
    """返回 imageio-ffmpeg 自带的 ffmpeg 路径，找不到时返回 None。"""
    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        return get_ffmpeg_exe()
    except (ImportError, RuntimeError) as e:
        logger.warning(f"[MusicUID] 未找到可用的 ffmpeg：{e}")
        return None


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
# 对它们改发带真实后缀的 file:// 路径，适配器即可按实际格式转码成 silk。
LOCAL_PATH_BOTS: Final[frozenset[str]] = frozenset({"qq_official"})


class BotSender(Protocol):
    """deliver_audio 实际需要的 Bot 能力，便于测试和无副作用调用。"""

    bot_id: str

    async def send(self, message: Message | list[Message]) -> list[str] | None: ...


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


def pick_voice_bitrate(max_kbps: int, limit_bytes: int, duration_sec: int) -> int:
    """Pick the highest bitrate that still fits the voice size limit.

    语音通道有体积天花板，码率 × 时长就是体积，所以「压多狠」不该写死：
    短歌可以在上限内保住接近原始的码率，长歌才需要真的降。

    Args:
        max_kbps: 配置的码率上限（``voice_bitrate``）。
        limit_bytes: 语音体积上限（``voice_max_mb`` 换算后的字节）。
        duration_sec: 歌曲时长，``0`` 表示未知。

    Returns:
        目标码率（kbps），不低于 :data:`MIN_VOICE_KBPS`。
    """
    if duration_sec <= 0:
        return max_kbps
    by_size = int(limit_bytes * 8 / duration_sec / 1000)
    return max(MIN_VOICE_KBPS, min(max_kbps, by_size))


async def compress_for_voice(path: Path, bitrate: int, keep_stereo: bool) -> Path | None:
    """Shrink an audio file with ffmpeg so it fits platform voice limits.

    整首 320kbps 的歌（10MB 级）作为语音会被 QQ 静默丢弃，所以要降到体积上限内；
    但降多少由 :func:`pick_voice_bitrate` 按剩余空间决定，而不是一律压到最低档。

    Args:
        path: Downloaded audio file.
        bitrate: Target mp3 bitrate in kbps.
        keep_stereo: 是否保留源声道；低码率下立体声会摊薄每声道的信息量，反而更难听。

    Returns:
        The compressed file, or ``None`` when ffmpeg is unavailable or failed
        (callers then keep using the original file).
    """
    out = path.with_name(f"{path.stem}_v.mp3")
    channel_args = [] if keep_stereo else ["-ac", "1"]
    ffmpeg = _resolve_ffmpeg()
    if ffmpeg is None:
        logger.warning("[MusicUID] 没有可用的 ffmpeg，跳过语音压缩并使用原始音频")
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-y",
            "-i",
            str(path),
            *channel_args,
            "-b:a",
            f"{bitrate}k",
            str(out),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as e:
        logger.warning(f"[MusicUID] ffmpeg 调用失败：{e}")
        return None

    try:
        code = await asyncio.wait_for(proc.wait(), timeout=COMPRESS_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        # ffmpeg 卡住时要主动回收，否则它会成为孤儿进程继续写盘
        proc.kill()
        await proc.wait()
        logger.warning("[MusicUID] ffmpeg 压缩超时，改用原始音频")
        out.unlink(missing_ok=True)
        return None
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        out.unlink(missing_ok=True)
        raise

    if code != 0 or not out.exists():
        logger.warning(f"[MusicUID] ffmpeg 压缩失败（rc={code}）")
        out.unlink(missing_ok=True)
        return None
    return out


def _write_song_meta_sync(path: Path, song: SongInfo) -> bool:
    """用 mutagen 原地写标签，按文件内容识别格式，不依赖文件后缀。"""
    try:
        from mutagen import File as MutagenFile, MutagenError
        from mutagen.id3 import TALB, TIT2, TPE1, TPE2
        from mutagen.mp3 import EasyMP3
        from mutagen.flac import FLAC
        from mutagen.wave import WAVE
        from mutagen.easymp4 import EasyMP4
        from mutagen.oggopus import OggOpus
        from mutagen.oggvorbis import OggVorbis
    except ImportError as e:
        logger.warning(f"[MusicUID] 缺少 mutagen，跳过歌曲信息写入：{e}")
        return False

    try:
        audio = MutagenFile(path, easy=True)
        if audio is None:
            logger.debug(f"[MusicUID] mutagen 无法识别音频格式：{path.suffix}")
            return False
        if isinstance(audio, WAVE):
            if audio.tags is None:
                audio.add_tags()
            tags = audio.tags
            if tags is None:
                return False
            for frame_id, frame_type, value in (
                ("TIT2", TIT2, song.name),
                ("TPE1", TPE1, song.singers),
                ("TPE2", TPE2, song.singers),
                ("TALB", TALB, song.album),
            ):
                if value:
                    tags.setall(frame_id, [frame_type(encoding=3, text=[value])])
                else:
                    tags.delall(frame_id)
        elif isinstance(audio, (EasyMP3, FLAC, EasyMP4, OggOpus, OggVorbis)):
            for key, value in (
                ("title", song.name),
                ("artist", song.singers),
                ("albumartist", song.singers),
                ("album", song.album),
            ):
                if value:
                    audio[key] = [value]
        else:
            logger.debug(f"[MusicUID] mutagen 暂不支持该标签类型：{type(audio).__name__}")
            return False
        audio.save()
    except (MutagenError, OSError) as e:
        logger.warning(f"[MusicUID] mutagen 写入歌曲信息失败：{e}")
        return False
    return True


async def write_song_meta(path: Path, song: SongInfo) -> bool:
    """把标题/歌手/专辑/专辑歌手写进音频文件，右键属性就能看到。

    使用纯 Python 的 mutagen 原地修改标签，不重编码；``path`` 的后缀可以与
    实际内容不同，mutagen 会按文件头识别容器。

    Args:
        path: 已落盘的音频文件。
        song: 歌曲元数据。

    Returns:
        ``True`` 表示标签已写入。
    """
    return await asyncio.to_thread(_write_song_meta_sync, path, song)


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
    bot: BotSender,
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
    write_meta: bool,
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
        write_meta: Whether to write title/artist/album into the file.

    Returns:
        An empty string when the audio was delivered, otherwise a short
        user-facing reason describing what failed.
    """
    # 先落成中性临时文件，识别真实容器后再改名；后续发送与转语音都用真实后缀
    temp_path = TEMP_PATH / f"{uuid4().hex}.part"
    try:
        payload = await download(audio_url, timeout)
    except Exception as e:
        logger.warning(f"[MusicUID] 音频下载失败：{e}")
        return "音频下载失败，可能该歌曲无版权或接口受限"

    # 无版权曲目的外链只返回很小的占位响应，不该当成歌曲下发（先判体积再落盘）
    if len(payload) < MIN_AUDIO_BYTES:
        return "音频下载失败：该歌曲可能无版权或需要 VIP"
    try:
        await asyncio.to_thread(temp_path.write_bytes, payload)
    except OSError as e:
        logger.warning(f"[MusicUID] 音频落盘失败：{e}")
        return "音频下载失败：临时文件写入失败"
    suffix = await asyncio.to_thread(detect_audio_suffix, temp_path)
    if not suffix:
        temp_path.unlink(missing_ok=True)
        return "音频下载失败：下载内容不是可识别的音频格式"
    audio_path = temp_path.with_suffix(suffix)
    try:
        await asyncio.to_thread(temp_path.replace, audio_path)
    except OSError as e:
        temp_path.unlink(missing_ok=True)
        logger.warning(f"[MusicUID] 音频改名为 {suffix} 失败：{e}")
        return "音频下载失败：临时文件改名失败"
    file_name = _safe_file_name(song, suffix)
    # 写标签要在语音压缩之前：压缩会重新编码，标签会丢
    if write_meta:
        await write_song_meta(audio_path, song)
    # QQ 等平台对语音消息的体积限制很严：整首 320kbps 会被静默丢弃，先压小再发。
    # 压到多低由时长与体积上限算出来，不做一刀切，否则控制台里的音质档位白设
    voice_path = audio_path
    limit_bytes = voice_max_mb * 1024 * 1024
    if send_voice and len(payload) > limit_bytes:
        target_kbps = pick_voice_bitrate(voice_bitrate, limit_bytes, song.duration_sec)
        keep_stereo = target_kbps >= STEREO_MIN_KBPS
        compressed = await compress_for_voice(audio_path, target_kbps, keep_stereo)
        if compressed is not None:
            voice_path = compressed
            logger.info(
                f"[MusicUID] 语音压缩 {len(payload)} → {compressed.stat().st_size} bytes"
                f"（{target_kbps}kbps {'立体声' if keep_stereo else '单声道'}，"
                f"源音频超过 {voice_max_mb}MB 语音上限）"
            )
    sent = False
    voice_ok = False
    file_ok = False
    failures: list[str] = []

    # 文案与音频同一条消息发出，避免在 QQ 官方等通道多消耗一次回复额度
    if send_voice:
        try:
            # base64 构造是同步阻塞的，交给线程做，别卡住整个事件循环
            voice_part = await asyncio.to_thread(voice_segment, voice_path, bot.bot_id, local_ref)
        except OSError as e:
            failures.append(f"语音准备失败（{e}）")
        else:
            try:
                await bot.send([MessageSegment.text(header), voice_part])
                sent = True
                voice_ok = True
            except Exception as e:  # noqa: BLE001 - 单渠道失败不能影响另一个渠道
                failures.append(f"语音发送失败（{e}）")
    if send_file:
        try:
            attachment = await asyncio.to_thread(MessageSegment.file, audio_path, file_name=file_name)
        except OSError as e:
            failures.append(f"文件准备失败（{e}）")
        else:
            try:
                if sent:
                    await bot.send(attachment)
                else:
                    await bot.send([MessageSegment.text(header), attachment])
                sent = True
                file_ok = True
            except Exception as e:  # noqa: BLE001 - 单渠道失败不能影响另一个渠道
                failures.append(f"文件发送失败（{e}）")

    if not sent:
        audio_path.unlink(missing_ok=True)
        if voice_path != audio_path:
            voice_path.unlink(missing_ok=True)
        # 语音与文件都关着时根本没发出任何东西，不能报成「发送失败」
        if not send_voice and not send_file:
            return "语音发送与文件发送均已关闭，请在配置里至少开启一个才能收到音频"
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
