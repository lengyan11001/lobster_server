"""manage.bhzn.top「库存（实体设备）」与「财务报表识图」回归测试（2026-09-26）。

需求：产品 = 对外卖的东西；库存 = 公司手里真实存在的设备。
覆盖点：
1. 库存增 / 查 / 改 / 删，以及汇总（总数、在库、在用、成本）；
2. 跨公司隔离（不是自己公司 → 404）；
3. 票据识图字段归一化（发票金额/日期/分类/方向）；
4. /api/manage/finance/scan 的装配：成功返回 fields；视觉模型失败 → 502 且带原因。
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from backend.app.api import manage as manage_api
from backend.app.services import document_scan


def _company(db, owner_id: int, name: str = "测试公司"):
    from backend.app.manage_models import MCompany

    row = MCompany(name=name, owner_user_id=owner_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _other_user(db, email: str = "bob@test.local"):
    from decimal import Decimal

    from backend.app.models import User

    user = User(email=email, hashed_password="x", credits=Decimal("0"), role="user",
                preferred_model="sutui")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_inventory_crud_and_summary(db_session, test_user):
    company = _company(db_session, test_user.id)

    created = manage_api.create_inventory(
        manage_api.InventoryIn(company_id=company.id, name="必火盒子", model_name="X100", sku="SN-0001",
                               quantity=3, unit="台", unit_cost=1200, location="深圳仓A架", keeper="张三"),
        user=test_user, db=db_session)
    assert created["ok"] is True
    item_id = created["item_id"]
    assert created["item"]["status"] == "in_stock"

    manage_api.create_inventory(
        manage_api.InventoryIn(company_id=company.id, name="采集手机", quantity=2, unit_cost=800,
                               status="in_use"),
        user=test_user, db=db_session)

    listed = manage_api.list_inventory(company_id=company.id, user=test_user, db=db_session)
    assert listed["summary"]["quantity"] == 5
    assert listed["summary"]["kinds"] == 2
    assert listed["summary"]["in_stock"] == 3
    assert listed["summary"]["in_use"] == 2
    assert listed["summary"]["value"] == 5200.0
    assert {i["name"] for i in listed["items"]} == {"必火盒子", "采集手机"}

    patched = manage_api.update_inventory(
        item_id, manage_api.InventoryPatchIn(company_id=company.id, status="repair", keeper="李四"),
        user=test_user, db=db_session)
    assert patched["item"]["status"] == "repair"
    assert patched["item"]["keeper"] == "李四"
    assert patched["item"]["quantity"] == 3  # 没传的字段不动

    deleted = manage_api.delete_inventory(item_id, user=test_user, db=db_session)
    assert deleted["deleted"] == item_id
    left = manage_api.list_inventory(company_id=company.id, user=test_user, db=db_session)
    assert left["summary"]["quantity"] == 2
    assert left["summary"]["repair"] == 0


def test_inventory_isolation_between_companies(db_session, test_user):
    other = _other_user(db_session)
    other_company = _company(db_session, other.id, "别人公司")
    mine = _company(db_session, test_user.id, "我的公司")
    manage_api.create_inventory(
        manage_api.InventoryIn(company_id=other_company.id, name="别人的盒子", quantity=1),
        user=other, db=db_session)

    with pytest.raises(HTTPException) as err:
        manage_api.list_inventory(company_id=other_company.id, user=test_user, db=db_session)
    assert err.value.status_code == 404

    mine_items = manage_api.list_inventory(company_id=mine.id, user=test_user, db=db_session)
    assert mine_items["summary"]["quantity"] == 0


def test_scan_fields_normalisation():
    fields = document_scan._norm_fields({
        "doc_type": "invoice", "amount": "¥1,234.56", "date": "2026-09-01", "party": "阿里云",
        "title": "阿里云 9 月服务器费", "items": "ECS 包月", "invoice_no": "12345678",
        "category": "软件服务", "direction": "expense", "confidence": "0.92",
    })
    assert fields["amount"] == 1234.56
    assert fields["date"] == "2026-09-01"
    assert fields["doc_type_label"] == "发票"
    assert fields["category"] == "软件服务"
    assert fields["entry_type"] == "expense"
    assert fields["summary"] == "阿里云 9 月服务器费"
    assert fields["confidence"] == 0.92

    messy = document_scan._norm_fields({"doc_type": "发票?", "date": "2026/09/01", "amount": "看不清",
                                        "category": "乱写", "direction": "income"})
    assert messy["doc_type"] == "other"
    assert messy["date"] == ""
    assert messy["amount"] is None
    assert messy["category"] == "其他"
    assert messy["entry_type"] == "income"
    assert messy["summary"] == "票据"


class _FakeUpload:
    filename = "invoice.jpg"

    async def read(self) -> bytes:
        return b"\xff\xd8\xff fake-jpeg"


def test_finance_scan_endpoint_wiring(monkeypatch, db_session, test_user):
    company = _company(db_session, test_user.id)

    async def fake_ok(data: bytes, filename: str = ""):
        assert data
        return {"ok": True, "model": "stub-vl", "latency_ms": 12,
                "fields": document_scan._norm_fields({"doc_type": "bill", "amount": "88.8",
                                                      "date": "2026-09-25", "party": "滴滴出行"})}

    monkeypatch.setattr(document_scan, "scan_finance_document", fake_ok)
    out = asyncio.run(manage_api.scan_finance_bill(company_id=company.id, file=_FakeUpload(),
                                                   user=test_user, db=db_session))
    assert out["ok"] is True
    assert out["model"] == "stub-vl"
    assert out["fields"]["amount"] == 88.8
    assert out["fields"]["doc_type_label"] == "账单"

    async def fake_bad(data: bytes, filename: str = ""):
        return {"ok": False, "error": "服务端没配视觉模型密钥（DASHSCOPE_API_KEY）"}

    monkeypatch.setattr(document_scan, "scan_finance_document", fake_bad)
    with pytest.raises(HTTPException) as err:
        asyncio.run(manage_api.scan_finance_bill(company_id=company.id, file=_FakeUpload(),
                                                 user=test_user, db=db_session))
    assert err.value.status_code == 502
    assert "DASHSCOPE_API_KEY" in str(err.value.detail)
