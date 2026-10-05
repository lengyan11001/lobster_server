"""管理后台「上报微信号」：抖音私信接管上报（wechat_contact_reports）列表。

2026-10-05 口径澄清：这块只展示**抖音私信接管**识别到的客户微信号/手机号（加好友联系池），
个微聊天那条来源已回滚（微信接管对象本来就是好友，抓他的微信号没意义）。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _seed(db_session, user_id: int, value: str = "djddjdjddn", kind: str = "wechat_id", status: str = "pending",
          contact: str = "张深根 微赢共创"):
    from backend.app.models import WechatContactReport

    row = WechatContactReport(
        user_id=int(user_id), brand_mark="daka", platform="douyin", kind=kind, value=value,
        source_username=contact, source_conversation="我的微信号是 " + value,
        status=status,
    )
    db_session.add(row)
    db_session.commit()
    return row


def test_admin_lists_douyin_dm_reports(db_session, test_user):
    from backend.app.api import admin as admin_api

    _seed(db_session, test_user.id)
    listed = admin_api.admin_list_wechat_shared_contacts(
        user_id=test_user.id, kind="", q="", page=1, page_size=10, ctx=None, db=db_session)
    assert listed["ok"] is True and listed["total"] == 1
    item = listed["items"][0]
    assert item["value"] == "djddjdjddn" and item["kind"] == "wechat_id"
    assert item["platform"] == "douyin" and item["status"] == "pending"
    assert item["contact_name"] == "张深根 微赢共创"
    assert item["user_email"] == test_user.email


def test_admin_filters_by_user_kind_and_keyword(db_session, test_user, other_user):
    from backend.app.api import admin as admin_api

    _seed(db_session, test_user.id, value="djddjdjddn", kind="wechat_id", status="pending")
    _seed(db_session, test_user.id, value="18124655127", kind="mobile", status="added")
    _seed(db_session, other_user.id, value="someoneelse888", kind="wechat_id", contact="别人家客户")

    # 按用户
    assert admin_api.admin_list_wechat_shared_contacts(
        user_id=test_user.id, kind="", q="", page=1, page_size=10, ctx=None, db=db_session)["total"] == 2
    # 按类型
    assert admin_api.admin_list_wechat_shared_contacts(
        user_id=0, kind="mobile", q="", page=1, page_size=10, ctx=None, db=db_session)["total"] == 1
    # 按值/客户名/会话关键词
    assert admin_api.admin_list_wechat_shared_contacts(
        user_id=0, kind="", q="djddjdjddn", page=1, page_size=10, ctx=None, db=db_session)["total"] == 1
    assert admin_api.admin_list_wechat_shared_contacts(
        user_id=0, kind="", q="张深根", page=1, page_size=10, ctx=None, db=db_session)["total"] == 2
    # 按用户邮箱
    assert admin_api.admin_list_wechat_shared_contacts(
        user_id=0, kind="", q=test_user.email, page=1, page_size=10, ctx=None, db=db_session)["total"] == 2
    # 查不到的用户
    assert admin_api.admin_list_wechat_shared_contacts(
        user_id=0, kind="", q="不存在的人@test.local", page=1, page_size=10, ctx=None, db=db_session)["total"] == 0


def test_admin_pagination(db_session, test_user):
    from backend.app.api import admin as admin_api

    for i in range(5):
        _seed(db_session, test_user.id, value="wxid_%d" % i, kind="wechat_id")
    page1 = admin_api.admin_list_wechat_shared_contacts(
        user_id=test_user.id, kind="", q="", page=1, page_size=2, ctx=None, db=db_session)
    page3 = admin_api.admin_list_wechat_shared_contacts(
        user_id=test_user.id, kind="", q="", page=3, page_size=2, ctx=None, db=db_session)
    assert page1["total"] == 5 and len(page1["items"]) == 2
    assert len(page3["items"]) == 1