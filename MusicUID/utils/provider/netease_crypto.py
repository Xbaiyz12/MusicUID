"""Netease weapi request encryption (AES-CBC + RSA).

网易的登录态只在 weapi 加密请求里生效：明文的 ``/api/...`` 接口会忽略 Cookie，
所以 VIP 音源、高音质档位都必须走这里。算法是社区公开的固定实现，不随账号变化。
"""

from __future__ import annotations

import json
import base64
import random
import string
import binascii
from typing import Final

# 网易 web 端固定常量
_MODULUS: Final[str] = (
    "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629"
    "ec4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d"
    "813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7"
)
_PUB_KEY: Final[str] = "010001"
_NONCE: Final[str] = "0CoJUm6Qyw8W8jud"
_IV: Final[str] = "0102030405060708"
_SECRET_CHARS: Final[str] = string.ascii_letters + string.digits
_SECRET_LEN: Final[int] = 16
_AES_BLOCK: Final[int] = 16


def _aes_encrypt(text: str, key: str) -> str:
    """Encrypt one weapi layer with AES-CBC/PKCS7 and base64 it.

    这里的 AES 依赖是可选的（pycryptodome）：缺失时由调用方回退公开外链，
    所以放在函数内导入，避免整个插件因缺包而加载失败。

    Args:
        text: Plain text to encrypt.
        key: 16-byte AES key.

    Returns:
        Base64 of the ciphertext.
    """
    from Crypto.Cipher import AES

    pad_len = _AES_BLOCK - len(text.encode("utf-8")) % _AES_BLOCK
    padded = text + chr(pad_len) * pad_len
    cipher = AES.new(key.encode("utf-8"), AES.MODE_CBC, _IV.encode("utf-8"))
    return base64.b64encode(cipher.encrypt(padded.encode("utf-8"))).decode("utf-8")


def _rsa_encrypt(secret: str) -> str:
    """RSA-encrypt the reversed random secret (raw, hex, zero padded).

    Args:
        secret: The per-request random AES key.

    Returns:
        256-character hex string used as ``encSecKey``.
    """
    reversed_secret = secret[::-1]
    a = int(binascii.hexlify(reversed_secret.encode("utf-8")), 16)
    b = int(_PUB_KEY, 16)
    c = int(_MODULUS, 16)
    return format(pow(a, b, c), "x").zfill(256)


def weapi_form(payload: dict[str, object]) -> dict[str, str]:
    """Build the form body of a weapi POST.

    Args:
        payload: Plain request payload.

    Returns:
        The ``params`` and ``encSecKey`` form fields.

    Raises:
        ImportError: pycryptodome is not installed.
    """
    secret = "".join(random.choices(_SECRET_CHARS, k=_SECRET_LEN))
    inner = _aes_encrypt(json.dumps(payload, ensure_ascii=False), _NONCE)
    return {"params": _aes_encrypt(inner, secret), "encSecKey": _rsa_encrypt(secret)}


def weapi_cookie(music_u: str) -> str:
    """Turn a bare ``MUSIC_U`` value into a Cookie header.

    Args:
        music_u: The MUSIC_U cookie value, possibly already prefixed.

    Returns:
        A Cookie header value, or an empty string when nothing was given.
    """
    value = music_u.strip()
    if not value:
        return ""
    if "MUSIC_U" not in value:
        value = f"MUSIC_U={value}"
    # os=pc 是必须的：实测同一条 MUSIC_U 只带它时 account 接口返回无名（未登录），
    # 补上 os=pc 后才返回昵称与 uid，VIP 取流也从 br=0 变成 320000。
    if "os=" not in value:
        value = f"{value}; os=pc"
    return value
