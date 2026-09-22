"""生命周期钩子：进程关闭 / 插件重载时释放常驻资源。"""

from __future__ import annotations

import asyncio

from gsuid_core.logger import logger
from gsuid_core.server import on_core_shutdown

from ..utils.http import close_client
from ..utils.render import close_browser
from ..utils.resource.RESOURCE_PATH import TEMP_PATH

# 单个释放动作的等待上限，避免拖住进程退出
RELEASE_TIMEOUT_SEC = 10


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
