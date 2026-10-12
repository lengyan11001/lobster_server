"""公司级飞书配置（每个老板配自己的 App ID/Secret）：加密存储 / 只回掩码 / 按公司发消息 / 手机号认人。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from backend.app.api import manage as manage_api
from backend.app.manage_models import MCompany, MIntegration, MMembership, MPlanNode, MProject
from backend.app.models import User
from backend.app.services import feishu_bot, integration_secrets, manage_notify


def _day(offset: int) -> str:
    return (datetime.utcnow() + timedelta(hours=8) + timedelta(days=offset)).date().isoformat()


class _Resp:
    def __init__(self, body=None, status_code: int = 200):
        self.status_code = status_code
        self._body = {"code": 0, "msg": "success"} if body is None else body
        self.content = json.dumps(self._body, ensure_ascii=False).encode()
        self.text = json.dumps(self._body, ensure_ascii=False)

    def json(self):
        return self._body


class _Client:
    """按 URL 分发的假 httpx.AsyncClient，支持手机号/邮箱换 id。"""

    def __init__(self, *, resp=None, token="t-co", open_ids=None):
        self.calls = []
        self._resp = resp
        self.token = token
        self.open_ids = dict(open_ids or {})

    async def post(self, url, json=None, headers=None, **kw):
        self.calls.append({"url": url, "json": json, "headers": headers or {}})
        if self._resp is not None:
            return self._resp
        if url.endswith("/auth/v3/tenant_access_token/internal"):
            return _Resp({"code": 0, "tenant_access_token": self.token})
        if "/contact/v3/users/batch_get_id" in url:
            keys = list((json or {}).get("mobiles") or []) + list((json or {}).get("emails") or [])
            return _Resp({"code": 0, "data": {"user_list": [{"user_id": self.open_ids.get(k, "")} for k in keys]}})
        if "/im/v1/messages" in url:
            return _Resp({"code": 0, "data": {"message_id": "om_1"}})
        return _Resp()

    def urls(self, needle):
        return [c["url"] for c in self.calls if needle in c["url"]]


@pytest.fixture
def company_env(db_session, test_user):
    """一家公司 + 一个用手机号登录的员工（名下有 1 条逾期任务）。"""
    company = MCompany(name="必火科技（测试）", short_name="必火", owner_user_id=test_user.id)
    db_session.add(company)
    db_session.flush()
    worker_user = User(email="13800000000@sms.lobster.local", hashed_password="x",
                       role="user", preferred_model="sutui")
    db_session.add(worker_user)
    db_session.flush()
    worker = MMembership(company_id=company.id, user_id=worker_user.id, display_name="林珂",
                         dept="业务中心", status="active")
    db_session.add(worker)
    db_session.flush()
    project = MProject(company_id=company.id, name="抖音渠道 0→30 万", created_by=test_user.id,
                       status="running", arrangement_status="confirmed", progress=30,
                       start_at=_day(-10), end_at=_day(40))
    db_session.add(project)
    db_session.flush()
    db_session.add(MPlanNode(company_id=company.id, project_id=project.id, node_type="task",
                             title="首条爆款脚本", owner_kind="member", owner_membership_id=worker.id,
                             owner_name="林珂", start_at=_day(-6), end_at=_day(-2), status="in_progress",
                             progress=40, weight=1, order_index=0))
    db_session.commit()
    return {"company": company, "worker": worker, "worker_user": worker_user, "project": project}


def test_secret_encrypt_roundtrip_and_mask():
    plain = "sec_AbC123456789xyz"
    enc = integration_secrets.encrypt(plain)
    assert enc.startswith("enc:v1:") and plain not in enc
    assert integration_secrets.decrypt(enc) == plain
    assert integration_secrets.decrypt("") == ""
    assert integration_secrets.mask(plain).startswith("sec_Ab")
    assert plain not in integration_secrets.mask(plain)
    assert integration_secrets.decrypt("明文历史值") == "明文历史值"   # 兼容


def test_company_config_wins_over_env(monkeypatch, db_session, company_env):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_env")
    monkeypatch.setenv("FEISHU_APP_SECRET", "sec_env")
    company = company_env["company"]
    db_session.add(MIntegration(company_id=company.id, kind="feishu", app_id="cli_bihuo",
                                app_secret_enc=integration_secrets.encrypt("sec_bihuo"),
                                webhook="https://open.feishu.cn/open-apis/bot/v2/hook/BIHUO", enabled=True))
    db_session.commit()
    cfg = manage_notify.company_feishu_config(db_session, company)
    assert cfg["source"] == "company"
    assert cfg["app_id"] == "cli_bihuo" and cfg["app_secret"] == "sec_bihuo"
    assert cfg["webhook"].endswith("/BIHUO")


def test_company_config_falls_back_to_env(monkeypatch, db_session, company_env):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_env")
    monkeypatch.setenv("FEISHU_APP_SECRET", "sec_env")
    monkeypatch.setenv("MANAGE_FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/ENV")
    cfg = manage_notify.company_feishu_config(db_session, company_env["company"])
    assert cfg["source"] == "env" and cfg["app_id"] == "cli_env"


def test_disabled_company_config_ignored(monkeypatch, db_session, company_env):
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("MANAGE_FEISHU_WEBHOOK", raising=False)
    company = company_env["company"]
    db_session.add(MIntegration(company_id=company.id, kind="feishu", app_id="cli_x", enabled=False))
    db_session.commit()
    cfg = manage_notify.company_feishu_config(db_session, company)
    assert cfg["source"] == "none" and cfg["enabled"] is False


def test_member_identifier_uses_phone(monkeypatch, db_session, company_env):
    monkeypatch.delenv("MANAGE_FEISHU_WEBHOOK", raising=False)
    ident, kind = manage_notify.member_identifier(db_session, company_env["worker"])
    assert ident == "13800000000" and kind == "mobile"


@pytest.mark.asyncio
async def test_resolve_open_id_by_mobile_payload(monkeypatch, db_session, company_env):
    company = company_env["company"]
    db_session.add(MIntegration(company_id=company.id, kind="feishu", app_id="cli_bihuo",
                                app_secret_enc=integration_secrets.encrypt("sec_bihuo"), enabled=True))
    db_session.commit()
    cfg = manage_notify.company_feishu_config(db_session, company)
    client = _Client(open_ids={"13800000000": "ou_linke"})
    oid = await manage_notify.member_open_id(db_session, company_env["worker"], cfg, client=client)
    assert oid == "ou_linke"
    assert client.calls[0]["json"] == {"app_id": "cli_bihuo", "app_secret": "sec_bihuo"}
    lookup = [c for c in client.calls if "batch_get_id" in c["url"]][0]
    assert lookup["json"] == {"mobiles": ["13800000000"]}


@pytest.mark.asyncio
async def test_daily_digest_uses_company_app(monkeypatch, db_session, company_env):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_env")
    monkeypatch.setenv("FEISHU_APP_SECRET", "sec_env")
    monkeypatch.setenv("MANAGE_FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/ENV")
    company = company_env["company"]
    db_session.add(MIntegration(company_id=company.id, kind="feishu", app_id="cli_bihuo",
                                app_secret_enc=integration_secrets.encrypt("sec_bihuo"),
                                webhook="https://open.feishu.cn/open-apis/bot/v2/hook/BIHUO", enabled=True))
    db_session.commit()
    client = _Client(open_ids={"13800000000": "ou_linke"})
    res = await manage_notify.send_daily_digest(db_session, company, client=client)
    assert res["source"] == "company"
    assert client.calls[0]["url"].endswith("/BIHUO")                                  # 用公司的群
    assert client.calls[1]["json"] == {"app_id": "cli_bihuo", "app_secret": "sec_bihuo"}  # 用公司的应用
    assert res["private"] == 1
    msg = [c for c in client.calls if "/im/v1/messages" in c["url"]][0]
    assert msg["json"]["receive_id"] == "ou_linke"
    assert "首条爆款脚本" in json.loads(msg["json"]["content"])["elements"][0]["text"]["content"]


def test_payload_masks_secret(monkeypatch, db_session, company_env):
    company = company_env["company"]
    db_session.add(MIntegration(company_id=company.id, kind="feishu", app_id="cli_bihuo",
                                app_secret_enc=integration_secrets.encrypt("sec_very_secret_value"),
                                webhook="https://x/hook", enabled=True))
    db_session.commit()
    payload = manage_api._feishu_payload(db_session, company)
    cfg = payload["config"]
    assert cfg["app_id"] == "cli_bihuo" and cfg["app_secret_set"] is True
    assert "sec_very_secret_value" not in json.dumps(payload, ensure_ascii=False)
    assert cfg["app_secret_mask"].endswith("alue")
    assert payload["members"]["reachable"] == 1 and payload["members"]["total"] == 1


def test_put_stores_encrypted_and_keeps_secret_when_blank(db_session, company_env, test_user):
    company = company_env["company"]
    body = manage_api.FeishuConfigIn(company_id=company.id, app_id="cli_new", app_secret="sec_new",
                                     webhook="https://x/hook", enabled=True)
    manage_api.put_feishu_config(body, actor=test_user, db=db_session)
    row = manage_notify.get_integration(db_session, int(company.id))
    assert row.app_id == "cli_new" and row.app_secret_enc.startswith("enc:v1:")
    assert "sec_new" not in row.app_secret_enc
    assert integration_secrets.decrypt(row.app_secret_enc) == "sec_new"
    # 再保存一次不带 secret：不能把已存的 secret 冲掉
    manage_api.put_feishu_config(manage_api.FeishuConfigIn(company_id=company.id, app_id="cli_new2"),
                                 actor=test_user, db=db_session)
    row2 = manage_notify.get_integration(db_session, int(company.id))
    assert row2.app_id == "cli_new2"
    assert integration_secrets.decrypt(row2.app_secret_enc) == "sec_new"
    # clear_secret=True 才清空
    manage_api.put_feishu_config(manage_api.FeishuConfigIn(company_id=company.id, app_id="cli_new2",
                                                           clear_secret=True), actor=test_user, db=db_session)
    assert integration_secrets.decrypt(manage_notify.get_integration(db_session, int(company.id)).app_secret_enc) == ""


@pytest.mark.asyncio
async def test_test_endpoint_reports_hints(monkeypatch, db_session, company_env, test_user):
    company = company_env["company"]
    db_session.add(MIntegration(company_id=company.id, kind="feishu", app_id="cli_bad",
                                app_secret_enc=integration_secrets.encrypt("sec_bad"), enabled=True))
    db_session.commit()

    async def _bad_token(**kw):
        raise RuntimeError('tenant_access_token failed: {"code": 99991672}')

    monkeypatch.setattr(feishu_bot, "fetch_tenant_access_token", _bad_token)
    out = await manage_api.test_feishu_config(company_id=company.id, send=True, actor=test_user, db=db_session)
    assert out["ok"] is False
    step = [s for s in out["steps"] if "tenant_access_token" in s["name"]][0]
    assert step["ok"] is False and "权限未开通" in step["hint"]


@pytest.mark.asyncio
async def test_test_endpoint_requires_config(db_session, company_env, test_user, monkeypatch):
    for name in ("MANAGE_FEISHU_WEBHOOK", "FEISHU_APP_ID", "FEISHU_APP_SECRET"):
        monkeypatch.delenv(name, raising=False)
    out = await manage_api.test_feishu_config(company_id=company_env["company"].id, send=False,
                                              actor=test_user, db=db_session)
    assert out["ok"] is False and "既没有群 webhook" in out["steps"][0]["detail"]
    assert "App ID + App Secret" in out["steps"][0]["hint"]
