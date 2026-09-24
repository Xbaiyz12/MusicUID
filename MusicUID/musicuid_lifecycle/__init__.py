"""生命周期钩子：启动时补齐渲染依赖，进程关闭 / 插件重载时释放常驻资源。"""

from __future__ import annotations

import sys
import asyncio
import importlib.util

from gsuid_core.logger import logger
from gsuid_core.server import on_core_start, on_core_shutdown

from ..utils.http import close_client
from ..utils.render import render_ready, close_browser
from ..musicuid_config import music_config
from ..utils.resource.RESOURCE_PATH import TEMP_PATH

# 单个释放动作的等待上限，避免拖住进程退出
RELEASE_TIMEOUT_SEC = 10
# 补依赖要跑 pip 并下载 Chromium 内核，给足时间但别无限等
INSTALL_TIMEOUT_SEC = 900

_RENDER_PACKAGE = "playwright"
_background_tasks: set[asyncio.Task[None]] = set()


def clean_temp_files() -> int:
    """Delete audio and card files left in the temp directory.

    Returns:
        Number of files removed.
    """
    removed = 0
    for path in TEMP_PATH.iterdir():
        if path.is_file():
            path.unlink(missing_ok=True)
            removed += 1
    return removed


async def _run_setup(cmd: list[str]) -> bool:
    """Run one setup command, logging only its outcome.

    Args:
        cmd: Command and its arguments.

    Returns:
        ``True`` when the command exited successfully.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as e:
        logger.warning(f"[MusicUID] 无法执行 {' '.join(cmd)}：{e}")
        return False
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=INSTALL_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        proc.kill()
        logger.warning(f"[MusicUID] {' '.join(cmd)} 超时，已放弃")
        return False
    if proc.returncode == 0:
        return True
    tail = " / ".join((out or b"").decode("utf-8", errors="ignore").strip().splitlines()[-3:])
    logger.warning(f"[MusicUID] {' '.join(cmd)} 失败（退出码 {proc.returncode}）：{tail}")
    return False


async def install_render_deps() -> None:
    """Install the playwright package when missing, then download Chromium.

    Chromium 内核有上百 MB，所以整个过程只在后台跑；任何一步失败都只降级为纯文本，
    不影响其他指令。
    """
    python = sys.executable
    logger.info("[MusicUID] 卡片渲染环境不完整，开始自动安装依赖（可能耗时数分钟）")
    if importlib.util.find_spec(_RENDER_PACKAGE) is None:
        if not await _run_setup([python, "-m", "pip", "install", _RENDER_PACKAGE]):
            logger.warning("[MusicUID] playwright 安装失败，卡片将继续以纯文本返回")
            return
    # Linux 上除了内核本体还会缺一堆系统库，--with-deps 会用包管理器一并补齐
    chromium_cmd = [python, "-m", _RENDER_PACKAGE, "install"]
    if sys.platform.startswith("linux"):
        chromium_cmd.append("--with-deps")
    chromium_cmd.append("chromium")
    if not await _run_setup(chromium_cmd):
        logger.warning("[MusicUID] Chromium 内核下载失败，卡片将继续以纯文本返回")
        return
    if await render_ready():
        logger.info("[MusicUID] 卡片渲染环境已就绪，下次点歌即可收到图片卡片")
    else:
        logger.warning("[MusicUID] 依赖已装上但浏览器仍无法启动，建议重载一次插件")


def pytakumi_available() -> bool:
    """Report whether GsCore's built-in pytakumi renderer is importable.

    这是主渲染路径：纯本地、不需要浏览器，所以只要它在，就完全不必碰 Chromium。

    Returns:
        ``True`` when pytakumi can be imported.
    """
    return importlib.util.find_spec("pytakumi") is not None


@on_core_start
async def prepare_render_env() -> None:
    """启动时检测渲染环境，只在缺 pytakumi 时才去补浏览器回退（不阻塞启动）。"""
    if pytakumi_available():
        logger.info("[MusicUID] 卡片渲染就绪（pytakumi，无需浏览器）")
        return
    logger.info("[MusicUID] 未检测到 pytakumi，将尝试浏览器渲染回退")
    if not music_config.get_config("auto_install_render").data:
        logger.debug("[MusicUID] 自动安装渲染依赖已关闭，跳过检测")
        return
    if await render_ready():
        logger.info("[MusicUID] 浏览器渲染可用，图片卡片就绪")
        return
    logger.info("[MusicUID] 浏览器渲染也不可用，将在后台自动下载 Chromium")
    task = asyncio.create_task(install_render_deps())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@on_core_shutdown
async def release_resources() -> None:
    """关闭常驻 Chromium 与共享 HTTP 连接池，并清掉遗留临时文件。

    钩子有总时间预算，超时会被强制中止，所以每步都限时且不向外抛异常。
    """
    for label, closer in (("Chromium", close_browser), ("HTTP 客户端", close_client)):
        try:
            await asyncio.wait_for(closer(), timeout=RELEASE_TIMEOUT_SEC)
        except Exception as e:
            logger.warning(f"[MusicUID] 释放{label}失败：{e}")
    removed = clean_temp_files()
    if removed:
        logger.info(f"[MusicUID] 已清理 {removed} 个临时文件")
