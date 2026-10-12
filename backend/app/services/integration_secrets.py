"""公司级集成凭证的加解密（飞书 App Secret 等）。

密钥由 settings.secret_key 派生（Fernet），换 SECRET_KEY 会导致已存凭证解不开，
所以生产换 key 时要重新填一次凭证。接口永远只回掩码，不回明文。
"""
from __future__ import annotations

import base64
import hashlib

from ..core.config import settings

_PREFIX = "enc:v1:"


def _fernet():
    from cryptography.fernet import Fernet

    digest = hashlib.sha256(("lobster-integration-v1:" + str(settings.secret_key or "")).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plain: str) -> str:
    text = str(plain or "")
    if not text:
        return ""
    return _PREFIX + _fernet().encrypt(text.encode("utf-8")).decode("ascii")


def decrypt(value: str) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    if not raw.startswith(_PREFIX):
        return raw  # 兼容历史明文（若曾直接存过）
    try:
        return _fernet().decrypt(raw[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except Exception:
        return ""


def mask(secret: str) -> str:
    """只用于展示：cli_a1b2…wx9z。"""
    text = str(secret or "")
    if not text:
        return ""
    if len(text) <= 8:
        return "…" + text[-2:]
    return text[:6] + "…" + text[-4:]
