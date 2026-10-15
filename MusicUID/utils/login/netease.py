"""网易云音乐登录：扫码与手机验证码。

登录态只在 weapi 加密请求里生效，所以统一走 weapi；成功后从 Set-Cookie 里
取 MUSIC_U，由调用方存进账号池。
"""

from __future__ import annotations

import httpx

from .base import LoginStatus, LoginSession, BaseLoginProvider, make_qr_image
from ..http import USER_AGENT, get_client
from ..accounts import music_u_of
from ..json_tools import to_obj, get_int, get_str

_REFERER = "https://music.163.com/"
_UNIKEY_PATH = "weapi/login/qrcode/unikey"
_CHECK_PATH = "weapi/login/qrcode/client/login"
_CAPTCHA_PATH = "weapi/sms/captcha/sent"
_LOGIN_PATH = "weapi/login/cellphone"
_QR_PREFIX = "https://music.163.com/login?codekey="


async def _weapi_post(path: str, payload: dict[str, object]) -> tuple[dict[str, object], httpx.Response]:
    """发一个 weapi POST，并把响应对象一并返回（要读 Set-Cookie）。

    Args:
        path: 相对路径，如 ``weapi/login/cellphone``。
        payload: 明文请求体。

    Returns:
        ``(响应体, 响应对象)``。
    """
    # 函数内导入：顶层导入 provider 包会在导入期反向依赖 login，形成循环
    from ..provider.netease_crypto import weapi_form

    headers = {"Referer": _REFERER, "User-Agent": USER_AGENT}
    resp = await get_client().post(f"{_REFERER}{path}", data=weapi_form(payload), headers=headers)
    resp.raise_for_status()
    return to_obj(resp.json()), resp


def _extract_music_u(body: dict[str, object], resp: httpx.Response) -> str:
    """从响应体或 Set-Cookie 里取出 MUSIC_U。

    Args:
        body: 已收窄的响应体。
        resp: 原始响应对象。

    Returns:
        MUSIC_U 的值，没有时返回空串。
    """
    value = music_u_of(get_str(body, "cookie"))
    if value:
        return value
    for raw in resp.headers.get_list("set-cookie"):
        value = music_u_of(raw)
        if value:
            return value
    return ""


class NeteaseLoginProvider(BaseLoginProvider):
    """网易云音乐登录提供者（扫码 + 手机验证码）。"""

    platform_name = "netease"
    display_name = "网易云音乐"

    async def create_qr_session(self) -> LoginSession:
        """生成网易云扫码登录会话。"""
        body, _ = await _weapi_post(_UNIKEY_PATH, {"type": 1})
        unikey = get_str(body, "unikey")
        if not unikey:
            return LoginSession(
                provider_name=self.platform_name,
                key="",
                qr_url="",
                qr_bytes=b"",
                status=LoginStatus.FAILED,
                message="获取网易云二维码密钥失败",
            )
        qr_url = f"{_QR_PREFIX}{unikey}"
        return LoginSession(
            provider_name=self.platform_name,
            key=unikey,
            qr_url=qr_url,
            qr_bytes=make_qr_image(qr_url),
            status=LoginStatus.WAITING,
            message="请使用【网易云音乐 APP】扫码并确认登录",
        )

    async def check_qr_status(self, session: LoginSession) -> LoginSession:
        """检查网易云二维码扫码状态。"""
        if not session.key:
            session.status = LoginStatus.FAILED
            session.message = "无效的二维码会话"
            return session

        body, resp = await _weapi_post(_CHECK_PATH, {"key": session.key, "type": 1})
        code = get_int(body, "code", -1)
        if code == 800:
            session.status = LoginStatus.EXPIRED
            session.message = "二维码已过期，请重新发起登录"
        elif code == 801:
            session.status = LoginStatus.WAITING
            session.message = "等待扫码中..."
        elif code == 802:
            session.status = LoginStatus.SCANNED
            session.nickname = get_str(body, "nickname")
            session.avatar_url = get_str(body, "avatarUrl")
            session.message = "已扫码，请在手机上确认登录"
        elif code == 803:
            music_u = _extract_music_u(body, resp)
            if not music_u:
                session.status = LoginStatus.FAILED
                session.message = "登录成功但没拿到 MUSIC_U，请改用 Cookie 导入"
                return session
            session.status = LoginStatus.SUCCESS
            session.cookie = f"MUSIC_U={music_u}"
            session.message = "网易云登录成功！"
        else:
            session.status = LoginStatus.FAILED
            session.message = get_str(body, "msg") or get_str(body, "message") or f"未知状态码: {code}"
        return session

    async def send_captcha(self, phone: str) -> tuple[bool, str]:
        """给手机号发送登录验证码。

        Args:
            phone: 11 位手机号。

        Returns:
            ``(是否发送成功, 可读提示)``。
        """
        body, _ = await _weapi_post(_CAPTCHA_PATH, {"cellphone": phone, "ctcode": "86"})
        if get_int(body, "code", -1) == 200 and body.get("data") is True:
            return True, f"验证码已发送到 {phone}，5 分钟内有效"
        reason = get_str(body, "msg") or get_str(body, "message") or "发送失败"
        return False, f"验证码发送失败：{reason}"

    async def login_by_captcha(self, phone: str, captcha: str) -> LoginSession:
        """用手机号 + 短信验证码登录。

        Args:
            phone: 11 位手机号。
            captcha: 收到的验证码。

        Returns:
            登录会话；成功时 ``cookie`` 为 ``MUSIC_U=...``。
        """
        session = LoginSession(
            provider_name=self.platform_name,
            key=phone,
            qr_url="",
            qr_bytes=b"",
            status=LoginStatus.WAITING,
            message="正在验证...",
        )
        body, resp = await _weapi_post(
            _LOGIN_PATH,
            {"phone": phone, "countrycode": "86", "captcha": captcha, "rememberLogin": "true"},
        )
        if get_int(body, "code", -1) != 200:
            session.status = LoginStatus.FAILED
            session.message = get_str(body, "msg") or get_str(body, "message") or "验证码登录失败"
            return session
        music_u = _extract_music_u(body, resp)
        if not music_u:
            session.status = LoginStatus.FAILED
            session.message = "登录成功但没拿到 MUSIC_U"
            return session
        session.status = LoginStatus.SUCCESS
        session.cookie = f"MUSIC_U={music_u}"
        session.nickname = get_str(to_obj(body.get("profile")), "nickname")
        session.message = "网易云登录成功！"
        return session
