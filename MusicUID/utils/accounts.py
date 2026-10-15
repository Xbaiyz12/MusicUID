"""多平台账号池：凭据存储、去重与轮换取流。

按平台分组存放；取流时同平台内轮换，池为空则回退到该平台的配置项。
这里刻意不导入 ``utils.provider``：那条链会在导入期反向依赖 login，形成循环导入，
所以 Cookie 规范化在本模块自己实现一份。
"""

from __future__ import annotations

import json
from typing import Final
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass

from gsuid_core.logger import logger

from .json_tools import to_obj, get_str, get_list
from ..musicuid_config import music_config
from .resource.RESOURCE_PATH import MAIN_PATH

ACCOUNTS_PATH: Final[Path] = MAIN_PATH / "accounts.json"
# 旧的网易云专用账号池，首次读取时自动并进来
LEGACY_NETEASE_PATH: Final[Path] = MAIN_PATH / "netease_accounts.json"

# 各平台凭据在配置项里的回退键
_CONFIG_KEYS: Final[dict[str, str]] = {
    "netease": "netease_cookie",
    "kugou": "kugou_cookie",
    "qq": "qqmusic_cookie",
}

# 账号唯一标识字段：按顺序取第一个非空的
_ID_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "netease": ("MUSIC_U",),
    "kugou": ("KugooID", "userid", "kg_mid"),
    "qq": ("uin",),
}

# 轮询游标：每个平台一份，进程内递增即可
_cursor: dict[str, int] = {}


@dataclass(frozen=True, slots=True)
class MusicAccount:
    """一个已登录的音乐平台账号。

    Attributes:
        name: 备注名。
        cookie: 该平台的凭据串。
        added_at: 添加时间。
    """

    name: str
    cookie: str
    added_at: str


def cookie_value_of(cookie: str, key: str) -> str:
    """从 Cookie 串里取某个字段的值。

    Args:
        cookie: 可能含多个字段的 Cookie 串。
        key: 字段名。

    Returns:
        字段值，没有时返回空串。
    """
    for part in cookie.split(";"):
        name, _, value = part.strip().partition("=")
        if name == key:
            return value
    return ""


def music_u_of(cookie: str) -> str:
    """取出网易云 Cookie 里的 MUSIC_U。

    Args:
        cookie: 网易云 Cookie 串。

    Returns:
        MUSIC_U 的值，没有时返回空串。
    """
    return cookie_value_of(cookie, "MUSIC_U")


def _qq_account_key(cookie: str) -> str:
    """QQ 音乐凭据是 JSON 串，按 musicid / uin / nick 去重。

    Args:
        cookie: JSON 序列化的凭据。

    Returns:
        唯一标识，解析失败时返回空串。
    """
    try:
        raw = json.loads(cookie)
    except ValueError:
        return ""
    entry = to_obj(raw)
    for field in ("musicid", "uin", "nick"):
        value = entry.get(field)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, int) and value > 0:
            return str(value)
    return ""


def account_key(platform: str, cookie: str) -> str:
    """取某个平台 Cookie 的账号唯一标识，用于去重。

    Args:
        platform: 平台标识。
        cookie: 该平台的 Cookie 串（QQ 音乐是 JSON 串）。

    Returns:
        唯一标识，取不到时返回空串。
    """
    if platform == "qq":
        return _qq_account_key(cookie)
    for field in _ID_FIELDS.get(platform, ("MUSIC_U",)):
        value = cookie_value_of(cookie, field)
        if value:
            return value
    return ""


def normalize_cookie(platform: str, cookie: str) -> str:
    """规范化 Cookie。

    网易云需要补 ``MUSIC_U`` 前缀与 ``os=pc``；其它平台只做去空白。

    Args:
        platform: 平台标识。
        cookie: 原始 Cookie 或裸值。

    Returns:
        可直接放进请求头的 Cookie 串，空输入返回空串。
    """
    value = cookie.strip()
    if not value or platform != "netease":
        return value
    if "MUSIC_U" not in value:
        value = f"MUSIC_U={value}"
    # os=pc 必须带：同一条 MUSIC_U 只带它时才会被当成已登录
    if "os=" not in value:
        value = f"{value}; os=pc"
    return value


def _parse_accounts(items: object) -> list[MusicAccount]:
    """把某个平台下的原始条目解析成账号列表。

    Args:
        items: 原始 JSON 值，期望是列表。

    Returns:
        解析成功的账号列表。
    """
    accounts: list[MusicAccount] = []
    for item in get_list({"items": items}, "items"):
        entry = to_obj(item)
        cookie = get_str(entry, "cookie")
        if not cookie:
            continue
        accounts.append(
            MusicAccount(
                name=get_str(entry, "name") or f"账号{len(accounts) + 1}",
                cookie=cookie,
                added_at=get_str(entry, "added_at"),
            )
        )
    return accounts


def _write_all(data: dict[str, list[MusicAccount]]) -> bool:
    """原子写入账号池。

    Args:
        data: 平台 -> 账号列表。

    Returns:
        是否写入成功。
    """
    payload = {
        platform: [{"name": item.name, "cookie": item.cookie, "added_at": item.added_at} for item in accounts]
        for platform, accounts in data.items()
        if accounts
    }
    tmp = ACCOUNTS_PATH.with_name(f"{ACCOUNTS_PATH.name}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(ACCOUNTS_PATH)
    except OSError as e:
        logger.warning(f"[MusicUID] 账号池写入失败：{e}")
        tmp.unlink(missing_ok=True)
        return False
    return True


def _migrate_legacy() -> dict[str, list[MusicAccount]]:
    """把旧版网易云专用账号池并入新的分平台结构。"""
    if not LEGACY_NETEASE_PATH.exists():
        return {}
    try:
        raw = json.loads(LEGACY_NETEASE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    accounts = _parse_accounts(raw)
    if not accounts:
        return {}
    migrated = {"netease": accounts}
    if _write_all(migrated):
        logger.info(f"[MusicUID] 已迁移 {len(accounts)} 个网易云账号到新的账号池")
    return migrated


def _read_all() -> dict[str, list[MusicAccount]]:
    """读取所有平台的账号，主文件缺失时尝试迁移旧结构。"""
    if not ACCOUNTS_PATH.exists():
        return _migrate_legacy()
    try:
        raw = json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(f"[MusicUID] 账号池读取失败：{e}")
        return {}
    root = to_obj(raw)
    data: dict[str, list[MusicAccount]] = {}
    for platform in root:
        accounts = _parse_accounts(root[platform])
        if accounts:
            data[platform] = accounts
    return data


def load_accounts(platform: str) -> list[MusicAccount]:
    """读取某个平台的账号。

    Args:
        platform: 平台标识，如 ``netease`` / ``kugou``。

    Returns:
        该平台的账号列表。
    """
    return _read_all().get(platform, [])


def save_account(platform: str, name: str, cookie: str) -> bool:
    """保存一个账号；同平台的同一个账号只保留一条（更新备注名）。

    Args:
        platform: 平台标识。
        name: 备注名，留空则自动编号。
        cookie: 该平台的 Cookie 串或裸凭据值。

    Returns:
        是否保存成功。
    """
    normalized = normalize_cookie(platform, cookie)
    key = account_key(platform, normalized)
    if not key:
        return False
    data = _read_all()
    kept = [item for item in data.get(platform, []) if account_key(platform, item.cookie) != key]
    kept.append(
        MusicAccount(
            name=name.strip() or f"账号{len(kept) + 1}",
            cookie=normalized,
            added_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
    )
    data[platform] = kept
    return _write_all(data)


def remove_account(platform: str, index: int) -> str:
    """按序号删除某个平台的账号。

    Args:
        platform: 平台标识。
        index: 从 1 开始的序号。

    Returns:
        被删除的账号名；序号非法时返回空串。
    """
    data = _read_all()
    accounts = data.get(platform, [])
    if index < 1 or index > len(accounts):
        return ""
    removed = accounts.pop(index - 1)
    data[platform] = accounts
    if not _write_all(data):
        return ""
    return removed.name


def pick_cookie(platform: str) -> str:
    """按轮询取某平台一个账号的 Cookie，用于取流。

    账号池为空时回退到该平台的配置项，保证单账号用户可用。

    Args:
        platform: 平台标识。

    Returns:
        可直接放进请求头的 Cookie 串，没有可用账号时为空串。
    """
    accounts = load_accounts(platform)
    if not accounts:
        config_key = _CONFIG_KEYS.get(platform, "")
        if not config_key:
            return ""
        return normalize_cookie(platform, music_config.get_config(config_key).data)
    index = _cursor.get(platform, 0) % len(accounts)
    _cursor[platform] = (index + 1) % len(accounts)
    return normalize_cookie(platform, accounts[index].cookie)
