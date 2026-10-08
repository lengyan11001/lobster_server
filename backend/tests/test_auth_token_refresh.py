"""登录态 30 天 + 静默续签（POST /auth/refresh）的回归测试。

背景（2026-09-17）：客户端提示「登录状态已失效」。查证是 JWT 7 天到期且没有续签机制
（今天全站 /auth/me 有 9048 次 401）。现在改成 30 天 + 到期前静默续签。

覆盖：
1. 新登录签发的 token 有效期 ≈ 30 天；
2. /auth/refresh 用有效 token 换新 token：有效期重新 30 天、jti 保持不变（槽位/会话不受影响）；
3. 刚过期（在宽限内）也能续签；过期超过宽限 → 401；
4. 用户不存在 / 品牌不一致 / 空 token → 401/403。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers

from backend.app.api import auth as auth_api
from backend.app.core.config import settings


def _decode(token: str) -> dict:
    return pyjwt.decode(token, settings.secret_key, algorithms=[auth_api.ALGORITHM],
                        options={"verify_exp": False})


def test_access_token_ttl_is_30_days():
    assert auth_api.ACCESS_TOKEN_EXPIRE_MINUTES == 60 * 24 * 30
    assert auth_api.ACCESS_TOKEN_REFRESH_MIN_REMAINING_SECONDS == 60 * 60 * 24


def test_login_token_expires_in_30_days(db_session, test_user):
    token = auth_api.create_access_token(data=auth_api.access_token_claims(test_user))
    payload = _decode(token)
    exp = datetime.utcfromtimestamp(int(payload["exp"]))
    remaining = exp - datetime.utcnow()
    assert timedelta(days=29, hours=23) < remaining <= timedelta(days=30, minutes=1)
    assert payload.get("jti")


def test_refresh_extends_token_and_keeps_jti(db_session, test_user):
    old = auth_api.create_access_token(data=auth_api.access_token_claims(test_user))
    old_payload = _decode(old)
    result = auth_api.refresh_access_token(request=_FakeRequest(), token=old, db=db_session)
    assert result["ok"] is True
    assert result["expires_in"] == 60 * 24 * 30 * 60
    new_payload = _decode(result["access_token"])
    assert new_payload["jti"] == old_payload["jti"], "续签必须保持同一个会话 id（槽位占用按 jti 记录）"
    assert new_payload["sub"] == old_payload["sub"]
    new_remaining = datetime.utcfromtimestamp(int(new_payload["exp"])) - datetime.utcnow()
    assert new_remaining > timedelta(days=29, hours=23)


def test_refresh_accepts_recently_expired_token(db_session, test_user):
    expired = auth_api.create_access_token(
        data=auth_api.access_token_claims(test_user),
        expires_delta=timedelta(hours=-2),          # 2 小时前过期，仍在 1 天宽限内
    )
    result = auth_api.refresh_access_token(request=_FakeRequest(), token=expired, db=db_session)
    assert result["ok"] is True


def test_refresh_rejects_token_expired_beyond_grace(db_session, test_user):
    too_old = auth_api.create_access_token(
        data=auth_api.access_token_claims(test_user),
        expires_delta=timedelta(days=-5),
    )
    with pytest.raises(HTTPException) as err:
        auth_api.refresh_access_token(request=_FakeRequest(), token=too_old, db=db_session)
    assert err.value.status_code == 401
    assert "重新登录" in str(err.value.detail)


def test_refresh_rejects_unknown_user_and_empty_token(db_session):
    orphan = auth_api.create_access_token(data={"sub": "999999", "brand_mark": "bihuo"})
    with pytest.raises(HTTPException) as err:
        auth_api.refresh_access_token(request=_FakeRequest(), token=orphan, db=db_session)
    assert err.value.status_code == 401
    with pytest.raises(HTTPException) as err2:
        auth_api.refresh_access_token(request=_FakeRequest(), token="", db=db_session)
    assert err2.value.status_code == 401


def test_refresh_rejects_brand_mismatch(db_session, test_user):
    """客户端带的品牌与 token/账号品牌不一致 → 403（与 /auth/me 一致）。"""
    token = auth_api.create_access_token(data=auth_api.access_token_claims(test_user))
    with pytest.raises(HTTPException) as err:
        auth_api.refresh_access_token(
            request=_FakeRequest(brand="yingshi"), token=token, db=db_session
        )
    assert err.value.status_code == 403
    assert "品牌" in str(err.value.detail)


class _FakeClient:
    host = "127.0.0.1"


class _FakeRequest:
    def __init__(self, brand: str = "") -> None:
        self.headers = Headers({"x-lobster-brand": brand} if brand else {})
        self.query_params = Headers({})
        self.client = _FakeClient()
        self.state = type("_S", (), {})()

# ---------------- 改密码后踢掉已登录会话（2026-09-27） ----------------

def test_change_password_invalidates_existing_tokens(db_session, test_user):
    """安全页改密码后：老 token 立刻失效、不能续签；新登录签发的 token 正常。"""
    test_user.hashed_password = auth_api.get_password_hash("old-pass-123")
    db_session.commit()
    old_token = auth_api.create_access_token(data=auth_api.access_token_claims(test_user))
    assert _decode(old_token).get("pv") == 0

    result = auth_api.change_password(
        body=auth_api.ChangePasswordBody(old_password="old-pass-123", new_password="new-pass-456"),
        current_user=test_user,
        db=db_session,
    )
    assert result["ok"] is True
    assert result["relogin_required"] is True, "改密码必须要求重新登录"
    assert int(test_user.password_version) == 1

    with pytest.raises(HTTPException) as err:
        auth_api.assert_token_password_version(_decode(old_token), test_user)
    assert err.value.status_code == 401
    assert "重新登录" in str(err.value.detail)

    # 老 token 也不能再静默续签
    with pytest.raises(HTTPException) as err2:
        auth_api.refresh_access_token(request=_FakeRequest(), token=old_token, db=db_session)
    assert err2.value.status_code == 401

    # 用新密码登录后签发的 token 正常，续签后仍带新 pv
    new_token = auth_api.create_access_token(data=auth_api.access_token_claims(test_user))
    assert _decode(new_token).get("pv") == 1
    auth_api.assert_token_password_version(_decode(new_token), test_user)
    refreshed = auth_api.refresh_access_token(request=_FakeRequest(), token=new_token, db=db_session)
    assert _decode(refreshed["access_token"]).get("pv") == 1


def test_get_current_user_rejects_token_issued_before_password_change(db_session, test_user):
    import asyncio

    old_token = auth_api.create_access_token(data=auth_api.access_token_claims(test_user))
    test_user.password_version = int(test_user.password_version or 0) + 1     # 模拟改过密码
    db_session.commit()

    with pytest.raises(HTTPException) as err:
        asyncio.run(auth_api.get_current_user(request=_FakeRequest(), token=old_token, db=db_session))
    assert err.value.status_code == 401
    assert "重新登录" in str(err.value.detail)

    fresh = auth_api.create_access_token(data=auth_api.access_token_claims(test_user))
    user = asyncio.run(auth_api.get_current_user(request=_FakeRequest(), token=fresh, db=db_session))
    assert user.id == test_user.id


def test_tokens_without_pv_still_work_for_users_who_never_changed_password(db_session, test_user):
    """上线兼容：没改过密码的用户（pv=0）手上没有 pv 的老 token 不能被误杀。"""
    legacy = auth_api.create_access_token(data={"sub": str(test_user.id)})   # 老格式：没有 pv
    assert _decode(legacy).get("pv") is None
    user = __import__("asyncio").run(
        auth_api.get_current_user(request=_FakeRequest(), token=legacy, db=db_session)
    )
    assert user.id == test_user.id
