"""音乐平台账号登录与凭证管理服务。"""

from __future__ import annotations

import asyncio

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.segment import MessageSegment

from ..utils.login import (
    LoginStatus,
    LoginSession,
    BaseLoginProvider,
    format_qq_cookie,
    get_login_provider,
)
from ..musicuid_config import music_config

sv_login = SV("点歌登录", priority=5)

_POLL_INTERVAL_SEC = 2.5
_MAX_POLL_ROUNDS = 40  # 最多轮询 100 秒
_active_tasks: set[asyncio.Task[None]] = set()


def is_master(ev: Event) -> bool:
    """判断当前事件触发者是否为主人（user_pm <= 1）。"""
    return ev.user_pm <= 1


def get_whitelist() -> list[str]:
    """获取当前配置的登录权限白名单列表。"""
    cfg = music_config.get_config("login_whitelist").data
    if isinstance(cfg, list):
        return [str(u).strip() for u in cfg if str(u).strip()]
    return []


def is_login_authorized(ev: Event) -> bool:
    """判断触发者是否拥有登录及凭证操作权限（主人或白名单用户）。"""
    if is_master(ev):
        return True
    user_id = str(ev.user_id).strip()
    return bool(user_id and user_id in get_whitelist())


def _extract_user_ids(ev: Event) -> list[str]:
    """从消息文本与 @ 列表中提取目标用户 ID。"""
    ids: list[str] = []
    # 提取 at
    if ev.at:
        ids.append(str(ev.at).strip())
    if ev.at_list:
        for at_id in ev.at_list:
            if at_id:
                ids.append(str(at_id).strip())

    # 提取文本中的数字或ID标识
    for token in ev.text.strip().split():
        token = token.strip()
        if token and token.isalnum() and token not in ids:
            ids.append(token)

    return list(dict.fromkeys(ids))


def _mask_cookie(val: str) -> str:
    """对 Cookie 进行脱敏显示。"""
    val = val.strip()
    if not val:
        return "未配置"
    if len(val) <= 12:
        return val[:3] + "***" + val[-3:]
    return val[:6] + "******" + val[-6:]


# ---------------------------------------------------------------- 白名单管理


@sv_login.on_prefix(
    ("点歌添加白名单", "点歌白名单添加", "音乐添加白名单"),
    to_ai="添加用户至音乐登录白名单（仅主人可用）",
)
async def add_whitelist(bot: Bot, ev: Event) -> None:
    """添加用户 ID 到登录权限白名单（仅主人可用）。"""
    if not is_master(ev):
        await bot.send("❌ 权限不足：只有主人才能管理登录白名单。")
        return

    target_ids = _extract_user_ids(ev)
    if not target_ids:
        await bot.send(
            "请指定要添加的用户 ID 或 @用户，例如：\n"
            "• 点歌添加白名单 12345678\n"
            "• @用户 点歌添加白名单"
        )
        return

    current = get_whitelist()
    added: list[str] = []
    for uid in target_ids:
        if uid not in current:
            current.append(uid)
            added.append(uid)

    if not added:
        await bot.send(f"指定的用户已在白名单中：{', '.join(target_ids)}")
        return

    music_config.set_config("login_whitelist", current)
    logger.info(f"[MusicUID] 主人 {ev.user_id} 添加了登录白名单用户: {added}")
    await bot.send(f"✅ 已成功将用户【{', '.join(added)}】加入点歌登录白名单。")


@sv_login.on_prefix(
    ("点歌删除白名单", "点歌白名单删除", "音乐删除白名单"),
    to_ai="从音乐登录白名单移除用户（仅主人可用）",
)
async def remove_whitelist(bot: Bot, ev: Event) -> None:
    """从登录权限白名单中移除用户（仅主人可用）。"""
    if not is_master(ev):
        await bot.send("❌ 权限不足：只有主人才能管理登录白名单。")
        return

    target_ids = _extract_user_ids(ev)
    if not target_ids:
        await bot.send(
            "请指定要移除的用户 ID 或 @用户，例如：\n"
            "• 点歌删除白名单 12345678\n"
            "• @用户 点歌删除白名单"
        )
        return

    current = get_whitelist()
    removed: list[str] = []
    for uid in target_ids:
        if uid in current:
            current.remove(uid)
            removed.append(uid)

    if not removed:
        await bot.send(f"指定的用户未在白名单中：{', '.join(target_ids)}")
        return

    music_config.set_config("login_whitelist", current)
    logger.info(f"[MusicUID] 主人 {ev.user_id} 移除了登录白名单用户: {removed}")
    await bot.send(f"✅ 已从点歌登录白名单移除用户【{', '.join(removed)}】。")


@sv_login.on_fullmatch(
    ("点歌白名单", "点歌白名单列表", "音乐白名单"),
    to_ai="查看音乐登录白名单列表（仅主人及白名单可用）",
)
async def list_whitelist(bot: Bot, ev: Event) -> None:
    """查看当前配置的登录权限白名单。"""
    if not is_login_authorized(ev):
        await bot.send("❌ 权限不足：仅主人与授权白名单用户可查看。")
        return

    whitelist = get_whitelist()
    if not whitelist:
        await bot.send("【MusicUID 登录白名单】\n当前列表为空（仅机器人主人具备登录与凭据配置权限）。")
        return

    items = "\n".join(f"• {uid}" for uid in whitelist)
    await bot.send(f"【MusicUID 登录白名单】\n{items}\n\n💡 提示：主人拥有默认最高权限。")


# ---------------------------------------------------------------- 登录与凭据


@sv_login.on_fullmatch(("点歌登录状态", "音乐登录状态"), to_ai="查询音乐平台登录状态与凭据配置")
async def check_login_status(bot: Bot, ev: Event) -> None:
    """查询当前音乐平台的 Cookie 与登录凭据配置状态。"""
    if not is_login_authorized(ev):
        await bot.send("❌ 权限不足：仅主人及登录白名单用户可查询凭据状态。")
        return

    netease_ck = music_config.get_config("netease_cookie").data
    qq_ck = music_config.get_config("qqmusic_cookie").data
    kugou_ck = music_config.get_config("kugou_cookie").data

    status_text = (
        "【MusicUID 音乐平台凭据状态】\n"
        f"• 网易云音乐: {_mask_cookie(netease_ck)}\n"
        f"• QQ 音乐: {_mask_cookie(qq_ck)}\n"
        f"• 酷狗音乐: {_mask_cookie(kugou_ck)}\n\n"
        "💡 提示：使用【点歌登录 网易云】或【点歌登录 酷狗】可直接扫码更新凭据。"
    )
    await bot.send(status_text)


@sv_login.on_prefix(
    (
        "点歌登录",
        "网易云登录",
        "酷狗登录",
        "QQ登录",
        "QQ音乐登录",
        "点歌导入cookie",
        "点歌绑定",
    ),
    to_ai="音乐平台扫码登录或绑定Cookie凭据",
)
async def handle_login(bot: Bot, ev: Event) -> None:
    """处理音乐平台二维码登录与 Cookie 导入指令。

    支持网易云音乐、酷狗音乐扫码登录，以及各平台 Cookie 手动导入绑定。
    """
    if not is_login_authorized(ev):
        await bot.send("❌ 权限不足：仅主人及登录白名单用户可进行平台登录与凭据配置。")
        return

    raw_text = ev.text.strip()
    cmd = ev.command.strip()

    # 1. 处理直接导入 Cookie 的情况
    if cmd in ("点歌导入cookie", "点歌绑定") or " " in raw_text:
        parts = raw_text.split(maxsplit=1)
        if len(parts) >= 2:
            plat_keyword, cookie_val = parts[0], parts[1]
            provider = get_login_provider(plat_keyword)
            if provider:
                config_key = f"{provider.platform_name}_cookie"
                if provider.platform_name == "qq":
                    cookie_val = format_qq_cookie(cookie_val)
                    config_key = "qqmusic_cookie"

                music_config.set_config(config_key, cookie_val)
                logger.info(f"[MusicUID] 已成功更新 {provider.display_name} Cookie")
                await bot.send(f"已成功更新【{provider.display_name}】Cookie！")
                return

    # 2. 判断目标平台
    target_platform = "netease"
    if "酷狗" in cmd or "kugou" in raw_text.lower():
        target_platform = "kugou"
    elif "qq" in cmd.lower() or "qq" in raw_text.lower():
        target_platform = "qq"
    elif "网易" in cmd or "netease" in raw_text.lower() or "163" in raw_text.lower():
        target_platform = "netease"
    elif raw_text:
        target_platform = raw_text.split()[0]

    provider = get_login_provider(target_platform)
    if not provider:
        await bot.send(
            "请指定要登录的平台：\n"
            "• 点歌登录 网易云\n"
            "• 点歌登录 酷狗\n"
            "• 点歌登录 QQ"
        )
        return

    # QQ 音乐由于协议风控，直接展示引导与二维码
    if provider.platform_name == "qq":
        session = await provider.create_qr_session()
        msg = MessageSegment.image(session.qr_bytes) + MessageSegment.text(session.message)
        await bot.send(msg)
        return

    # 3. 创建网易云/酷狗扫码会话
    await bot.send(f"正在生成【{provider.display_name}】登录二维码，请稍候...")
    session = await provider.create_qr_session()

    if session.status == LoginStatus.FAILED or not session.qr_bytes:
        await bot.send(f"生成二维码失败：{session.message}")
        return

    # 发送二维码与扫码提示
    msg = MessageSegment.image(session.qr_bytes) + MessageSegment.text(
        f"\n{session.message}\n（有效期约 2 分钟，请尽快确认）"
    )
    await bot.send(msg)

    # 4. 启动后台轮询任务
    task = asyncio.create_task(_poll_login_status(bot, ev, provider, session))
    _active_tasks.add(task)
    task.add_done_callback(_active_tasks.discard)


async def _poll_login_status(
    bot: Bot,
    ev: Event,
    provider: BaseLoginProvider,
    session: LoginSession,
) -> None:
    """后台轮询扫码状态并在成功时自动保存凭证。"""
    notified_scanned = False

    for _ in range(_MAX_POLL_ROUNDS):
        await asyncio.sleep(_POLL_INTERVAL_SEC)
        try:
            session = await provider.check_qr_status(session)
        except Exception as e:
            logger.debug(f"[MusicUID] 轮询 {provider.display_name} 登录状态异常: {e}")
            continue

        if session.status == LoginStatus.SCANNED and not notified_scanned:
            notified_scanned = True
            logger.info(f"[MusicUID] 用户已扫描 {provider.display_name} 二维码，等待手机确认")

        elif session.status == LoginStatus.SUCCESS:
            config_key = f"{provider.platform_name}_cookie"
            music_config.set_config(config_key, session.cookie)
            logger.info(f"[MusicUID] {provider.display_name} 扫码登录成功，凭证已保存")
            success_tip = (
                f"🎉【{provider.display_name}】扫码登录成功！\n"
                f"• 用户: {session.nickname or '已登录'}\n"
                "• 状态: 凭据已自动保存并实时生效，现在点歌可畅享 VIP 与高音质！"
            )
            await bot.send(success_tip)
            return

        elif session.status == LoginStatus.EXPIRED:
            logger.info(f"[MusicUID] {provider.display_name} 二维码已过期")
            await bot.send(f"【{provider.display_name}】登录二维码已过期，如需登录请重新发起。")
            return

        elif session.status == LoginStatus.FAILED:
            logger.warning(f"[MusicUID] {provider.display_name} 登录失败: {session.message}")
            await bot.send(f"【{provider.display_name}】登录异常：{session.message}")
            return

    # 超时退出
    logger.info(f"[MusicUID] {provider.display_name} 扫码轮询超时已结束")
