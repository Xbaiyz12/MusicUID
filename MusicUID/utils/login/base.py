"""登录 Provider 基类与数据模型。"""

from __future__ import annotations

import io
from abc import ABC, abstractmethod
from enum import Enum
from dataclasses import dataclass

import qrcode


class LoginStatus(str, Enum):
    """扫码登录状态。"""

    WAITING = "waiting"  # 等待扫码
    SCANNED = "scanned"  # 已扫码待确认
    SUCCESS = "success"  # 登录成功
    EXPIRED = "expired"  # 二维码已过期
    FAILED = "failed"  # 异常失败


@dataclass
class LoginSession:
    """登录会话对象。"""

    provider_name: str
    key: str
    qr_url: str
    qr_bytes: bytes
    status: LoginStatus = LoginStatus.WAITING
    cookie: str = ""
    nickname: str = ""
    avatar_url: str = ""
    message: str = ""
    extra_data: dict | None = None


def make_qr_image(text: str) -> bytes:
    """将文本或 URL 转换成 PNG 图片字节流。

    Args:
        text: 待编码的 URL 或文本。

    Returns:
        PNG 格式的二进制数据。
    """
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=8,
        border=2,
    )
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class BaseLoginProvider(ABC):
    """平台登录提供者基类。"""

    platform_name: str = ""
    display_name: str = ""

    @abstractmethod
    async def create_qr_session(self) -> LoginSession:
        """生成登录二维码会话。"""
        raise NotImplementedError

    @abstractmethod
    async def check_qr_status(self, session: LoginSession) -> LoginSession:
        """轮询二维码扫码状态。"""
        raise NotImplementedError
