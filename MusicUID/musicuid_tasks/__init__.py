"""网易云音乐每日任务：签到与云贝领取。

只做「签到」和「领取已完成任务的云贝」这两类点击动作，不伪造播放上报去刷
听歌时长/等级——后者是平台风控最严的部分，收益也不值得拿账号去换。
"""

from __future__ import annotations

from dataclasses import dataclass

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event

from ..utils.http import USER_AGENT, MusicRequestError, post_json
from ..musicuid_login import is_login_authorized
from ..musicuid_config import music_config
from ..utils.json_tools import to_obj, get_int, get_str, get_list
from ..utils.provider.netease_crypto import weapi_form, weapi_cookie

sv_task = SV("音乐任务", priority=3)

_REFERER = "https://music.163.com/"
# type=1 是 Web 端签到：实测 type=0（Android）已被官方关闭，返回「功能暂不支持」
_SIGN_URL = "https://music.163.com/weapi/point/dailyTask"
# 待领取列表：已完成的每日任务会带着 userTaskId 出现在这里
_TODO_URL = "https://music.163.com/weapi/usertool/task/todo/query"
_RECEIVE_URL = "https://music.163.com/weapi/usertool/task/point/receive"

# 签到与领取的日上限，避免异常重试把接口打爆
_MAX_CLAIM_PER_RUN = 20


@dataclass(frozen=True, slots=True)
class ClaimedTask:
    """一个已领取的任务奖励。

    Attributes:
        name: 任务名。
        point: 领取到的云贝数。
    """

    name: str
    point: int


@dataclass(frozen=True, slots=True)
class NeteaseTaskResult:
    """一次网易云每日任务的执行结果。

    Attributes:
        ok: 是否成功跑完（网络与凭据正常）。
        sign_point: 签到得到的云贝，``0`` 表示没签到或已签过。
        sign_message: 签到环节的可读回执。
        claimed: 本次领取到的任务奖励。
        claimed_known: 是否成功读到了待领取列表。
    """

    ok: bool
    sign_point: int
    sign_message: str
    claimed: tuple[ClaimedTask, ...]
    claimed_known: bool


async def _post_weapi(url: str, payload: dict[str, object], cookie: str) -> dict[str, object]:
    """发一个 weapi 请求并把响应收窄成字典。

    Args:
        url: 完整接口地址。
        payload: 明文请求体，由 :func:`weapi_form` 加密。
        cookie: 已拼好的 Cookie 头。

    Returns:
        收窄后的响应对象，非对象响应返回空字典。

    Raises:
        MusicRequestError: 请求失败或响应不是 JSON。
    """
    headers = {"Referer": _REFERER, "User-Agent": USER_AGENT, "Cookie": cookie}
    return to_obj(await post_json(url, weapi_form(payload), headers=headers))


async def _sign(cookie: str) -> tuple[int, str]:
    """执行每日签到。

    Args:
        cookie: 已拼好的 Cookie 头。

    Returns:
        ``(云贝数, 可读回执)``；重复签到时云贝数为 0。
    """
    data = await _post_weapi(_SIGN_URL, {"type": 1}, cookie)
    code = get_int(data, "code", -1)
    point = get_int(data, "point", 0)
    if point > 0:
        return point, f"签到成功，云贝 +{point}"
    raw_msg = get_str(data, "msg") or get_str(data, "message")
    # 服务端把「今天已签过」也叫重复签到，原样透出去容易被当成失败
    if "重复" in raw_msg or "已签" in raw_msg:
        return 0, "今天已经签过了，明天再来"
    if code == 200:
        return 0, raw_msg or "签到完成，但服务端没有返回云贝数"
    return 0, raw_msg or f"签到失败（code={code}）"


async def _claim(cookie: str) -> tuple[ClaimedTask, ...]:
    """领取已完成任务的云贝。

    Args:
        cookie: 已拼好的 Cookie 头。

    Returns:
        本次领取到的奖励列表。
    """
    data = await _post_weapi(_TODO_URL, {}, cookie)
    claimed: list[ClaimedTask] = []
    for raw in get_list(data, "data")[:_MAX_CLAIM_PER_RUN]:
        item = to_obj(raw)
        user_task_id = get_int(item, "userTaskId", 0)
        if user_task_id <= 0:
            continue
        result = await _post_weapi(
            _RECEIVE_URL,
            {
                "userTaskId": user_task_id,
                "depositCode": get_int(item, "depositCode", 0),
            },
            cookie,
        )
        receipt = to_obj(result)
        # code=200 也可能是空领：服务端用 data=false 表示这次没真的给云贝
        if get_int(receipt, "code", -1) != 200 or receipt.get("data") is False:
            continue
        claimed.append(
            ClaimedTask(
                name=get_str(item, "taskName", "未知任务"),
                point=get_int(item, "taskPoint", 0),
            )
        )
    return tuple(claimed)


def _account_targets() -> list[tuple[str, str]]:
    """列出要跑任务的账号。

    账号池为空时回退到配置项里的单个账号，保证老用户不受影响。

    Returns:
        ``[(备注名, Cookie 头), ...]``；没有可用凭据时为空列表。
    """
    from ..utils.accounts import load_accounts

    accounts = load_accounts()
    if accounts:
        return [(item.name, weapi_cookie(item.cookie)) for item in accounts]
    fallback = weapi_cookie(music_config.get_config("netease_cookie").data)
    return [("默认账号", fallback)] if fallback else []


async def _run_for_cookie(cookie: str) -> NeteaseTaskResult:
    """对单个凭据执行签到与云贝领取。

    Args:
        cookie: 已拼好的 Cookie 头。

    Returns:
        该账号的执行结果。
    """
    try:
        point, message = await _sign(cookie)
        claimed = await _claim(cookie)
    except MusicRequestError as e:
        return NeteaseTaskResult(False, 0, f"请求失败：{e}", (), False)
    return NeteaseTaskResult(True, point, message, claimed, True)


async def run_netease_daily_task() -> NeteaseTaskResult:
    """执行一次网易云签到与云贝领取（单账号，用第一个可用凭据）。

    Returns:
        本次执行结果；网络或凭据异常时 ``ok`` 为 ``False``。
    """
    targets = _account_targets()
    if not targets:
        return NeteaseTaskResult(False, 0, "未配置网易云 Cookie", (), False)
    return await _run_for_cookie(targets[0][1])


async def run_all_netease_tasks() -> list[tuple[str, NeteaseTaskResult]]:
    """对账号池里的每个账号各跑一次签到与云贝领取。

    Returns:
        ``[(备注名, 结果), ...]``；没有可用账号时为空列表。
    """
    return [(name, await _run_for_cookie(cookie)) for name, cookie in _account_targets()]


def format_task_report(result: NeteaseTaskResult) -> str:
    """把执行结果拼成给用户看的回执。

    Args:
        result: :func:`run_netease_daily_task` 的返回值。

    Returns:
        多行文本。
    """
    lines = ["【网易云每日任务】", f"• 签到：{result.sign_message}"]
    if not result.claimed_known:
        lines.append("• 云贝领取：未能读取待领取列表")
    elif result.claimed:
        total = sum(item.point for item in result.claimed)
        lines.append(f"• 云贝领取：{len(result.claimed)} 项，共 +{total}")
        lines.extend(f"   - {item.name} +{item.point}" for item in result.claimed)
    else:
        lines.append("• 云贝领取：暂无可领取的云贝")
        lines.append("  （听歌时长类任务只认网易云客户端播放，机器人点歌不计入）")
    lines.append("")
    lines.append("💡 只做签到与领取，不刷听歌时长等动作")
    return "\n".join(lines)


async def notify_task_result(target: str, text: str) -> bool:
    """把任务结果推送到配置的会话。

    定时任务是静默的：不推送的话用户根本看不到它在跑，所以结果要发出来。

    Args:
        target: 通知目标，``群号`` 或 ``适配器:群号``。
        text: 要发送的内容。

    Returns:
        是否成功发出。
    """
    cleaned = target.strip()
    if not cleaned:
        return False

    adapter, _, target = cleaned.partition(":")
    if not target:
        adapter, target = "", adapter
    # p: 前缀表示私聊，其余按群号处理
    is_private = target.startswith("p:")
    chat_id = target[2:] if is_private else target

    from gsuid_core.gss import gss

    if not gss.active_bot:
        logger.warning("[MusicUID] 签到结果未推送：当前没有已连接的适配器")
        return False
    if adapter:
        if adapter not in gss.active_bot:
            logger.warning(f"[MusicUID] 签到结果未推送：适配器 {adapter} 未连接")
            return False
        bot_id = adapter
    else:
        bot_id = next(iter(gss.active_bot))

    event = Event(
        bot_id=bot_id,
        user_id=chat_id if is_private else "",
        group_id="" if is_private else chat_id,
        user_type="direct" if is_private else "group",
        real_bot_id=bot_id,
        WS_BOT_ID=bot_id,
    )
    try:
        await Bot(gss.active_bot[bot_id], event).send(text)
    except Exception as e:  # noqa: BLE001 - 推送失败不能影响任务本身
        logger.warning(f"[MusicUID] 签到结果推送失败：{e}")
        return False
    return True


def format_multi_report(results: list[tuple[str, NeteaseTaskResult]]) -> str:
    """把多账号的执行结果拼成一条回执。

    Args:
        results: :func:`run_all_netease_tasks` 的返回值。

    Returns:
        多行文本；只有一个账号时复用单账号回执。
    """
    if not results:
        return "❌ 未配置网易云 Cookie，无法执行每日任务。"
    if len(results) == 1:
        return format_task_report(results[0][1])
    lines = [f"【网易云每日任务】共 {len(results)} 个账号"]
    for name, result in results:
        if not result.ok:
            lines.append(f"• {name}：{result.sign_message}")
            continue
        total = sum(item.point for item in result.claimed)
        lines.append(f"• {name}：{result.sign_message}；领取 {len(result.claimed)} 项 +{total}")
    lines.append("")
    lines.append("💡 只做签到与领取，不刷听歌时长等动作")
    return "\n".join(lines)


async def auto_daily_task_job() -> None:
    """定时执行网易云每日任务（开关关闭时直接返回）。"""
    if not music_config.get_config("netease_task_enable").data:
        return
    results = await run_all_netease_tasks()
    if not results:
        logger.warning("[MusicUID] 网易云每日任务跳过：没有可用账号")
        return
    failed = [name for name, item in results if not item.ok]
    if failed:
        logger.warning(f"[MusicUID] 网易云每日任务部分失败：{'、'.join(failed)}")
    logger.info(f"[MusicUID] 网易云每日任务完成：{len(results)} 个账号")

    target = music_config.get_config("netease_task_notify").data
    if target:
        await notify_task_result(target, format_multi_report(results))


@sv_task.on_fullmatch(
    ("音乐签到", "点歌签到", "网易云签到", "点歌任务", "音乐任务"),
    block=True,
    to_ai="执行网易云每日签到并领取云贝",
)
async def handle_daily_task(bot: Bot, ev: Event) -> None:
    """手动执行网易云签到与云贝领取。"""
    if not is_login_authorized(ev):
        await bot.send("❌ 权限不足：仅主人及登录白名单用户可执行音乐任务。")
        return
    await bot.send("正在执行网易云签到与云贝领取...")
    report = format_multi_report(await run_all_netease_tasks())
    await bot.send(report)
    # 手动执行也同步到通知目标，方便在别的会话里确认结果（同群不重复发）
    target = music_config.get_config("netease_task_notify").data
    if target and target != ev.group_id:
        await notify_task_result(target, report)
