"""网易云多账号池：凭据存储、去重与轮换取流。

账号池为空时回退到 ``netease_cookie`` 配置项，单账号用户不受影响。
这里刻意不导入 ``utils.provider``：那条链会在导入期反向依赖 login，
形成循环导入，所以 Cookie 规范化在本模块自己实现一份。
"""

from __future__ import annotations

import json
from typing import Final
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass

from gsuid_core.logger import logger

from .json_tools import to_obj, get_str
from ..musicuid_config import music_config
from .resource.RESOURCE_PATH import MAIN_PATH

ACCOUNTS_PATH: Final[Path] = MAIN_PATH / "netease_accounts.json"

# 轮询游标：进程内递增即可，重启后从头开始
_cursor = 0


@dataclass(frozen=True, slots=True)
class NeteaseAccount:
    """一个已登录的网易云账号。

    Attributes:
        name: 备注名。
        cookie: 只含 MUSIC_U 的 Cookie 串。
        added_at: 添加时间。
    """

    name: str
    cookie: str
    added_at: str


def normalize_cookie(cookie: str) -> str:
    """把裸 MUSIC_U 或完整 Cookie 规范成可用的请求头。

    Args:
        cookie: 原始 Cookie 串或裸 MUSIC_U 值。

    Returns:
        可直接放进请求头的 Cookie 串，空输入返回空串。
    """
    value = cookie.strip()
    if not value:
        return ""
    if "MUSIC_U" not in value:
        value = f"MUSIC_U={value}"
    # os=pc 必须带：同一条 MUSIC_U 只带它时才会被当成已登录
    if "os=" not in value:
        value = f"{value}; os=pc"
    return value


def music_u_of(cookie: str) -> str:
    """从 Cookie 串里取出 MUSIC_U 的值。

    Args:
        cookie: 可能含多个字段的 Cookie 串。

    Returns:
        MUSIC_U 的值，没有时返回空串。
    """
    for part in cookie.split(";"):
        key, _, value = part.strip().partition("=")
        if key == "MUSIC_U":
            return value
    return ""


def _write_raw(accounts: list[NeteaseAccount]) -> bool:
    """原子写入账号池。

    Args:
        accounts: 要落盘的账号列表。

    Returns:
        是否写入成功。
    """
    payload = [{"name": item.name, "cookie": item.cookie, "added_at": item.added_at} for item in accounts]
    tmp = ACCOUNTS_PATH.with_name(f"{ACCOUNTS_PATH.name}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(ACCOUNTS_PATH)
    except OSError as e:
        logger.warning(f"[MusicUID] 账号池写入失败：{e}")
        tmp.unlink(missing_ok=True)
        return False
    return True


def load_accounts() -> list[NeteaseAccount]:
    """读取全部账号，文件缺失或损坏时返回空列表。"""
    if not ACCOUNTS_PATH.exists():
        return []
    try:
        raw = json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(f"[MusicUID] 账号池读取失败：{e}")
        return []
    if not isinstance(raw, list):
        return []
    accounts: list[NeteaseAccount] = []
    for item in raw:
        entry = to_obj(item)
        cookie = get_str(entry, "cookie")
        if not cookie:
            continue
        accounts.append(
            NeteaseAccount(
                name=get_str(entry, "name") or f"账号{len(accounts) + 1}",
                cookie=cookie,
                added_at=get_str(entry, "added_at"),
            )
        )
    return accounts


def save_account(name: str, cookie: str) -> bool:
    """把一个账号写入账号池；同一个 MUSIC_U 只保留一条（更新备注名）。

    Args:
        name: 备注名，留空则自动编号。
        cookie: 完整 Cookie 头或裸 MUSIC_U 值。

    Returns:
        是否保存成功。
    """
    music_u = music_u_of(normalize_cookie(cookie))
    if not music_u:
        return False
    accounts = [item for item in load_accounts() if music_u_of(item.cookie) != music_u]
    accounts.append(
        NeteaseAccount(
            name=name.strip() or f"账号{len(accounts) + 1}",
            cookie=f"MUSIC_U={music_u}",
            added_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
    )
    return _write_raw(accounts)


def remove_account(index: int) -> str:
    """按序号删除账号。

    Args:
        index: 从 1 开始的序号。

    Returns:
        被删除的账号名；序号非法时返回空串。
    """
    accounts = load_accounts()
    if index < 1 or index > len(accounts):
        return ""
    removed = accounts.pop(index - 1)
    if not _write_raw(accounts):
        return ""
    return removed.name


def pick_cookie() -> str:
    """按轮询取一个账号的 Cookie，用于取流。

    账号池为空时回退到 ``netease_cookie`` 配置项，保证单账号用户可用。

    Returns:
        可直接放进请求头的 Cookie 串，没有可用账号时为空串。
    """
    global _cursor
    accounts = load_accounts()
    if not accounts:
        return normalize_cookie(music_config.get_config("netease_cookie").data)
    index = _cursor % len(accounts)
    _cursor = (index + 1) % len(accounts)
    return normalize_cookie(accounts[index].cookie)
