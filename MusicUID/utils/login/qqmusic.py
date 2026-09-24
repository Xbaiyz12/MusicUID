"""QQ 音乐凭证与登录辅助实现。"""

from __future__ import annotations

import httpx

from .base import LoginStatus, LoginSession, BaseLoginProvider, make_qr_image

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://y.qq.com/",
}


def parse_qq_cookie(cookie_str: str) -> dict[str, str]:
    """解析 QQ 音乐 Cookie 字符串并提取关键字段。"""
    cookies: dict[str, str] = {}
    for item in cookie_str.split(";"):
        if "=" in item:
            k, v = item.strip().split("=", 1)
            cookies[k] = v
    return cookies


def format_qq_cookie(cookie_str: str) -> str:
    """提取 QQ 音乐核心 Cookie 字段 (uin, qm_keyst / qqmusic_key / pskey / skey)。"""
    cookies = parse_qq_cookie(cookie_str)
    keys = ["uin", "qm_keyst", "qqmusic_key", "pskey", "skey", "p_skey", "p_uin"]
    extracted = [f"{k}={cookies[k]}" for k in keys if k in cookies]
    return "; ".join(extracted) if extracted else cookie_str.strip()


class QQMusicLoginProvider(BaseLoginProvider):
    """QQ 音乐登录提供者。

    说明：腾讯登录协议（ptlogin2）近期强化了客户端环境风控拦截。
    因此本模块提供网页登录指引生成及 Cookie 导入与格式化校验。
    """

    platform_name = "qq"
    display_name = "QQ音乐"

    async def create_qr_session(self) -> LoginSession:
        """生成 QQ 音乐登录二维码或指引。"""
        # 生成 QQ 音乐官方登录入口二维码
        qr_url = "https://y.qq.com"
        qr_bytes = make_qr_image(qr_url)
        return LoginSession(
            provider_name=self.platform_name,
            key="guide",
            qr_url=qr_url,
            qr_bytes=qr_bytes,
            status=LoginStatus.WAITING,
            message=(
                "【QQ音乐凭证设置指引】\n"
                "1. 在电脑浏览器打开 https://y.qq.com 并登录\n"
                "2. 按 F12 打开开发者工具 -> 应用(Application) -> Cookie\n"
                "3. 复制 uin 和 qm_keyst 的值\n"
                "4. 发送指令：点歌导入cookie qq uin=你的QQ号; qm_keyst=你的Key"
            ),
        )

    async def check_qr_status(self, session: LoginSession) -> LoginSession:
        """轮询二维码状态。"""
        session.status = LoginStatus.WAITING
        session.message = "QQ音乐建议使用【点歌导入cookie qq ...】直接绑定"
        return session

    @staticmethod
    async def verify_cookie(cookie_str: str) -> tuple[bool, str]:
        """验证 QQ 音乐 Cookie 是否有效。

        Returns:
            (是否有效, 用户昵称或错误信息)
        """
        formatted = format_qq_cookie(cookie_str)
        if not formatted:
            return False, "Cookie 不能为空"

        url = "https://c.y.qq.com/rsc/fcgi-bin/fcg_get_profile_homepage.fcg"
        headers = {**_HEADERS, "Cookie": formatted}
        try:
            async with httpx.AsyncClient(headers=headers, timeout=8.0) as client:
                resp = await client.get(url)
                data = resp.json()
                if data.get("code") == 0:
                    creator = data.get("data", {}).get("creator", {})
                    nick = creator.get("nick") or creator.get("name") or "QQ音乐用户"
                    return True, nick
                return False, data.get("msg") or "Cookie 无效或已过期"
        except Exception as e:
            # 即使无法验证，若格式包含 uin 则允许保存
            if "uin=" in formatted:
                return True, "已导入（无法联网验证）"
            return False, f"请求失败: {e}"
