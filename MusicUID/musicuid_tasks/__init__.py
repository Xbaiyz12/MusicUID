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
    if code == 200 and point > 0:
        return point, f"签到成功，云贝 +{point}"
    if code == 200:
        return 0, "今天已经签到过了"
    message = get_str(data, "msg") or get_str(data, "message") or f"签到失败（code={code}）"
    return 0, message


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
        if get_int(result, "code", -1) != 200:
            continue
        claimed.append(
            ClaimedTask(
                name=get_str(item, "taskName", "未知任务"),
                point=get_int(item, "taskPoint", 0),
            )
        )
    return tuple(claimed)


async def run_netease_daily_task() -> NeteaseTaskResult:
    """执行一次网易云签到与云贝领取。

    Returns:
        本次执行结果；网络或凭据异常时 ``ok`` 为 ``False``。
    """
    cookie = weapi_cookie(music_config.get_config("netease_cookie").data)
    if not cookie:
        return NeteaseTaskResult(False, 0, "未配置网易云 Cookie", (), False)
    try:
        point, message = await _sign(cookie)
        claimed = await _claim(cookie)
    except MusicRequestError as e:
        return NeteaseTaskResult(False, 0, f"请求失败：{e}", (), False)
    return NeteaseTaskResult(True, point, message, claimed, True)


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
    lines.append("")
    lines.append("💡 只做签到与领取，不刷听歌时长等动作")
    return "\n".join(lines)


async def auto_daily_task_job() -> None:
    """定时执行网易云每日任务（开关关闭时直接返回）。"""
    if not music_config.get_config("netease_task_enable").data:
        return
    result = await run_netease_daily_task()
    if not result.ok:
        logger.warning(f"[MusicUID] 网易云每日任务失败：{result.sign_message}")
        return
    logger.info(f"[MusicUID] 网易云每日任务完成：{result.sign_message}，领取 {len(result.claimed)} 项")


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
    result = await run_netease_daily_task()
    await bot.send(format_task_report(result))
