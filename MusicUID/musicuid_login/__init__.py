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

# 优先级设为 2，优先于通用搜索指令 (priority=5) 匹配
sv_login = SV("点歌登录", priority=2)

_POLL_INTERVAL_SEC = 2.5
_MAX_POLL_ROUNDS = 40  # 最多轮询 100 秒
_active_tasks: set[asyncio.Task[None]] = set()

QQ_COOKIE_GUIDE = (
    "【QQ音乐 Cookie 极简配置教程】\n\n"
    "1. 电脑浏览器打开 QQ音乐官网 (y.qq.com) 并登录账号；\n"
    "2. 按 F12 打开控制台 (Console)，输入 document.cookie 并回车；\n"
    "3. 复制输出内容中的 uin 与 qm_keyst 项；\n"
    "4. 对机器人发送以下指令即可完成绑定：\n"
    "   👉 QQ音乐cookie uin=你的QQ号; qm_keyst=你的密钥值\n\n"
    "💡 网易云与酷狗支持免填 Cookie 扫码登录：\n"
    "• 发送【网易云登录】或【点歌登录 网易云】\n"
    "• 发送【酷狗登录】或【点歌登录 酷狗】"
)

LOGIN_MENU = (
    "🎵【MusicUID 音乐平台登录与凭据配置】\n\n"
    "【📱 手机扫码一键登录（免提取 Cookie）】\n"
    "• 网易云音乐：发送「网易云登录」或「点歌登录 网易云」\n"
    "• 酷狗音乐：发送「酷狗登录」或「点歌登录 酷狗」\n\n"
    "【🔑 Cookie 快捷配置】\n"
    "• QQ 音乐：发送「QQ音乐cookie <值>」（发送「QQ音乐cookie」可查看教程）\n"
    "• 通用设置：发送「设置cookie <平台> <值>」\n\n"
    "【📋 凭证状态与白名单】\n"
    "• 状态查询：发送「点歌状态」或「点歌登录状态」\n"
    "• 权限授权（仅主人）：发送「点歌加白 <用户ID/@用户>」"
)


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
    if ev.at:
        ids.append(str(ev.at).strip())
    if ev.at_list:
        for at_id in ev.at_list:
            if at_id:
                ids.append(str(at_id).strip())

    for token in ev.text.strip().split():
        token = token.strip()
        if token and token.isalnum() and token not in ids:
            ids.append(token)

    return list(dict.fromkeys(ids))


def _mask_cookie(val: object) -> str:
    """对 Cookie 进行脱敏显示。"""
    if not isinstance(val, str):
        return "未配置"
    val = val.strip()
    if not val:
        return "未配置"
    if len(val) <= 12:
        return val[:3] + "***" + val[-3:]
    return val[:6] + "******" + val[-6:]


# ---------------------------------------------------------------- 白名单管理


@sv_login.on_command(
    ("点歌加白", "点歌添加白名单", "点歌白名单添加", "音乐添加白名单"),
    block=True,
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
            "• 点歌加白 12345678\n"
            "• @用户 点歌加白"
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


@sv_login.on_command(
    ("点歌删白", "点歌删除白名单", "点歌白名单删除", "音乐删除白名单"),
    block=True,
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
            "• 点歌删白 12345678\n"
            "• @用户 点歌删白"
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
    block=True,
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
    await bot.send(f"【MusicUID 登录白名单】\n{items}\n\n💡 提示：主人拥有最高权限，可使用「点歌加白 <ID>」授权他人。")


# ---------------------------------------------------------------- 凭据状态查询


@sv_login.on_fullmatch(
    ("点歌状态", "点歌登录状态", "音乐状态", "音乐登录状态"),
    block=True,
    to_ai="查询音乐平台登录状态与凭据配置",
)
async def check_login_status(bot: Bot, ev: Event) -> None:
    """查询当前音乐平台的 Cookie 与登录凭据配置状态。"""
    if not is_login_authorized(ev):
        await bot.send("❌ 权限不足：仅主人及登录白名单用户可查询凭据状态。")
        return

    netease_ck = music_config.get_config("netease_cookie").data
    qq_ck = music_config.get_config("qqmusic_cookie").data
    kugou_ck = music_config.get_config("kugou_cookie").data

    status_text = (
        "【MusicUID 音乐平台凭据状态】\n\n"
        f"• 🔴 网易云音乐: {_mask_cookie(netease_ck)}\n"
        f"• 🟢 QQ 音乐: {_mask_cookie(qq_ck)}\n"
        f"• 🔵 酷狗音乐: {_mask_cookie(kugou_ck)}\n\n"
        "💡 登录与配置方式：\n"
        "• 网易云/酷狗：发送「网易云登录」或「酷狗登录」直接扫码绑定；\n"
        "• QQ音乐：发送「QQ音乐cookie <值>」或直接发送「QQ音乐cookie」查看教程。"
    )
    await bot.send(status_text)


# ---------------------------------------------------------------- Cookie 快捷设置


@sv_login.on_command(
    (
        "qq音乐cookie",
        "qqcookie",
        "qq_cookie",
        "网易云cookie",
        "网易cookie",
        "酷狗cookie",
        "设置cookie",
        "导入cookie",
        "点歌设置cookie",
        "点歌导入cookie",
        "点歌cookie",
        "点歌绑定",
    ),
    block=True,
    to_ai="快捷设置或导入音乐平台Cookie凭证",
)
async def handle_set_cookie(bot: Bot, ev: Event) -> None:
    """处理 Cookie 设置与导入指令。"""
    if not is_login_authorized(ev):
        await bot.send("❌ 权限不足：仅主人及登录白名单用户可配置 Cookie 凭证。")
        return

    cmd = ev.command.strip().lower()
    raw_text = ev.text.strip()

    # 1. 如果是专门平台的快捷指令，如 qq音乐cookie / 网易云cookie / 酷狗cookie
    if cmd in ("qq音乐cookie", "qqcookie", "qq_cookie"):
        if not raw_text:
            await bot.send(QQ_COOKIE_GUIDE)
            return
        formatted = format_qq_cookie(raw_text)
        music_config.set_config("qqmusic_cookie", formatted)
        logger.info("[MusicUID] 已成功更新 QQ 音乐 Cookie")
        await bot.send(f"✅ 已成功更新【QQ 音乐】Cookie 凭据！\n已格式化提取核心票据：\n{_mask_cookie(formatted)}")
        return

    if cmd in ("网易云cookie", "网易cookie"):
        if not raw_text:
            await bot.send(
                "💡 提示：网易云支持扫码免填 Cookie，建议直接发送「网易云登录」！\n"
                "如需手动绑定请发送：网易云cookie <你的Cookie值>"
            )
            return
        music_config.set_config("netease_cookie", raw_text)
        logger.info("[MusicUID] 已成功更新网易云音乐 Cookie")
        await bot.send("✅ 已成功更新【网易云音乐】Cookie 凭据！")
        return

    if cmd == "酷狗cookie":
        if not raw_text:
            await bot.send(
                "💡 提示：酷狗音乐支持扫码免填 Cookie，建议直接发送「酷狗登录」！\n"
                "如需手动绑定请发送：酷狗cookie <你的Cookie值>"
            )
            return
        music_config.set_config("kugou_cookie", raw_text)
        logger.info("[MusicUID] 已成功更新酷狗音乐 Cookie")
        await bot.send("✅ 已成功更新【酷狗音乐】Cookie 凭据！")
        return

    # 2. 通用设置指令：设置cookie / 导入cookie / 点歌设置cookie 等
    parts = raw_text.split(maxsplit=1)
    if len(parts) < 2:
        await bot.send(
            "【Cookie 设置指令说明】\n\n"
            "• 设置 QQ 音乐：QQ音乐cookie <值>（发送「QQ音乐cookie」看教程）\n"
            "• 通用格式：设置cookie <平台名称> <Cookie值>\n"
            "  例如：设置cookie qq uin=123456; qm_keyst=xxxx\n"
            "  例如：设置cookie 网易云 MUSIC_U=xxxx\n\n"
            "💡 网易云与酷狗推荐直接扫码：发送「网易云登录」或「酷狗登录」"
        )
        return

    plat_keyword, cookie_val = parts[0].strip(), parts[1].strip()
    provider = get_login_provider(plat_keyword)
    if not provider:
        await bot.send(f"❌ 未知平台「{plat_keyword}」，支持的平台名称：网易云 (netease)、QQ音乐 (qq)、酷狗 (kugou)")
        return

    config_key = f"{provider.platform_name}_cookie"
    if provider.platform_name == "qq":
        cookie_val = format_qq_cookie(cookie_val)
        config_key = "qqmusic_cookie"

    music_config.set_config(config_key, cookie_val)
    logger.info(f"[MusicUID] 已成功更新 {provider.display_name} Cookie")
    await bot.send(f"✅ 已成功更新【{provider.display_name}】Cookie 凭据！")


# ---------------------------------------------------------------- 扫码登录


@sv_login.on_command(
    (
        "点歌登录",
        "音乐登录",
        "网易云登录",
        "网易登录",
        "酷狗登录",
        "QQ登录",
        "QQ音乐登录",
        "扫码登录",
    ),
    block=True,
    to_ai="音乐平台扫码登录或获取登录二维码",
)
async def handle_login(bot: Bot, ev: Event) -> None:
    """处理音乐平台二维码登录指令。"""
    if not is_login_authorized(ev):
        await bot.send("❌ 权限不足：仅主人及登录白名单用户可进行平台登录与凭据配置。")
        return

    cmd = ev.command.strip().lower()
    raw_text = ev.text.strip().lower()

    # 1. 单独发送「点歌登录」或「音乐登录」且未带平台参数 -> 显示登录引导菜单
    if cmd in ("点歌登录", "音乐登录") and not raw_text:
        await bot.send(LOGIN_MENU)
        return

    # 2. 判断目标平台
    target_platform = "netease"
    if "酷狗" in cmd or "kugou" in raw_text or "kg" in raw_text:
        target_platform = "kugou"
    elif "qq" in cmd or "qq" in raw_text:
        target_platform = "qq"
    elif "网易" in cmd or "netease" in raw_text or "163" in raw_text or "wyy" in raw_text:
        target_platform = "netease"
    elif raw_text:
        target_platform = raw_text.split()[0]

    provider = get_login_provider(target_platform)
    if not provider:
        await bot.send(LOGIN_MENU)
        return

    # QQ 音乐由于协议风控与限制，发送详细教程与提示
    if provider.platform_name == "qq":
        await bot.send(QQ_COOKIE_GUIDE)
        return

    # 3. 创建网易云/酷狗扫码会话
    await bot.send(f"正在生成【{provider.display_name}】登录二维码，请稍候...")
    session = await provider.create_qr_session()

    if session.status == LoginStatus.FAILED or not session.qr_bytes:
        await bot.send(f"❌ 生成二维码失败：{session.message}")
        return

    msg = [
        MessageSegment.image(session.qr_bytes),
        MessageSegment.text(
            f"\n{session.message}\n（二维码有效期约 2 分钟，请打开对应 APP 扫码并确认登录）"
        ),
    ]
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
                "• 状态: 凭证已自动保存并实时生效，现在点歌可畅享 VIP 歌曲与高音质！"
            )
            await bot.send(success_tip)
            return

        elif session.status == LoginStatus.EXPIRED:
            logger.info(f"[MusicUID] {provider.display_name} 二维码已过期")
            await bot.send(f"⌛【{provider.display_name}】登录二维码已过期，请重新发送指令发起登录。")
            return

        elif session.status == LoginStatus.FAILED:
            logger.warning(f"[MusicUID] {provider.display_name} 登录失败：{session.message}")
            await bot.send(f"❌【{provider.display_name}】登录失败：{session.message}")
            return

    logger.info(f"[MusicUID] {provider.display_name} 扫码轮询超时结束")
    await bot.send(f"⌛【{provider.display_name}】登录超时，请重新发送指令发起登录。")
