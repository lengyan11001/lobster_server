from __future__ import annotations


def _seed(db_session, owner_id: int):
    from backend.app.models import ContentCompetitorAccount, IPContentKeyword, IPContentScheduleTemplate

    kw1 = IPContentKeyword(user_id=owner_id, keyword="算力", display_name="算力")
    kw2 = IPContentKeyword(user_id=owner_id, keyword="AI培训", display_name="AI培训")
    kw_deleted = IPContentKeyword(user_id=owner_id, keyword="已删词", display_name="已删词", status="deleted")
    comp = ContentCompetitorAccount(user_id=owner_id, platform="douyin", account_key="dy_peers", display_name="同行A")
    db_session.add_all([kw1, kw2, kw_deleted, comp])
    db_session.commit()
    tpl = IPContentScheduleTemplate(
        user_id=owner_id,
        name="AI培训",
        is_system=True,
        keyword_ids=[kw1.id, 999999, kw2.id, kw_deleted.id],
        competitor_ids=[comp.id, 888888],
        memory_doc_ids=[],
        requirements={"oral": "口播要求A", "moments": "朋友圈要求B", "image": "出图要求C"},
        status="active",
    )
    db_session.add(tpl)
    db_session.commit()
    db_session.refresh(tpl)
    return tpl, kw1, kw2, comp


def test_system_template_list_exposes_keyword_and_competitor_names(db_session, test_user):
    """用户端「教程 → 系统模板」详情要同时看到关联关键词和关联同行账号（带名称）。"""
    from backend.app.api import ip_content_studio as studio

    tpl, kw1, kw2, comp = _seed(db_session, int(test_user.id))
    payload = studio.list_system_templates(current_user=test_user, db=db_session)
    item = next(i for i in payload["items"] if i["id"] == int(tpl.id))

    assert item["keyword_count"] == 2, item
    assert item["competitor_count"] == 1, item
    assert [int(k["id"]) for k in item["keywords"]] == [int(kw1.id), int(kw2.id)], item
    assert [k["display_name"] for k in item["keywords"]] == ["算力", "AI培训"], item
    assert [(c["account_key"], c["display_name"], c["platform"]) for c in item["competitors"]] == [
        ("dy_peers", "同行A", "douyin")
    ], item
