"""设备备注（界面上的设备名）按机器身份保存 的回归测试。

需求（2026-09-17）：
  用户给设备起的备注（例如「全天抖音获客」）以前只挂在 installation_id 上，
  客户端一换槽位（换账号登录、换品牌、OTA 后新的签名槽位）名字就丢，
  界面上退回默认名 local-online，看起来像「备注没了」。
  现在改成挂 machine_instance_id：换槽位后自动沿用；机器身份也变了时给「沿用建议」。

覆盖：
1. 默认名（空 / local-online / online / 在线设备 / 本机）不算备注，不写库；
2. 同一台机器换槽位后仍解析出备注（label_machine），新槽位心跳时自动套用（adopt）；
3. 拿不到机器身份时退化为 slot: 键，仍按槽位解析；
4. 人工改名会把该机器已知槽位一起改名（apply_label_to_machine_slots）；
5. 沿用建议：只在「源槽位离线 ≥60 分钟、且 30 天内改过、当前槽位没备注」时给出；
6. H5 改名 / 清空 / 设备列表接口的返回字段；
7. manage 设置设备名端点：403（非老板/总）→ 404（设备不存在）→ 409（没有心跳）→ 成功，
   以及虚拟员工列表 JSON 带 device_label / suggested_label；
8. 迁移入口已注册 + 补录幂等（同一台机器重复补录只留一行）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from backend.app.api import h5_chat as h5_api
from backend.app.api import manage as manage_api
from backend.app.manage_models import MAiEmployee, MCompany, MMembership, MMembershipRole
from backend.app.models import H5ChatDevicePresence, UserDeviceLabel, UserMachineIdentity
from backend.app.services import device_labels


MACHINE = "m19ffd70db8e869078ff8ba940d0846b41b168e8d398"
SLOT_OLD = "u54-c2ba02746bf6de5f5254d8639b5aef9c"
SLOT_NEW = "u54-d9f12546ac13d514db14a89504d362b4"
DEVICE_NAME = "全天抖音获客"


def _bind_machine(db, user_id: int, machine: str, slot: str, seen: datetime | None = None):
    """客户端登录/续签时上报的机器身份映射（一台机器一行，槽位变化时就地更新）。"""
    row = UserMachineIdentity(
        user_id=user_id,
        machine_instance_id=machine,
        installation_id=slot,
        last_seen_at=seen or datetime.utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def _presence(db, user_id: int, slot: str, name: str | None = None, seen: datetime | None = None):
    row = H5ChatDevicePresence(
        user_id=user_id,
        installation_id=slot,
        display_name=name,
        last_seen_at=seen or datetime.utcnow(),
    )
    db.add(row)
    db.flush()
    return row


# —— 1. 默认名不算备注 ——

@pytest.mark.parametrize("value", ["", "   ", None, "local-online", "LOCAL-ONLINE", "online", "在线设备", "本机"])
def test_default_names_are_not_custom_labels(value):
    assert device_labels.is_custom_label(value) is False


@pytest.mark.parametrize("value", [DEVICE_NAME, "全天内容创作", "全天微信托管", "前台 01"])
def test_user_names_are_custom_labels(value):
    assert device_labels.is_custom_label(value) is True


# —— 2. 备注跟机器走：换槽位后仍解析得到 + 心跳自动沿用 ——

def test_label_follows_machine_across_slot_change(db_session, test_user):
    _bind_machine(db_session, test_user.id, MACHINE, SLOT_OLD)
    _presence(db_session, test_user.id, SLOT_OLD, DEVICE_NAME)
    db_session.commit()

    # 迁移补录：把历史 presence 上的备注按机器身份建档（create_app 启动时会跑一遍）
    label = device_labels.remember_device_label(
        db_session, user_id=test_user.id, installation_id=SLOT_OLD,
        display_name=DEVICE_NAME, source="manual",
    )
    db_session.commit()
    assert label is not None
    assert label.label_key == MACHINE
    assert db_session.query(UserDeviceLabel).count() == 1

    # 换槽位：同一台机器拿到新的签名槽位（u54-c2ba… → u54-d9f1…）
    ident = (
        db_session.query(UserMachineIdentity)
        .filter(UserMachineIdentity.user_id == test_user.id)
        .one()
    )
    ident.installation_id = SLOT_NEW
    ident.last_seen_at = datetime.utcnow()
    _presence(db_session, test_user.id, SLOT_NEW, None)  # 新槽位那行是空的（默认名）
    db_session.commit()

    assert device_labels.machine_instance_id_for(db_session, test_user.id, SLOT_NEW) == MACHINE
    name, source = device_labels.resolve_device_label(db_session, test_user.id, SLOT_NEW)
    assert (name, source) == (DEVICE_NAME, "label_machine")

    # 心跳：presence 没有自定义名时自动套用同一台机器的备注
    adopted = device_labels.adopt_device_label(db_session, test_user.id, SLOT_NEW)
    db_session.commit()
    assert adopted == DEVICE_NAME
    new_row = (
        db_session.query(H5ChatDevicePresence)
        .filter(
            H5ChatDevicePresence.user_id == test_user.id,
            H5ChatDevicePresence.installation_id == SLOT_NEW,
        )
        .one()
    )
    assert new_row.display_name == DEVICE_NAME
    # 已经改过名的槽位不会被覆盖
    assert device_labels.adopt_device_label(db_session, test_user.id, SLOT_NEW) == DEVICE_NAME


def test_heartbeat_endpoint_returns_adopted_device_name(db_session, test_user):
    """心跳接口本身就带自动沿用：客户端换槽位上线后返回体里的 device_name 就是备注名。"""
    from starlette.requests import Request

    _bind_machine(db_session, test_user.id, MACHINE, SLOT_OLD)
    device_labels.remember_device_label(
        db_session, user_id=test_user.id, installation_id=SLOT_OLD,
        display_name=DEVICE_NAME, source="manual",
    )
    ident = (
        db_session.query(UserMachineIdentity)
        .filter(UserMachineIdentity.user_id == test_user.id)
        .one()
    )
    ident.installation_id = SLOT_NEW
    db_session.commit()

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/h5-chat/device-heartbeat",
            "headers": [(b"x-installation-id", SLOT_NEW.encode())],
            "query_string": b"",
            "server": ("testserver", 80),
            "scheme": "http",
            "client": ("127.0.0.1", 12345),
        }
    )
    body = h5_api.H5HeartbeatIn(display_name=None)
    result = h5_api.h5_device_heartbeat(
        body, request, current_user_id=test_user.id, db=db_session
    )
    assert result["ok"] is True
    assert result["device_name"] == DEVICE_NAME


# —— 3. 没有机器身份时退化为 slot: 键 ——

def test_slot_key_fallback_without_machine_identity(db_session, test_user):
    _presence(db_session, test_user.id, SLOT_OLD, None)
    device_labels.remember_device_label(
        db_session, user_id=test_user.id, installation_id=SLOT_OLD,
        display_name=DEVICE_NAME, source="manual",
    )
    db_session.commit()

    row = db_session.query(UserDeviceLabel).one()
    assert row.label_key == f"slot:{SLOT_OLD}"
    assert row.machine_instance_id is None
    assert device_labels.resolve_device_label(db_session, test_user.id, SLOT_OLD) == (DEVICE_NAME, "label_slot")
    # 没有机器身份就联系不上新槽位（此时靠「沿用建议」兜底）
    assert device_labels.resolve_device_label(db_session, test_user.id, SLOT_NEW) == ("", "none")


def test_remember_is_idempotent_per_machine(db_session, test_user):
    _bind_machine(db_session, test_user.id, MACHINE, SLOT_OLD)
    _presence(db_session, test_user.id, SLOT_OLD, None)
    for _ in range(3):
        device_labels.remember_device_label(
            db_session, user_id=test_user.id, installation_id=SLOT_OLD,
            display_name=DEVICE_NAME, source="manual",
        )
    db_session.commit()
    rows = db_session.query(UserDeviceLabel).all()
    assert len(rows) == 1
    assert rows[0].display_name == DEVICE_NAME
    # 改名只更新那一行
    device_labels.remember_device_label(
        db_session, user_id=test_user.id, installation_id=SLOT_OLD,
        display_name="全天内容创作", source="manual",
    )
    db_session.commit()
    rows = db_session.query(UserDeviceLabel).all()
    assert len(rows) == 1 and rows[0].display_name == "全天内容创作"


# —— 4. 改名会同步该机器已知的槽位 ——

def test_rename_propagates_to_known_slots_of_machine(db_session, test_user):
    """先给老槽位起名，再换到新槽位，然后在老槽位上改名：新槽位也跟着改名。"""
    ident = _bind_machine(db_session, test_user.id, MACHINE, SLOT_OLD)
    _presence(db_session, test_user.id, SLOT_OLD, None)
    db_session.commit()

    h5_api.h5_update_device_display_name(
        SLOT_OLD,
        h5_api.H5DeviceDisplayNameIn(display_name=DEVICE_NAME),
        current_user=test_user,
        db=db_session,
    )
    db_session.commit()
    assert db_session.query(UserDeviceLabel).one().label_key == MACHINE

    # 同一台机器换到新槽位，机器身份表那行就地更新
    ident.installation_id = SLOT_NEW
    ident.last_seen_at = datetime.utcnow()
    _presence(db_session, test_user.id, SLOT_NEW, None)
    db_session.commit()

    # 老槽位那行还在列表里，用户在它上面又改了一次名
    h5_api.h5_update_device_display_name(
        SLOT_OLD,
        h5_api.H5DeviceDisplayNameIn(display_name="全天内容创作"),
        current_user=test_user,
        db=db_session,
    )
    db_session.commit()
    names = {
        row.installation_id: row.display_name
        for row in db_session.query(H5ChatDevicePresence).all()
    }
    assert names[SLOT_OLD] == "全天内容创作"
    assert names[SLOT_NEW] == "全天内容创作"  # 同一台机器已知的槽位一起改


def test_legacy_slot_label_upgraded_to_machine_key(db_session, test_user):
    """老数据（改名时没有机器身份，按 slot: 存）：客户端上报机器身份后升级为按机器存。"""
    _presence(db_session, test_user.id, SLOT_OLD, DEVICE_NAME)
    device_labels.remember_device_label(
        db_session, user_id=test_user.id, installation_id=SLOT_OLD,
        display_name=DEVICE_NAME, source="manual",
    )
    db_session.commit()
    assert db_session.query(UserDeviceLabel).one().label_key == f"slot:{SLOT_OLD}"

    # 客户端重新登录，机器身份上报上来（槽位没变）
    _bind_machine(db_session, test_user.id, MACHINE, SLOT_OLD)
    db_session.commit()
    assert device_labels.adopt_device_label(db_session, test_user.id, SLOT_OLD) == DEVICE_NAME
    db_session.commit()
    row = db_session.query(UserDeviceLabel).one()
    assert row.label_key == MACHINE and row.machine_instance_id == MACHINE

    # 之后这台机器换槽位也能沿用
    ident = (
        db_session.query(UserMachineIdentity)
        .filter(UserMachineIdentity.user_id == test_user.id)
        .one()
    )
    ident.installation_id = SLOT_NEW
    db_session.commit()
    assert device_labels.resolve_device_label(db_session, test_user.id, SLOT_NEW) == (DEVICE_NAME, "label_machine")


def test_legacy_label_without_machine_link_falls_back_to_suggestion(db_session, test_user):
    """机器身份也换了、又没法把老槽位和它连起来时，只能给「沿用建议」（人工确认一次）。"""
    _presence(db_session, test_user.id, SLOT_OLD, None, seen=datetime.utcnow() - timedelta(days=1))
    device_labels.remember_device_label(
        db_session, user_id=test_user.id, installation_id=SLOT_OLD,
        display_name=DEVICE_NAME, source="manual",
    )
    _bind_machine(db_session, test_user.id, MACHINE, SLOT_NEW)
    _presence(db_session, test_user.id, SLOT_NEW, None)
    db_session.commit()

    assert device_labels.resolve_device_label(db_session, test_user.id, SLOT_NEW) == ("", "none")
    suggestion = device_labels.suggest_device_label(
        db_session, user_id=test_user.id, installation_id=SLOT_NEW
    )
    assert suggestion is not None and suggestion["display_name"] == DEVICE_NAME
    # 用户确认沿用（=在新槽位上改名）后，备注挂到机器身份上
    h5_api.h5_update_device_display_name(
        SLOT_NEW,
        h5_api.H5DeviceDisplayNameIn(display_name=DEVICE_NAME),
        current_user=test_user,
        db=db_session,
    )
    db_session.commit()
    rows = db_session.query(UserDeviceLabel).all()
    assert [r.label_key for r in rows].count(MACHINE) == 1
    assert [r for r in rows if r.label_key == MACHINE][0].display_name == DEVICE_NAME
    assert device_labels.suggest_device_label(
        db_session, user_id=test_user.id, installation_id=SLOT_NEW
    ) is None


# —— 5. 沿用建议 ——

def test_suggestion_only_when_source_slot_offline(db_session, test_user):
    _presence(db_session, test_user.id, SLOT_NEW, None)          # 当前设备（没备注）
    _presence(db_session, test_user.id, SLOT_OLD, None, seen=datetime.utcnow())  # 旧槽位还活着
    device_labels.remember_device_label(
        db_session, user_id=test_user.id, installation_id=SLOT_OLD,
        display_name=DEVICE_NAME, source="manual",
    )
    db_session.commit()

    assert device_labels.suggest_device_label(
        db_session, user_id=test_user.id, installation_id=SLOT_NEW
    ) is None

    old = (
        db_session.query(H5ChatDevicePresence)
        .filter(H5ChatDevicePresence.installation_id == SLOT_OLD)
        .one()
    )
    old.last_seen_at = datetime.utcnow() - timedelta(hours=3)
    db_session.commit()

    suggestion = device_labels.suggest_device_label(
        db_session, user_id=test_user.id, installation_id=SLOT_NEW
    )
    assert suggestion is not None
    assert suggestion["display_name"] == DEVICE_NAME
    assert suggestion["from_installation_id"] == SLOT_OLD
    assert suggestion["idle_minutes"] >= 60


def test_suggestion_skipped_when_current_slot_has_label_or_label_too_old(db_session, test_user):
    _presence(db_session, test_user.id, SLOT_NEW, None)
    _presence(db_session, test_user.id, SLOT_OLD, None, seen=datetime.utcnow() - timedelta(days=5))
    row = device_labels.remember_device_label(
        db_session, user_id=test_user.id, installation_id=SLOT_OLD,
        display_name=DEVICE_NAME, source="manual",
    )
    row.updated_at = datetime.utcnow() - timedelta(days=40)
    db_session.commit()
    assert device_labels.suggest_device_label(
        db_session, user_id=test_user.id, installation_id=SLOT_NEW
    ) is None

    row.updated_at = datetime.utcnow()
    db_session.commit()
    assert device_labels.suggest_device_label(
        db_session, user_id=test_user.id, installation_id=SLOT_NEW
    ) is not None
    # 当前槽位自己已经有备注 → 不再建议
    assert device_labels.suggest_device_label(
        db_session, user_id=test_user.id, installation_id=SLOT_OLD
    ) is None


def test_suggestion_list_keeps_several_candidates(db_session, test_user):
    """机器身份也换过时（连不上），给出多条候选让界面选，而不是硬猜一条。"""
    slot_a = "u54-c2ba02746bf6de5f5254d8639b5aef9c"
    slot_b = "u54-568517064ad7a0907558ce559ba23d05"
    _presence(db_session, test_user.id, slot_a, None, seen=datetime.utcnow() - timedelta(days=3))
    _presence(db_session, test_user.id, slot_b, None, seen=datetime.utcnow() - timedelta(days=5))
    first = device_labels.remember_device_label(
        db_session, user_id=test_user.id, installation_id=slot_a,
        display_name=DEVICE_NAME, source="manual",
    )
    first.updated_at = datetime.utcnow() - timedelta(hours=1)
    second = device_labels.remember_device_label(
        db_session, user_id=test_user.id, installation_id=slot_b,
        display_name="全天内容创作", source="manual",
    )
    second.updated_at = datetime.utcnow() - timedelta(hours=2)
    _presence(db_session, test_user.id, SLOT_NEW, None)
    db_session.commit()

    items = device_labels.suggest_device_labels(
        db_session, user_id=test_user.id, installation_id=SLOT_NEW, limit=5
    )
    assert [i["display_name"] for i in items] == [DEVICE_NAME, "全天内容创作"]
    assert device_labels.suggest_device_label(
        db_session, user_id=test_user.id, installation_id=SLOT_NEW
    )["display_name"] == DEVICE_NAME
    # 当前槽位一旦有备注，候选清空
    device_labels.remember_device_label(
        db_session, user_id=test_user.id, installation_id=SLOT_NEW,
        display_name="前台 01", source="manual",
    )
    db_session.commit()
    assert device_labels.suggest_device_labels(
        db_session, user_id=test_user.id, installation_id=SLOT_NEW
    ) == []


# —— 6. H5 接口 ——

def test_h5_devices_status_reports_machine_label(db_session, test_user):
    ident = _bind_machine(db_session, test_user.id, MACHINE, SLOT_OLD)
    _presence(db_session, test_user.id, SLOT_OLD, DEVICE_NAME, seen=datetime.utcnow() - timedelta(hours=6))
    device_labels.remember_device_label(
        db_session, user_id=test_user.id, installation_id=SLOT_OLD,
        display_name=DEVICE_NAME, source="manual",
    )
    # 同一台机器换到新槽位（u54-c2ba… → u54-d9f1…），新槽位那行还是默认名
    ident.installation_id = SLOT_NEW
    ident.last_seen_at = datetime.utcnow()
    _presence(db_session, test_user.id, SLOT_NEW, None)
    # 第三台设备：既没备注也没建议（旧槽位已改名，被排除了）
    other_slot = "u54-44b4e96470be7deced015248f3329818"
    _presence(db_session, test_user.id, other_slot, None)
    db_session.commit()

    payload = h5_api.h5_devices_status(current_user=test_user, db=db_session)
    by_slot = {d["installation_id"]: d for d in payload["devices"]}
    assert by_slot[SLOT_NEW]["display_name"] == DEVICE_NAME
    assert by_slot[SLOT_NEW]["label_source"] == "label_machine"
    assert by_slot[SLOT_NEW]["online"] is True
    assert by_slot[other_slot]["display_name"] == "local-online"
    assert by_slot[other_slot]["label_source"] == "none"


def test_h5_devices_status_offers_suggestion(db_session, test_user):
    _presence(db_session, test_user.id, SLOT_NEW, None)
    _presence(db_session, test_user.id, SLOT_OLD, None, seen=datetime.utcnow() - timedelta(days=2))
    device_labels.remember_device_label(
        db_session, user_id=test_user.id, installation_id=SLOT_OLD,
        display_name=DEVICE_NAME, source="manual",
    )
    db_session.commit()
    payload = h5_api.h5_devices_status(current_user=test_user, db=db_session)
    by_slot = {d["installation_id"]: d for d in payload["devices"]}
    assert by_slot[SLOT_NEW]["display_name"] == "local-online"
    assert by_slot[SLOT_NEW]["suggested_label"]["display_name"] == DEVICE_NAME
    assert [s["display_name"] for s in by_slot[SLOT_NEW]["suggested_labels"]] == [DEVICE_NAME]


def test_h5_rename_and_clear(db_session, test_user):
    _bind_machine(db_session, test_user.id, MACHINE, SLOT_OLD)
    _presence(db_session, test_user.id, SLOT_OLD, None)
    db_session.commit()

    result = h5_api.h5_update_device_display_name(
        SLOT_OLD,
        h5_api.H5DeviceDisplayNameIn(display_name=DEVICE_NAME),
        current_user=test_user,
        db=db_session,
    )
    assert result["device"]["display_name"] == DEVICE_NAME
    assert result["device"]["label_source"] == "label_machine"
    assert db_session.query(UserDeviceLabel).count() == 1

    cleared = h5_api.h5_update_device_display_name(
        SLOT_OLD,
        h5_api.H5DeviceDisplayNameIn(display_name=""),
        current_user=test_user,
        db=db_session,
    )
    assert cleared["device"]["display_name"] == "local-online"
    assert cleared["device"]["label_source"] == "none"
    assert db_session.query(UserDeviceLabel).count() == 0

    with pytest.raises(HTTPException) as excinfo:
        h5_api.h5_update_device_display_name(
            "u54-nope",
            h5_api.H5DeviceDisplayNameIn(display_name=DEVICE_NAME),
            current_user=test_user,
            db=db_session,
        )
    assert excinfo.value.status_code == 404


# —— 7. manage 端点 ——

@pytest.fixture
def manage_device(db_session, test_user):
    company = MCompany(name="必火科技（测试）", short_name="必火", owner_user_id=test_user.id)
    db_session.add(company)
    db_session.flush()
    employee = MAiEmployee(company_id=company.id, name="抖音获客机", installation_id=SLOT_NEW,
                           source="slot", created_by=test_user.id)
    db_session.add(employee)
    db_session.flush()
    sales = MMembership(company_id=company.id, display_name="小林", dept="业务中心",
                        email="sales@test.local", user_id=2001, status="active")
    db_session.add(sales)
    db_session.flush()
    db_session.add(MMembershipRole(company_id=company.id, membership_id=sales.id,
                                   role_code="sales", level="p3"))
    db_session.commit()
    sales_actor = type("_U", (), {"id": 2001, "email": "sales@test.local", "role": "user",
                                  "brand_mark": "bihuo"})()
    return {"company": company, "employee": employee, "sales_actor": sales_actor}


def test_manage_device_label_requires_plan_admin(db_session, manage_device):
    with pytest.raises(HTTPException) as excinfo:
        manage_api.set_ai_employee_device_label(
            manage_device["employee"].id,
            manage_api.DeviceLabelIn(display_name=DEVICE_NAME),
            user=manage_device["sales_actor"],
            db=db_session,
        )
    assert excinfo.value.status_code == 403


def test_manage_device_label_404_and_409(db_session, manage_device):
    admin = manage_api.AdminActor("admin")
    with pytest.raises(HTTPException) as excinfo:
        manage_api.set_ai_employee_device_label(
            999999,
            manage_api.DeviceLabelIn(display_name=DEVICE_NAME),
            user=admin,
            db=db_session,
        )
    assert excinfo.value.status_code == 404

    # 还没心跳过（没有 presence）→ 先让设备上线
    with pytest.raises(HTTPException) as excinfo:
        manage_api.set_ai_employee_device_label(
            manage_device["employee"].id,
            manage_api.DeviceLabelIn(display_name=DEVICE_NAME),
            user=admin,
            db=db_session,
        )
    assert excinfo.value.status_code == 409
    assert "上线" in str(excinfo.value.detail)


def test_manage_device_label_success_and_ai_row_json(db_session, manage_device, test_user):
    admin = manage_api.AdminActor("admin")
    _bind_machine(db_session, test_user.id, MACHINE, SLOT_NEW)
    _presence(db_session, test_user.id, SLOT_NEW, None)
    db_session.commit()

    result = manage_api.set_ai_employee_device_label(
        manage_device["employee"].id,
        manage_api.DeviceLabelIn(display_name=DEVICE_NAME),
        user=admin,
        db=db_session,
    )
    assert result["ok"] is True
    assert result["device_label"] == DEVICE_NAME
    row = db_session.query(UserDeviceLabel).one()
    assert row.label_key == MACHINE and row.display_name == DEVICE_NAME
    presence = (
        db_session.query(H5ChatDevicePresence)
        .filter(H5ChatDevicePresence.installation_id == SLOT_NEW)
        .one()
    )
    assert presence.display_name == DEVICE_NAME

    payload = manage_api._ai_row_json(
        db_session, manage_device["company"], manage_device["employee"], None, {}
    )
    assert payload["device_label"] == DEVICE_NAME
    assert payload["device_label_source"] == "label_machine"
    assert payload["suggested_label"] is None

    # 清空备注：presence 与 label 一起清掉，列表回到默认名
    manage_api.set_ai_employee_device_label(
        manage_device["employee"].id,
        manage_api.DeviceLabelIn(display_name=""),
        user=admin,
        db=db_session,
    )
    payload = manage_api._ai_row_json(
        db_session, manage_device["company"], manage_device["employee"], None, {}
    )
    assert payload["device_label"] == ""
    assert payload["device_label_source"] == "none"


def test_ai_row_json_suggests_label_when_slot_renamed(db_session, manage_device, test_user):
    """老槽位改过名、这台机器又换了新槽位（机器身份也变了）→ 列表里给沿用建议。"""
    _presence(db_session, test_user.id, SLOT_OLD, None, seen=datetime.utcnow() - timedelta(days=3))
    device_labels.remember_device_label(
        db_session, user_id=test_user.id, installation_id=SLOT_OLD,
        display_name=DEVICE_NAME, source="manual",
    )
    _presence(db_session, test_user.id, SLOT_NEW, None)
    db_session.commit()
    payload = manage_api._ai_row_json(
        db_session, manage_device["company"], manage_device["employee"], None, {}
    )
    assert payload["device_label"] == ""
    assert payload["suggested_label"]["display_name"] == DEVICE_NAME
    assert payload["suggested_label"]["from_installation_id"] == SLOT_OLD


# —— 8. 迁移 ——

def test_device_labels_table_registered_and_migration_wired():
    from backend.app import create_app as create_app_module
    from backend.app.db import Base

    assert "user_device_labels" in Base.metadata.tables
    source = open(create_app_module.__file__, encoding="utf-8").read()
    assert "def _migrate_device_labels()" in source
    assert "_migrate_device_labels()\n" in source  # 启动迁移列表里真的调用了


def test_backfill_counts_only_custom_names(db_session, test_user):
    """迁移补录：只有自定义名才进新表（默认名 local-online 之类跳过）。"""
    _bind_machine(db_session, test_user.id, MACHINE, SLOT_OLD)
    _presence(db_session, test_user.id, SLOT_OLD, DEVICE_NAME)
    _presence(db_session, test_user.id, "u54-00000000000000000000000000000001", "local-online")
    db_session.commit()

    migrated = 0
    for row in db_session.query(H5ChatDevicePresence).all():
        if not device_labels.is_custom_label(row.display_name):
            continue
        if device_labels.remember_device_label(
            db_session, user_id=int(row.user_id), installation_id=str(row.installation_id),
            display_name=str(row.display_name), source="manual",
        ) is not None:
            migrated += 1
    db_session.commit()
    assert migrated == 1
    assert db_session.query(UserDeviceLabel).count() == 1
