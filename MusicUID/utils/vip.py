"""新账号的 VIP 校验：没 VIP 的只挂起，不参与取流。

网易云的 ``nuser/account/get`` 一次就能拿到 ``vipType`` 与昵称，所以新登录的
账号入库前先问一次，避免免费号混进轮换池。校验本身可能因网络异常而拿不到结论，
这时返回的第三项为 ``False``：调用方要按「暂时无法判定」处理，不能据此挂起账号。

QQ 音乐没有公开的会员查询接口（``musicu.fcg`` 的用户模块全部返回 500003），
所以改用行为判定：拿一支付费专享曲目去取 320kbps，匿名与无会员账号一律
``104003``，有会员才下发地址。
"""

from __future__ import annotations

from typing import Final

from gsuid_core.logger import logger

from .http import USER_AGENT, MusicRequestError, post_json
from .json_tools import to_obj, get_int, get_obj, get_str, get_list
from .provider.netease_crypto import weapi_form, weapi_cookie

_VIP_URL = "https://music.163.com/weapi/nuser/account/get"

_QQ_MUSICU_URL: Final[str] = "https://u.y.qq.com/cgi-bin/musicu.fcg"
# 探针曲目：周杰伦《晴天》，付费专享。曲目若失效会返回 101404，
# 判定随之变成「无法判定」，不会把好号误挂起。
_QQ_PROBE_SONGMID: Final[str] = "0039MnYb0qxYhV"
# 取流返回该 code 表示账号没有权限（未登录 / 非会员）
_QQ_LOGIN_REQUIRED: Final[int] = 104003


async def check_netease_vip(cookie: str) -> tuple[bool, str, bool]:
    """检查网易云账号是否具备 VIP。

    Args:
        cookie: 完整 Cookie 串或裸 MUSIC_U 值。

    Returns:
        ``(是否有 VIP, 账号昵称, 检查是否成功)``。第三项为 ``False`` 表示没问到
        结论（凭据不可用或网络异常），此时不要把账号当成「无 VIP」而挂起。
    """
    header = weapi_cookie(cookie)
    if not header:
        return False, "", False
    headers = {
        "Cookie": header,
        "Referer": "https://music.163.com/",
        "User-Agent": USER_AGENT,
    }
    try:
        data = await post_json(_VIP_URL, weapi_form({}), headers=headers)
    except MusicRequestError as e:
        logger.warning(f"[MusicUID] 网易云 VIP 校验请求失败：{e}")
        return False, "", False
    body = to_obj(data)
    if get_int(body, "code", -1) != 200:
        return False, "", False
    account = to_obj(body.get("account"))
    profile = to_obj(body.get("profile"))
    # vipType > 0 是会员等级；paidFee 是付费标记，任一为真都算有 VIP
    vip = get_int(account, "vipType", 0) > 0 or account.get("paidFee") is True
    return vip, get_str(profile, "nickname"), True


async def check_qq_vip(cookie: str) -> tuple[bool, str, bool]:
    """检查 QQ 音乐账号是否具备会员权限。

    用付费专享曲目的 320kbps 取流结果反推：匿名、伪造凭据、无会员账号都返回
    ``104003``，只有会员才会下发直链。歌曲本身不存在时返回 ``101404``，此时
    算「无法判定」而不是「无会员」。

    Args:
        cookie: 账号池里的 QQ 音乐网页 Cookie。

    Returns:
        ``(是否有会员, 昵称, 检查是否成功)``。取流接口不返回昵称，第二项恒为
        空串；第三项为 ``False`` 表示没问到结论，此时不要把账号挂起。
    """
    from .accounts import cookie_value_of

    uin = cookie_value_of(cookie, "uin")
    if not uin:
        return False, "", False
    headers = {
        "Cookie": cookie,
        "Referer": "https://y.qq.com/portal/player.html",
        "User-Agent": USER_AGENT,
    }
    body = {
        "req_1": {
            "module": "vkey.GetVkeyServer",
            "method": "CgiGetVkey",
            "param": {
                "filename": [f"M800{_QQ_PROBE_SONGMID}{_QQ_PROBE_SONGMID}.mp3"],
                "guid": "10000",
                "songmid": [_QQ_PROBE_SONGMID],
                "songtype": [0],
                "uin": uin,
                "loginflag": 1,
                "platform": "20",
            },
        },
        "loginUin": uin,
        "comm": {"uin": uin, "format": "json", "ct": 24, "cv": 0},
    }
    try:
        payload = await post_json(_QQ_MUSICU_URL, body, headers=headers, as_json=True)
    except MusicRequestError as e:
        logger.warning(f"[MusicUID] QQ 音乐会员校验请求失败：{e}")
        return False, "", False
    infos = get_list(get_obj(get_obj(to_obj(payload), "req_1"), "data"), "midurlinfo")
    if not infos:
        return False, "", False
    info = to_obj(infos[0])
    if get_str(info, "purl"):
        return True, "", True
    if get_int(info, "result", 0) == _QQ_LOGIN_REQUIRED:
        return False, "", True
    return False, "", False
