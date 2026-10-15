"""新账号的 VIP 校验：没 VIP 的只挂起，不参与取流。

网易云的 ``nuser/account/get`` 一次就能拿到 ``vipType`` 与昵称，所以新登录的
账号入库前先问一次，避免免费号混进轮换池。校验本身可能因网络异常而拿不到结论，
这时返回的第三项为 ``False``：调用方要按「暂时无法判定」处理，不能据此挂起账号。
"""

from __future__ import annotations

from gsuid_core.logger import logger

from .http import USER_AGENT, MusicRequestError, post_json
from .json_tools import to_obj, get_int, get_str
from .provider.netease_crypto import weapi_form, weapi_cookie

_VIP_URL = "https://music.163.com/weapi/nuser/account/get"


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
