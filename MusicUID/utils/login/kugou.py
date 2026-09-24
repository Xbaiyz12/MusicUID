"""酷狗音乐扫码登录实现。"""

from __future__ import annotations

import time
import base64
import hashlib
from typing import Any

import httpx

from .base import LoginStatus, LoginSession, BaseLoginProvider, make_qr_image

_SALT = "NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt"
_QR_URL = "https://login-user.kugou.com/v2/qrcode"
_CHECK_URL = "https://login-user.kugou.com/v2/get_userinfo_qrcode"
_H5_QR_PREFIX = "https://h5.kugou.com/apps/loginQRCode/html/index.html?qrcode="

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.kugou.com/",
}


def _signature_web_params(params: dict[str, Any]) -> str:
    """计算酷狗 Web 端参数签名。"""
    items = [f"{k}={v}" for k, v in params.items()]
    items.sort()
    params_str = "".join(items)
    return hashlib.md5(f"{_SALT}{params_str}{_SALT}".encode("utf-8")).hexdigest()


class KugouLoginProvider(BaseLoginProvider):
    """酷狗音乐二维码登录提供者。"""

    platform_name = "kugou"
    display_name = "酷狗音乐"

    async def create_qr_session(self) -> LoginSession:
        """生成酷狗扫码登录会话。"""
        now = int(time.time())
        params = {
            "appid": 1014,
            "type": 1,
            "plat": 4,
            "qrcode_txt": "https://h5.kugou.com/apps/loginQRCode/html/index.html?appid=1014&",
            "srcappid": 2919,
            "clienttime": now,
            "clientver": 20489,
            "dfid": "-",
            "mid": "12345678901234567890123456789012",
            "uuid": "-",
        }
        params["signature"] = _signature_web_params(params)

        async with httpx.AsyncClient(headers=_HEADERS, timeout=10.0) as client:
            resp = await client.get(_QR_URL, params=params)
            res_json = resp.json()
            data = res_json.get("data") or {}
            qrcode = data.get("qrcode", "")
            qrcode_img = data.get("qrcode_img", "")

            if not qrcode:
                return LoginSession(
                    provider_name=self.platform_name,
                    key="",
                    qr_url="",
                    qr_bytes=b"",
                    status=LoginStatus.FAILED,
                    message="获取酷狗二维码失败",
                )

            qr_bytes = b""
            if qrcode_img and qrcode_img.startswith("data:image/"):
                try:
                    _, b64_data = qrcode_img.split(",", 1)
                    qr_bytes = base64.b64decode(b64_data)
                except Exception:
                    pass

            qr_url = f"{_H5_QR_PREFIX}{qrcode}"
            if not qr_bytes:
                qr_bytes = make_qr_image(qr_url)

            return LoginSession(
                provider_name=self.platform_name,
                key=qrcode,
                qr_url=qr_url,
                qr_bytes=qr_bytes,
                status=LoginStatus.WAITING,
                message="请使用【酷狗音乐 APP】扫码并确认登录",
            )

    async def check_qr_status(self, session: LoginSession) -> LoginSession:
        """检查酷狗二维码扫码状态。"""
        if not session.key:
            session.status = LoginStatus.FAILED
            session.message = "无效的二维码会话"
            return session

        now = int(time.time())
        params = {
            "plat": 4,
            "appid": 1014,
            "srcappid": 2919,
            "qrcode": session.key,
            "clienttime": now,
            "clientver": 20489,
            "dfid": "-",
            "mid": "12345678901234567890123456789012",
            "uuid": "-",
        }
        params["signature"] = _signature_web_params(params)

        async with httpx.AsyncClient(headers=_HEADERS, timeout=10.0) as client:
            resp = await client.get(_CHECK_URL, params=params)
            res_json = resp.json()
            data = res_json.get("data") or {}
            status_code = data.get("status")

            # 1: 等待扫码, 2: 已扫码待确认, 3: 二维码失效, 4: 登录成功
            if status_code == 1:
                session.status = LoginStatus.WAITING
                session.message = "等待扫码中..."
            elif status_code == 2:
                session.status = LoginStatus.SCANNED
                session.message = "已扫码，请在手机上确认登录"
            elif status_code == 3:
                session.status = LoginStatus.EXPIRED
                session.message = "二维码已失效，请重新发起登录"
            elif status_code == 4:
                session.status = LoginStatus.SUCCESS
                token = data.get("token", "")
                userid = data.get("userid", 0)
                session.nickname = data.get("nickname", "") or data.get("username", "")
                session.cookie = f"token={token}; userid={userid}"
                session.message = "酷狗登录成功！"
            else:
                session.status = LoginStatus.FAILED
                session.message = res_json.get("error_msg") or f"未知状态: {status_code}"

            return session
