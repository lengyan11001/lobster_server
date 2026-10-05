"""个微接管「聊天里给出的联系方式」上报：观察接口落库 + 管理后台按用户账户查询。

背景（2026-10-05）：客户在微信里回「我的微信号是 djndndnnn」这种消息，以前微信接管的
模型契约里没有联系方式字段，识别不到也上报不了；现在补了 contact_shared + 本地正则兜底，
落库到 wechat_shared_contacts，管理后台新增「上报微信号」tab 可按用户账户查。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _observe(db_session, test_user, monkeypatch, items, *, contact_key="张深根-AI三域营销运营"):
    from backend.app.api import wechat_intelligence as wi

    monkeypatch.setattr(wi, "online_user_for_mobile_user", lambda _db, user: user)
    body = wi.WechatObservationIn(
        account_id="pc-wechat-default",
        contact_key=contact_key,
        contact_name="张深根",
        event_type="reply_sent",
        status="completed",
        inbound_message_id="wxhash:test-shared-1",
        inbound_text="我的微信号是 djndndnnn",
        reply_text="好的，收到",
        shared_contacts=items,
    )
    return wi.observe_wechat_interaction(body=body, current_user=test_user, db=db_session)


def test_observe_saves_shared_contacts(db_session, test_user, monkeypatch):
    from backend.app.api import wechat_intelligence as wi
    from backend.app.models import WechatSharedContact

    out = _observe(db_session, test_user, monkeypatch, [
        wi.WechatSharedContactIn(kind="wechat_id", value="djndndnnn",
                                 evidence="我的微信号是 djndndnnn", direction="inbound"),
        wi.WechatSharedContactIn(kind="mobile", value="181 2465 5127",
                                 evidence="18124655127", direction="outbound"),
        wi.WechatSharedContactIn(kind="mobile", value="123", evidence="乱码"),  # 不是 11 位 → 丢掉
    ])
    assert out["ok"] is True
    assert out["shared_contacts_saved"] == 2

    rows = db_session.query(WechatSharedContact).filter(WechatSharedContact.user_id == test_user.id).all()
    assert {(row.kind, row.value, row.direction) for row in rows} == {
        ("wechat_id", "djndndnnn", "inbound"),
        ("mobile", "18124655127", "outbound"),
    }
    row = [r for r in rows if r.kind == "wechat_id"][0]
    assert row.contact_name == "张深根"
    assert row.evidence == "我的微信号是 djndndnnn"


def test_observe_shared_contacts_is_idempotent(db_session, test_user, monkeypatch):
    from backend.app.api import wechat_intelligence as wi
    from backend.app.models import WechatSharedContact

    for _ in range(2):
        out = _observe(db_session, test_user, monkeypatch, [
            wi.WechatSharedContactIn(kind="wechat_id", value="djndndnnn", evidence="我的微信号是 djndndnnn"),
        ])
        assert out["shared_contacts_saved"] == 1
    rows = db_session.query(WechatSharedContact).filter(WechatSharedContact.user_id == test_user.id).all()
    assert len(rows) == 1


def test_admin_can_query_shared_contacts_by_user_account(db_session, test_user, monkeypatch):
    from backend.app.api import admin as admin_api
    from backend.app.api import wechat_intelligence as wi

    _observe(db_session, test_user, monkeypatch, [
        wi.WechatSharedContactIn(kind="wechat_id", value="djndndnnn", evidence="我的微信号是 djndndnnn"),
    ])

    listed = admin_api.admin_list_wechat_shared_contacts(
        user_id=test_user.id, kind="", q="", page=1, page_size=10, ctx=None, db=db_session)
    assert listed["ok"] is True and listed["total"] == 1
    item = listed["items"][0]
    assert item["user_id"] == test_user.id
    assert item["user_email"] == test_user.email
    assert item["kind"] == "wechat_id" and item["value"] == "djndndnnn"

    # 按邮箱模糊查也能命中；按别的用户查不到
    assert admin_api.admin_list_wechat_shared_contacts(
        user_id=0, kind="", q=test_user.email,
        page=1, page_size=10, ctx=None, db=db_session)["total"] == 1
    assert admin_api.admin_list_wechat_shared_contacts(
        user_id=0, kind="", q="不存在的人@test.local",
        page=1, page_size=10, ctx=None, db=db_session)["total"] == 0
    assert admin_api.admin_list_wechat_shared_contacts(
        user_id=test_user.id + 99, kind="", q="",
        page=1, page_size=10, ctx=None, db=db_session)["total"] == 0
    # 类型过滤
    assert admin_api.admin_list_wechat_shared_contacts(
        user_id=0, kind="mobile", q="",
        page=1, page_size=10, ctx=None, db=db_session)["total"] == 0


def test_sutui_chat_persists_ai_judged_contacts(db_session, test_user):
    """服务端在 LLM 代理里直接按 AI 的判断入库（对方不一定按固定格式写，服务端不做正则）。"""
    from backend.app.api import sutui_chat_proxy as sp
    from backend.app.models import WechatSharedContact

    body = {
        "messages": [
            {"role": "system", "content": '必须返回 JSON：{"should_reply":true,"should_invite_group":false}'},
            {"role": "user", "content": "会话对象：张深根-AI三域营销运营\n\n对方最新消息：\n我的微信号是 djndndnnn"},
        ]
    }
    out = {"choices": [{"message": {"content": (
        '{"should_reply":true,"contact_shared":'
        '[{"kind":"wechat_id","value":"djndndnnn","evidence":"我的微信号是 djndndnnn","direction":"inbound"}]}'
    )}}]}
    assert sp._persist_wechat_model_contacts(db_session, test_user, body, out) == 1
    rows = db_session.query(WechatSharedContact).all()
    assert len(rows) == 1
    assert rows[0].value == "djndndnnn"
    assert rows[0].contact_key.startswith("张深根")
    assert rows[0].kind == "wechat_id" and rows[0].direction == "inbound"
    # 重复同一轮不要再插一条
    assert sp._persist_wechat_model_contacts(db_session, test_user, body, out) == 1
    assert db_session.query(WechatSharedContact).count() == 1
    # 不是微信接管请求（system 里没有 should_reply/should_invite_group）就不落库
    plain = {"messages": [{"role": "system", "content": "你是助手"}]}
    assert sp._persist_wechat_model_contacts(db_session, test_user, plain, out) == 0
    # AI 没给 contact_shared 时不落库
    none_out = {"choices": [{"message": {"content": '{"should_reply":true}'}}]}
    assert sp._persist_wechat_model_contacts(db_session, test_user, body, none_out) == 0


def test_ensure_wechat_contact_instruction_only_for_old_client():
    from backend.app.api import sutui_chat_proxy as sp

    old = {"messages": [{"role": "system", "content": '{"should_reply":true,"should_invite_group":false}'}]}
    assert sp._ensure_wechat_contact_instruction(old) is True
    assert "contact_shared" in old["messages"][0]["content"]
    assert sp._ensure_wechat_contact_instruction(old) is False   # 补过一次就不再补

    new = {"messages": [{"role": "system", "content": '{"should_reply":true,"should_invite_group":false,"contact_shared":[]}'}]}
    assert sp._ensure_wechat_contact_instruction(new) is False

    plain = {"messages": [{"role": "system", "content": "你是助手"}]}
    assert sp._ensure_wechat_contact_instruction(plain) is False
