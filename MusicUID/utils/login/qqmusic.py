"""QQ 音乐凭证、移动端协议登录与自动续期实现。"""

from __future__ import annotations

import re
import json
import time
import uuid
import urllib.parse
from typing import TypedDict

import httpx
from gsuid_core.logger import logger

from .base import LoginStatus, LoginSession, BaseLoginProvider
from ...musicuid_config import music_config
from ..resource.RESOURCE_PATH import QQ_CREDENTIAL_PATH

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://y.qq.com/",
}
_MUSICU_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"


class QQMobileCredential(TypedDict):
    """QQ 音乐移动端登录凭据模型。"""

    openid: str
    refresh_token: str
    access_token: str
    expired_at: int
    musicid: int
    musickey: str
    unionid: str
    str_musicid: str
    refresh_key: str
    musickey_create_time: int
    key_expires_in: int
    login_type: int
    nick: str


def hash33(s: str, h: int = 0) -> int:
    """计算 ptlogin 与 QQ 互联 hash33 值。"""
    for c in s:
        h = (h << 5) + h + ord(c)
    return 2147483647 & h


def load_qq_credential() -> QQMobileCredential | None:
    """读取已保存的 QQ 音乐移动端凭证。"""
    if not QQ_CREDENTIAL_PATH.exists():
        return None
    try:
        content = QQ_CREDENTIAL_PATH.read_text(encoding="utf-8")
        raw: dict[str, object] = json.loads(content)
        musicid = int(str(raw.get("musicid", 0)))
        musickey = str(raw.get("musickey", ""))
        refresh_token = str(raw.get("refresh_token", ""))
        if not musickey or not refresh_token:
            return None
        return QQMobileCredential(
            openid=str(raw.get("openid", "")),
            refresh_token=refresh_token,
            access_token=str(raw.get("access_token", "")),
            expired_at=int(str(raw.get("expired_at", 0))),
            musicid=musicid,
            musickey=musickey,
            unionid=str(raw.get("unionid", "")),
            str_musicid=str(raw.get("str_musicid", "")),
            refresh_key=str(raw.get("refresh_key", "")),
            musickey_create_time=int(str(raw.get("musickey_create_time", 0))),
            key_expires_in=int(str(raw.get("key_expires_in", 0))),
            login_type=int(str(raw.get("login_type", 2))),
            nick=str(raw.get("nick", "")),
        )
    except (OSError, json.JSONDecodeError, ValueError) as e:
        logger.warning(f"[MusicUID] 读取 QQ 音乐移动凭据异常: {e}")
        return None


def save_qq_credential(cred: QQMobileCredential) -> None:
    """保存 QQ 音乐移动端凭据至本地文件。"""
    try:
        QQ_CREDENTIAL_PATH.write_text(
            json.dumps(cred, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.error(f"[MusicUID] 保存 QQ 音乐凭据失败: {e}")


def is_credential_expiring(
    cred: QQMobileCredential, buffer_sec: int = 86400 * 7
) -> bool:
    """判断凭据是否已进入需刷新的临界期（默认剩余小于 7 天）。"""
    create_time = cred["musickey_create_time"]
    expires_in = cred["key_expires_in"]
    if create_time <= 0 or expires_in <= 0:
        return False
    expire_deadline = create_time + expires_in
    return time.time() >= (expire_deadline - buffer_sec)


async def refresh_qq_credential(
    cred: QQMobileCredential | None = None,
) -> tuple[bool, str]:
    """通过移动端协议与 refresh_token/key 刷新 QQ 音乐凭证。"""
    target = cred or load_qq_credential()
    if target is None:
        return False, "未找到已保存的 QQ 音乐移动端凭证"

    payload = {
        "comm": {
            "ct": 11,
            "cv": "12080008",
            "tmeLoginType": target["login_type"],
        },
        "req": {
            "module": "music.login.LoginServer",
            "method": "Login",
            "param": {
                "openid": target["openid"],
                "access_token": target["access_token"],
                "refresh_token": target["refresh_token"],
                "expired_in": target["expired_at"],
                "musicid": target["musicid"],
                "musickey": target["musickey"],
                "refresh_key": target["refresh_key"],
                "loginMode": 2,
            },
        },
    }

    try:
        async with httpx.AsyncClient(headers=_HEADERS, timeout=12.0) as client:
            resp = await client.post(_MUSICU_URL, json=payload)
            res_json = resp.json()
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        logger.warning(f"[MusicUID] 请求 QQ 音乐刷新接口失败: {e}")
        return False, f"网络请求异常: {e}"

    req_block = res_json.get("req")
    if not isinstance(req_block, dict):
        return False, "返回数据格式不符合预期"

    code = req_block.get("code")
    if code != 0:
        err_msg = str(req_block.get("errMsg") or f"错误码 {code}")
        logger.warning(f"[MusicUID] QQ 音乐移动凭据刷新失败: {err_msg}")
        return False, err_msg

    data = req_block.get("data")
    if not isinstance(data, dict):
        return False, "返回核心凭据为空"

    new_musickey = str(data.get("musickey") or target["musickey"])
    new_refresh_token = str(data.get("refresh_token") or target["refresh_token"])
    new_refresh_key = str(data.get("refresh_key") or target["refresh_key"])
    new_create_time = int(str(data.get("musickeyCreateTime") or int(time.time())))
    new_expires_in = int(str(data.get("keyExpiresIn") or target["key_expires_in"]))
    new_nick = str(data.get("nick") or target["nick"])

    updated_cred = QQMobileCredential(
        openid=str(data.get("openid") or target["openid"]),
        refresh_token=new_refresh_token,
        access_token=str(data.get("access_token") or target["access_token"]),
        expired_at=int(str(data.get("expired_at") or target["expired_at"])),
        musicid=target["musicid"],
        musickey=new_musickey,
        unionid=str(data.get("unionid") or target["unionid"]),
        str_musicid=str(data.get("str_musicid") or target["str_musicid"]),
        refresh_key=new_refresh_key,
        musickey_create_time=new_create_time,
        key_expires_in=new_expires_in,
        login_type=target["login_type"],
        nick=new_nick,
    )
    save_qq_credential(updated_cred)

    formatted_cookie = (
        f"uin={target['musicid']}; qm_keyst={new_musickey}; "
        f"qqmusic_key={new_musickey}"
    )
    music_config.set_config("qqmusic_cookie", formatted_cookie)
    logger.info(
        f"[MusicUID] QQ 音乐移动凭据自动刷新成功（用户: {new_nick or target['musicid']}，"
        f"有效期延顺至 {new_expires_in} 秒）"
    )
    return True, "刷新成功"


async def auto_refresh_qq_job() -> None:
    """定时巡检与自动续签 QQ 音乐凭证。"""
    cred = load_qq_credential()
    if cred is None:
        return
    if is_credential_expiring(cred):
        logger.info("[MusicUID] 检测到 QQ 音乐移动凭据即将过期，正在自动续期...")
        ok, msg = await refresh_qq_credential(cred)
        if not ok:
            logger.warning(f"[MusicUID] QQ 音乐定时自动续期失败: {msg}")


def parse_qq_cookie(cookie_str: str) -> dict[str, str]:
    """解析 QQ 音乐 Cookie 字符串并提取关键字段。"""
    cookies: dict[str, str] = {}
    for item in cookie_str.split(";"):
        if "=" in item:
            k, v = item.strip().split("=", 1)
            cookies[k] = v
    return cookies


def format_qq_cookie(cookie_str: str) -> str:
    """提取 QQ 音乐核心 Cookie 字段。"""
    cookies = parse_qq_cookie(cookie_str)
    keys = ["uin", "qm_keyst", "qqmusic_key", "pskey", "skey", "p_skey", "p_uin"]
    extracted = [f"{k}={cookies[k]}" for k in keys if k in cookies]
    return "; ".join(extracted) if extracted else cookie_str.strip()


async def _exchange_mobile_credential(
    sig_url: str, nick: str
) -> tuple[QQMobileCredential | None, str]:
    """通过 ptlogin 鉴权结果换取移动端完整长效凭据。"""
    parsed = urllib.parse.urlparse(sig_url)
    qs = urllib.parse.parse_qs(parsed.query)
    uin = qs.get("uin", [""])[0]
    ptsigx = qs.get("ptsigx", [""])[0]
    if not uin or not ptsigx:
        return None, "登录返回中缺少 uin 或 ptsigx 参数"

    check_sig_params = {
        "uin": uin,
        "pttype": "1",
        "service": "ptqrlogin",
        "nodirect": "0",
        "ptsigx": ptsigx,
        "s_url": "https://graph.qq.com/oauth2.0/login_jump",
        "ptlang": "2052",
        "ptredirect": "100",
        "aid": "716027609",
        "daid": "383",
        "j_later": "0",
        "low_login_hour": "0",
        "regmaster": "0",
        "pt_login_type": "3",
        "pt_aid": "0",
        "pt_aaid": "16",
        "pt_light": "0",
        "pt_3rd_aid": "100497308",
    }
    check_headers = {**_HEADERS, "Referer": "https://xui.ptlogin2.qq.com/"}

    try:
        async with httpx.AsyncClient(
            headers=check_headers, timeout=10.0, follow_redirects=False
        ) as client:
            sig_resp = await client.get(
                "https://ssl.ptlogin2.graph.qq.com/check_sig",
                params=check_sig_params,
            )
            p_skey = sig_resp.cookies.get("p_skey", "")
            if not p_skey:
                return None, "未获取到 p_skey 鉴权票据"

            auth_data = {
                "response_type": "code",
                "client_id": "100497308",
                "redirect_uri": (
                    "https://y.qq.com/portal/wx_redirect.html?login_type=1&"
                    "surl=https://y.qq.com/"
                ),
                "scope": "get_user_info,get_app_friends",
                "state": "state",
                "switch": "",
                "from_ptlogin": "1",
                "src": "1",
                "update_auth": "1",
                "openapi": "1010_1030",
                "g_tk": str(hash33(p_skey, 5381)),
                "auth_time": str(int(time.time() * 1000)),
                "ui": str(uuid.uuid4()),
            }
            auth_resp = await client.post(
                "https://graph.qq.com/oauth2.0/authorize",
                data=auth_data,
                cookies=dict(sig_resp.cookies),
            )
            location = auth_resp.headers.get("Location", "")
            code_match = re.search(r"[?&]code=([^&]+)", location)
            if not code_match:
                return None, "获取 OAuth code 授权码失败"
            code = code_match.group(1)

            login_payload = {
                "comm": {"tmeLoginType": 2},
                "req": {
                    "module": "QQConnectLogin.LoginServer",
                    "method": "QQLogin",
                    "param": {"code": code},
                },
            }
            login_resp = await client.post(_MUSICU_URL, json=login_payload)
            res_json = login_resp.json()
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        return None, f"请求授权服务异常: {e}"

    req_block = res_json.get("req")
    if not isinstance(req_block, dict):
        return None, "解析移动端登录响应失败"

    if req_block.get("code") != 0:
        return None, str(req_block.get("errMsg") or "移动端登录授权被拒绝")

    data = req_block.get("data")
    if not isinstance(data, dict):
        return None, "未返回凭证数据"

    musickey = str(data.get("musickey", ""))
    refresh_token = str(data.get("refresh_token", ""))
    if not musickey or not refresh_token:
        return None, "返回的 musickey 或 refresh_token 为空"

    cred = QQMobileCredential(
        openid=str(data.get("openid", "")),
        refresh_token=refresh_token,
        access_token=str(data.get("access_token", "")),
        expired_at=int(str(data.get("expired_at", 0))),
        musicid=int(str(data.get("musicid", uin))),
        musickey=musickey,
        unionid=str(data.get("unionid", "")),
        str_musicid=str(data.get("str_musicid", "")),
        refresh_key=str(data.get("refresh_key", "")),
        musickey_create_time=int(str(data.get("musickeyCreateTime") or int(time.time()))),
        key_expires_in=int(str(data.get("keyExpiresIn", 259200))),
        login_type=2,
        nick=str(data.get("nick") or nick),
    )
    return cred, "授权成功"


class QQMusicLoginProvider(BaseLoginProvider):
    """QQ 音乐 Android 移动端长效协议登录提供者。"""

    platform_name = "qq"
    display_name = "QQ音乐"

    async def create_qr_session(self) -> LoginSession:
        """生成 QQ 音乐登录二维码。"""
        qr_url = (
            f"https://ssl.ptlogin2.qq.com/ptqrshow?appid=716027609&e=2&l=M&s=3&d=72&v=4&"
            f"t={time.time()}&daid=383&pt_3rd_aid=100497308"
        )
        headers = {**_HEADERS, "Referer": "https://xui.ptlogin2.qq.com/"}
        try:
            async with httpx.AsyncClient(headers=headers, timeout=10.0) as client:
                resp = await client.get(qr_url)
                qrsig = resp.cookies.get("qrsig", "")
                if not qrsig or resp.status_code != 200:
                    return LoginSession(
                        provider_name=self.platform_name,
                        key="",
                        qr_url="",
                        qr_bytes=b"",
                        status=LoginStatus.FAILED,
                        message="获取 QQ 登录二维码失败",
                    )
                return LoginSession(
                    provider_name=self.platform_name,
                    key=qrsig,
                    qr_url=qr_url,
                    qr_bytes=resp.content,
                    status=LoginStatus.WAITING,
                    message="请使用【手机 QQ】扫码授权登录\n（模拟移动端协议，支持 refresh_token 长期自动续期）",
                )
        except httpx.HTTPError as e:
            return LoginSession(
                provider_name=self.platform_name,
                key="",
                qr_url="",
                qr_bytes=b"",
                status=LoginStatus.FAILED,
                message=f"网络请求失败: {e}",
            )

    async def check_qr_status(self, session: LoginSession) -> LoginSession:
        """轮询二维码扫码状态并在完成时换取移动端长效凭证。"""
        if not session.key:
            session.status = LoginStatus.FAILED
            session.message = "缺少会话标识"
            return session

        token = hash33(session.key)
        params = {
            "u1": "https://graph.qq.com/oauth2.0/login_jump",
            "ptqrtoken": str(token),
            "ptredirect": "0",
            "h": "1",
            "t": "1",
            "g": "1",
            "from_ui": "1",
            "ptlang": "2052",
            "action": f"0-0-{int(time.time() * 1000)}",
            "js_ver": "20102616",
            "js_type": "1",
            "pt_uistyle": "40",
            "aid": "716027609",
            "daid": "383",
            "pt_3rd_aid": "100497308",
            "has_onekey": "1",
        }
        headers = {**_HEADERS, "Referer": "https://xui.ptlogin2.qq.com/"}
        cookies = {"qrsig": session.key}

        try:
            async with httpx.AsyncClient(
                headers=headers, cookies=cookies, timeout=10.0
            ) as client:
                resp = await client.get(
                    "https://ssl.ptlogin2.qq.com/ptqrlogin", params=params
                )
        except httpx.HTTPError as e:
            session.status = LoginStatus.WAITING
            session.message = f"网络波动: {e}"
            return session

        match = re.search(r"ptuiCB\((.*?)\)", resp.text)
        if not match:
            session.status = LoginStatus.WAITING
            return session

        parts = [p.strip("' ") for p in match.group(1).split(",")]
        if not parts:
            session.status = LoginStatus.WAITING
            return session

        code = parts[0]
        if code == "66":
            session.status = LoginStatus.WAITING
            session.message = "等待手机 QQ 扫码"
        elif code == "67":
            session.status = LoginStatus.SCANNED
            session.message = "已扫码，请在手机 QQ 点击确认登录"
        elif code == "65":
            session.status = LoginStatus.EXPIRED
            session.message = "二维码已过期，请重新发起登录"
        elif code == "0":
            sig_url = parts[2] if len(parts) > 2 else ""
            nick = parts[5] if len(parts) > 5 else "QQ用户"
            cred, err = await _exchange_mobile_credential(sig_url, nick)
            if cred is None:
                session.status = LoginStatus.FAILED
                session.message = f"凭据换取失败: {err}"
                return session

            save_qq_credential(cred)
            formatted = (
                f"uin={cred['musicid']}; qm_keyst={cred['musickey']}; "
                f"qqmusic_key={cred['musickey']}"
            )
            music_config.set_config("qqmusic_cookie", formatted)
            session.status = LoginStatus.SUCCESS
            session.cookie = formatted
            session.nickname = cred["nick"] or nick
            session.message = "登录成功，已绑定移动端凭据并启用自动保活"
        else:
            msg = parts[4] if len(parts) > 4 else "登录失败"
            session.status = LoginStatus.FAILED
            session.message = msg

        return session

    @staticmethod
    async def verify_cookie(cookie_str: str) -> tuple[bool, str]:
        """验证 QQ 音乐 Cookie 是否有效。"""
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
        except (httpx.HTTPError, json.JSONDecodeError) as e:
            if "uin=" in formatted:
                return True, "已导入（无法联网验证）"
            return False, f"请求失败: {e}"
