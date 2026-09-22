"""Pytest bootstrap: make the Core repo and the plugins directory importable.

``SV()`` 从调用栈文件路径里找 ``plugins`` 段来推断插件归属，所以测试必须在
``<core>/gsuid_core/plugins/<Plugin>/tests`` 这个位置运行；找不到仓库根时
可用环境变量 ``GSUID_CORE_ROOT`` 显式指定。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()


def _find_repo_root() -> Path | None:
    for parent in _HERE.parents:
        if (parent / "gsuid_core" / "sv.py").is_file():
            return parent
    return None


def _bootstrap() -> None:
    env_root = os.environ.get("GSUID_CORE_ROOT", "")
    repo_root = Path(env_root).resolve() if env_root else _find_repo_root()
    if repo_root is None:
        raise RuntimeError("找不到 GsCore 仓库根，请设置 GSUID_CORE_ROOT 环境变量")
    for path in (repo_root, repo_root / "gsuid_core" / "plugins"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


_bootstrap()
