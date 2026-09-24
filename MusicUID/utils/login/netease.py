"""网易云音乐扫码登录实现。"""

from __future__ import annotations

import httpx

from .base import LoginStatus, LoginSession, BaseLoginProvider, make_qr_image

_UNIKEY_URL = "https://music.163.com/api/login/qrcode/unikey"
_CHECK_URL = "https://music.163.com/api/login/qrcode/client/login"
_QR_PREFIX = "https://music.163.com/login?codekey="

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://music.163.com/",
}


class NeteaseLoginProvider(BaseLoginProvider):
    """网易云音乐二维码登录提供者。"""

    platform_name = "netease"
    display_name = "网易云音乐"

    async def create_qr_session(self) -> LoginSession:
        """生成网易云扫码登录会话。"""
        async with httpx.AsyncClient(headers=_HEADERS, timeout=10.0) as client:
            resp = await client.post(_UNIKEY_URL, data={"type": 1})
            data = resp.json()
            unikey = data.get("unikey", "")
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
            qr_bytes = make_qr_image(qr_url)
            return LoginSession(
                provider_name=self.platform_name,
                key=unikey,
                qr_url=qr_url,
                qr_bytes=qr_bytes,
                status=LoginStatus.WAITING,
                message="请使用【网易云音乐 APP】扫码并确认登录",
            )

    async def check_qr_status(self, session: LoginSession) -> LoginSession:
        """检查网易云二维码扫码状态。"""
        if not session.key:
            session.status = LoginStatus.FAILED
            session.message = "无效的二维码会话"
            return session

        async with httpx.AsyncClient(headers=_HEADERS, timeout=10.0) as client:
            resp = await client.post(_CHECK_URL, data={"key": session.key, "type": 1})
            data = resp.json()
            code = data.get("code")

            if code == 800:
                session.status = LoginStatus.EXPIRED
                session.message = "二维码已过期，请重新发起登录"
            elif code == 801:
                session.status = LoginStatus.WAITING
                session.message = "等待扫码中..."
            elif code == 802:
                session.status = LoginStatus.SCANNED
                session.nickname = data.get("nickname", "")
                session.avatar_url = data.get("avatarUrl", "")
                session.message = "已扫码，请在手机上确认登录"
            elif code == 803:
                session.status = LoginStatus.SUCCESS
                # 优先从响应体中的 cookie 字段或响应头的 Set-Cookie 中提取
                cookie_str = data.get("cookie", "")
                if not cookie_str and resp.cookies:
                    cookie_str = "; ".join(f"{k}={v}" for k, v in resp.cookies.items())

                # 提取关键 MUSIC_U
                cookies_dict: dict[str, str] = {}
                for item in cookie_str.split(";"):
                    if "=" in item:
                        k, v = item.strip().split("=", 1)
                        cookies_dict[k] = v

                music_u = cookies_dict.get("MUSIC_U", "")
                if music_u:
                    session.cookie = f"MUSIC_U={music_u}"
                else:
                    session.cookie = cookie_str

                session.message = "网易云登录成功！"
            else:
                session.status = LoginStatus.FAILED
                session.message = data.get("msg") or data.get("message") or f"未知状态码: {code}"

            return session
