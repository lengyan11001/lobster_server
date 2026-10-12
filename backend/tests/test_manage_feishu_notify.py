"""manage 飞书播报回归测试：卡片格式 / 群 webhook / 自建应用单聊 / 今日任务与逾期口径。

需求（2026-10-11）：员工要接入飞书，把任务情况与进度通过飞书发给他们。
覆盖：
- 飞书交互卡片结构（msg_type/header/lark_md/按钮/备注）；
- 群机器人 webhook：未配置时静默跳过、正常发送、错误码抛错、HTTP 错误抛错；
- 自建应用单聊：tenant_access_token → im/v1/messages 的 receive_id / content 组装；
- 今日任务口径：逾期高亮计数、按人过滤、只推逾期时无逾期则跳过；
- send_daily_digest = 群卡片 + 每个有任务的人一条私聊。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from backend.app.manage_models import MCompany, MMembership, MPlanNode, MProject
from backend.app.services import feishu_bot, manage_notify


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
    """假的 httpx.AsyncClient：按 URL 分发响应（token / 通讯录 / 发消息 / webhook），并记录出站请求。"""

    def __init__(self, *, resp=None, token="t-1", open_ids=None):
        self.calls = []
        self._resp = resp
        self.token = token
        self.open_ids = dict(open_ids or {})

    async def post(self, url, json=None, headers=None, **kw):
        self.calls.append({"url": url, "json": json, "headers": headers or {}})
        if self._resp is not None:
            return self._resp
        if url.endswith("/auth/v3/tenant_access_token/internal"):
            return _Resp({"code": 0, "tenant_access_token": self.token, "expire": 7200})
        if "/contact/v3/users/batch_get_id" in url:
            emails = list((json or {}).get("emails") or [])
            rows = [{"user_id": self.open_ids.get(e, "")} for e in emails]
            return _Resp({"code": 0, "data": {"user_list": rows}})
        if "/im/v1/messages" in url:
            return _Resp({"code": 0, "data": {"message_id": "om_1"}})
        return _Resp()

    def urls(self, needle):
        return [c["url"] for c in self.calls if needle in c["url"]]


@pytest.fixture
def feishu_company(db_session, test_user):
    company = MCompany(name="必火科技（测试）", short_name="必火", owner_user_id=test_user.id)
    db_session.add(company)
    db_session.flush()
    worker = MMembership(company_id=company.id, display_name="林珂", dept="业务中心",
                         email="worker@test.local", load_pct=60, status="active")
    idle = MMembership(company_id=company.id, display_name="周研", dept="内容中心",
                       email="idle@test.local", load_pct=10, status="active")
    db_session.add_all([worker, idle])
    db_session.flush()
    project = MProject(company_id=company.id, name="抖音渠道 0→30 万", created_by=test_user.id,
                       status="running", arrangement_status="confirmed",
                       progress=42, start_at=_day(-10), end_at=_day(60))
    db_session.add(project)
    db_session.flush()

    def node(title, *, start, end, status, owner=None, progress=0):
        row = MPlanNode(company_id=company.id, project_id=project.id, node_type="task", title=title,
                        owner_kind="member" if owner else "unassigned",
                        owner_membership_id=(owner.id if owner else None),
                        owner_name=(owner.display_name if owner else ""),
                        requirement="每周产出 8 条短视频", start_at=start, end_at=end,
                        kpi="周均播放 5 万", status=status, progress=progress, weight=1, order_index=0)
        db_session.add(row)
        db_session.flush()
        return row

    overdue = node("首条爆款脚本", start=_day(-6), end=_day(-2), status="in_progress",
                   owner=worker, progress=40)
    soon = node("本周成片交付", start=_day(-1), end=_day(2), status="in_progress",
                owner=worker, progress=10)
    node("复盘 SOP", start=_day(20), end=_day(30), status="not_started", owner=idle)
    db_session.commit()
    return {"company": company, "worker": worker, "idle": idle, "project": project,
            "overdue": overdue, "soon": soon}


# ── 卡片与传输 ──

def test_build_card_shape():
    card = feishu_bot.build_card("今日任务", ["a", "  ", "b"], template="orange",
                                 note="来自 manage", link="https://manage.bhzn.top", link_text="打开")
    assert card["msg_type"] == "interactive"
    assert card["card"]["header"]["title"]["content"] == "今日任务"
    assert card["card"]["header"]["template"] == "orange"
    body = card["card"]["elements"][0]
    assert body["text"]["tag"] == "lark_md" and body["text"]["content"] == "a\nb"
    assert card["card"]["elements"][1]["actions"][0]["url"] == "https://manage.bhzn.top"
    assert card["card"]["elements"][2]["elements"][0]["content"] == "来自 manage"


def test_build_card_empty_lines_fallback():
    card = feishu_bot.build_card("t", ["", "   "])
    assert card["card"]["elements"][0]["text"]["content"] == "（暂无内容）"


@pytest.mark.asyncio
async def test_send_card_skipped_without_webhook(monkeypatch):
    monkeypatch.delenv("MANAGE_FEISHU_WEBHOOK", raising=False)
    monkeypatch.delenv("LOBSTER_MANAGE_FEISHU_WEBHOOK", raising=False)
    client = _Client()
    res = await feishu_bot.send_card("t", ["x"], client=client)
    assert res["ok"] is False and "skipped" in res
    assert client.calls == []


@pytest.mark.asyncio
async def test_send_card_posts_to_webhook(monkeypatch):
    monkeypatch.setenv("MANAGE_FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/TEST")
    client = _Client()
    res = await feishu_bot.send_card("今日任务", ["⚠️ **首条爆款脚本**"], template="orange", client=client)
    assert res["ok"] is True
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"].endswith("/TEST")
    assert call["json"]["msg_type"] == "interactive"
    assert call["json"]["card"]["header"]["title"]["content"] == "今日任务"
    assert "首条爆款脚本" in call["json"]["card"]["elements"][0]["text"]["content"]


@pytest.mark.asyncio
async def test_post_card_raises_on_feishu_error_code():
    client = _Client(resp=_Resp({"code": 19021, "msg": "sign match fail"}))
    with pytest.raises(RuntimeError) as exc:
        await feishu_bot.post_card({"msg_type": "interactive", "card": {}}, webhook="https://x/hook", client=client)
    assert "19021" in str(exc.value)


@pytest.mark.asyncio
async def test_post_card_raises_on_http_error():
    client = _Client(resp=_Resp({"raw": "bad"}, status_code=400))
    with pytest.raises(RuntimeError) as exc:
        await feishu_bot.post_card({"msg_type": "interactive", "card": {}}, webhook="https://x/hook", client=client)
    assert "HTTP 400" in str(exc.value)


@pytest.mark.asyncio
async def test_app_single_chat_payload(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "sec_test")
    client = _Client(token="t-123")
    card = feishu_bot.build_card("你的任务", ["- **首条爆款脚本** · 进行中 40%"])
    res = await feishu_bot.send_card_to_user("ou_abc", card, client=client)
    assert res.get("code") == 0
    assert client.calls[0]["url"].endswith("/auth/v3/tenant_access_token/internal")
    assert client.calls[0]["json"] == {"app_id": "cli_test", "app_secret": "sec_test"}
    second = client.calls[1]
    assert "/im/v1/messages?receive_id_type=open_id" in second["url"]
    assert second["json"]["receive_id"] == "ou_abc"
    assert second["json"]["msg_type"] == "interactive"
    assert "你的任务" in json.loads(second["json"]["content"])["header"]["title"]["content"]


@pytest.mark.asyncio
async def test_resolve_open_id_by_email(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "sec_test")
    client = _Client(token="t-123", open_ids={"worker@test.local": "ou_worker"})
    oid = await feishu_bot.resolve_open_id("worker@test.local", client=client)
    assert oid == "ou_worker"
    assert client.calls[1]["url"].endswith("/contact/v3/users/batch_get_id?user_id_type=open_id")
    assert client.calls[1]["json"] == {"emails": ["worker@test.local"]}
    assert client.calls[1]["headers"]["Authorization"] == "Bearer t-123"


# ── 任务口径与播报 ──

def test_digest_lines_marks_overdue_and_counts(db_session, feishu_company):
    company = feishu_company["company"]
    today = manage_notify.today_str()
    payload = manage_notify.digest_lines(db_session, company, today=today)
    text = "\n".join(payload["lines"])
    assert "抖音渠道 0→30 万" in text
    assert "首条爆款脚本" in text and "逾期" in text
    assert "复盘 SOP" in text
    assert payload["counts"]["tasks"] == 3
    assert payload["counts"]["overdue"] == 1
    assert payload["template"] == "orange"
    assert today in payload["title"] and company.name in payload["title"]
    assert "合计：未完成 3" in text


def test_task_line_marks_due_today(db_session, feishu_company):
    node = feishu_company["soon"]
    line = manage_notify.task_line(node, today=node.end_at)
    assert "今天到期" in line and "进行中 10%" in line and "林珂" in line


def test_member_lines_only_own_tasks(db_session, feishu_company):
    company, worker, idle = feishu_company["company"], feishu_company["worker"], feishu_company["idle"]
    mine = "\n".join(manage_notify.member_lines(db_session, company, worker))
    assert "首条爆款脚本" in mine and "本周成片交付" in mine and "复盘 SOP" not in mine
    theirs = "\n".join(manage_notify.member_lines(db_session, company, idle))
    assert "复盘 SOP" in theirs and "首条爆款脚本" not in theirs


@pytest.mark.asyncio
async def test_send_daily_digest_group_card_only(monkeypatch, db_session, feishu_company):
    monkeypatch.setenv("MANAGE_FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/TEST")
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    client = _Client()
    res = await manage_notify.send_daily_digest(db_session, feishu_company["company"], client=client)
    assert res["group"]["ok"] is True and res["private"] == 0
    assert len(client.calls) == 1
    assert "首条爆款脚本" in client.calls[0]["json"]["card"]["elements"][0]["text"]["content"]


@pytest.mark.asyncio
async def test_send_daily_digest_private_push(monkeypatch, db_session, feishu_company):
    monkeypatch.setenv("MANAGE_FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/TEST")
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "sec_test")
    client = _Client(open_ids={"worker@test.local": "ou_worker", "idle@test.local": "ou_idle"})
    res = await manage_notify.send_daily_digest(db_session, feishu_company["company"], client=client)
    assert client.calls[0]["url"].endswith("/TEST")            # 先发群卡片
    msgs = [c for c in client.calls if "/im/v1/messages" in c["url"]]
    assert len(msgs) == 2                                     # 林珂、周研各一条私聊
    assert all("/im/v1/messages?receive_id_type=open_id" in c["url"] for c in msgs)
    assert all(c["headers"].get("Authorization") == "Bearer t-1" for c in msgs)
    bodies = {}
    for c in msgs:
        content = json.loads(c["json"]["content"])
        assert "你的任务" in content["header"]["title"]["content"]
        bodies[c["json"]["receive_id"]] = content["elements"][0]["text"]["content"]
    worker_msg = bodies["ou_worker"]
    assert "首条爆款脚本" in worker_msg and "复盘 SOP" not in worker_msg
    assert "复盘 SOP" in bodies["ou_idle"] and "首条爆款脚本" not in bodies["ou_idle"]
    assert res["private"] == 2


@pytest.mark.asyncio
async def test_notify_overdue_only_skips_when_clean(monkeypatch, db_session, feishu_company):
    monkeypatch.setenv("MANAGE_FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/TEST")
    client = _Client()
    node = feishu_company["overdue"]
    node.end_at = _day(5)
    node.status = "not_started"
    db_session.commit()
    res = await manage_notify.notify_overdue_only(db_session, feishu_company["company"], client=client)
    assert res["ok"] is False and res["skipped"] == "no overdue"
    assert client.calls == []


@pytest.mark.asyncio
async def test_notify_overdue_only_sends_red_card(monkeypatch, db_session, feishu_company):
    monkeypatch.setenv("MANAGE_FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/TEST")
    client = _Client()
    res = await manage_notify.notify_overdue_only(db_session, feishu_company["company"], client=client)
    assert res["ok"] is True
    card = client.calls[0]["json"]["card"]
    assert card["header"]["template"] == "red"
    assert "逾期 1 条" in card["elements"][0]["text"]["content"]


@pytest.mark.asyncio
async def test_notify_task_assigned_card(monkeypatch, db_session, feishu_company):
    monkeypatch.setenv("MANAGE_FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/TEST")
    client = _Client()
    res = await manage_notify.notify_task_assigned(db_session, feishu_company["overdue"],
                                                   project=feishu_company["project"],
                                                   actor_name="老板", client=client)
    assert res["ok"] is True
    text = client.calls[0]["json"]["card"]["elements"][0]["text"]["content"]
    assert "抖音渠道 0→30 万" in text and "首条爆款脚本" in text and "林珂" in text and "老板" in text


@pytest.mark.asyncio
async def test_notify_all_daily_skips_without_config(monkeypatch, db_session, feishu_company):
    for name in ("MANAGE_FEISHU_WEBHOOK", "LOBSTER_MANAGE_FEISHU_WEBHOOK", "FEISHU_APP_ID", "FEISHU_APP_SECRET"):
        monkeypatch.delenv(name, raising=False)
    out = await manage_notify.notify_all_daily(db_session, client=_Client())
    assert out["skipped"] >= 1 and out["sent"] == 0


def test_status_and_overdue_helpers():
    from backend.app.manage_models import MPlanNode as N
    today = manage_notify.today_str()
    done = N(title="x", status="completed", end_at=_day(-9), progress=100)
    late = N(title="y", status="in_progress", end_at=_day(-3), progress=10)
    future = N(title="z", status="not_started", end_at=_day(9))
    assert manage_notify.is_done(done) and not manage_notify.is_overdue(done, today)
    assert manage_notify.is_overdue(late, today) and manage_notify.overdue_days(late, today) == 3
    assert not manage_notify.is_overdue(future, today)
    assert manage_notify.task_line(late, today=today).startswith("⚠️")

@pytest.mark.asyncio
async def test_resolve_open_id_surfaces_permission_error(monkeypatch):
    """飞书用 400+99991672 报权限没开时，必须抛错并带出处置提示，不能静默成"查不到人"。"""
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "sec_test")
    denied = _Resp({"code": 99991672, "msg": "Access denied. One of the following scopes is required: [contact:user.id:readonly]"}, status_code=400)
    client = _Client(resp=denied)
    with pytest.raises(RuntimeError) as exc:
        await feishu_bot.resolve_open_id("15829455388", kind="mobile", client=client)
    assert "99991672" in str(exc.value)
    hint = feishu_bot.hint_for(exc.value)
    assert "contact:user.id:readonly" in hint and "im:message" in hint


@pytest.mark.asyncio
async def test_batch_resolve_open_ids_parses_real_payload():
    """用 2026-10-12 生产抓到的真实返回：8 个手机号里 5 个有 user_id、3 个只有 mobile。"""
    real = {
        "code": 0, "msg": "success",
        "data": {"user_list": [
            {"mobile": "15829455388", "user_id": "ou_22d8bc1386075a1ea7f0bd9f06d0feda"},
            {"mobile": "13060158503"},
            {"mobile": "17877109975", "user_id": "ou_52b3c3f55347626db104a6207cb5a57f"},
            {"mobile": "18665877911", "user_id": "ou_583c66b7473113e5c7adfdc4fb204182"},
            {"mobile": "13927485337", "user_id": "ou_20d69b4a35b0a8c4e96f4d2df230b585"},
            {"mobile": "18711047715"},
            {"mobile": "15683020857", "user_id": "ou_c9018722670c2c3ed90b4a37a8a7774e"},
            {"mobile": "18038025066"},
        ]},
    }
    client = _Client(resp=_Resp(real))
    found = await feishu_bot.batch_resolve_open_ids(
        mobiles=["15829455388", "13060158503", "17877109975", "18665877911",
                 "13927485337", "18711047715", "15683020857", "18038025066"], client=client)
    assert len(found) == 5
    assert found["15829455388"].startswith("ou_22d8")
    assert "13060158503" not in found and "18711047715" not in found and "18038025066" not in found
    body = client.calls[0]["json"]
    assert body["mobiles"][0] == "15829455388" and len(body["mobiles"]) == 8
    assert "include_resigned" not in body          # 不带离职，避免拿到重复/离职账号


@pytest.mark.asyncio
async def test_batch_resolve_open_ids_empty_and_error(monkeypatch):
    assert await feishu_bot.batch_resolve_open_ids() == {}
    monkeypatch.setenv("FEISHU_APP_ID", "cli_x")
    monkeypatch.setenv("FEISHU_APP_SECRET", "sec_x")
    denied = _Resp({"code": 99991672, "msg": "scope required: contact:user.id:readonly"}, status_code=400)
    with pytest.raises(RuntimeError) as exc:
        await feishu_bot.batch_resolve_open_ids(mobiles=["15829455388"], client=_Client(resp=denied))
    assert "99991672" in str(exc.value)
