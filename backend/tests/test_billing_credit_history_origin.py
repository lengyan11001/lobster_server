"""消费记录「消耗位置」：只讲业务位置，不暴露供应商/模型价格。"""

from __future__ import annotations

from backend.app.api import billing


def test_capability_origin_uses_business_name():
    assert billing._public_credit_history_origin(
        entry_type="pre_deduct",
        ref_type="comfly_proxy",
        meta={"capability_id": "comfly.daihuo.pipeline", "endpoint": "chat"},
    ).startswith("技能「爆款TVC」整包成片")

    video = billing._public_credit_history_origin(
        entry_type="direct_charge",
        ref_type="comfly_proxy",
        meta={"capability_id": "video.generate", "endpoint": "video"},
    )
    assert "生成视频" in video and video.endswith("· 视频")


def test_origin_never_leaks_internal_provider_or_pricing():
    origin = billing._public_credit_history_origin(
        entry_type="sutui_chat",
        ref_type="sutui_chat",
        meta={"model": "deepseek-flash", "provider": "direct:deepseek", "_recon": {"sutui_pool": "bihuo"}},
    )
    assert origin == "速推对话"
    for leaked in ("deepseek", "bihuo", "direct:", "gpt-", "comfly", "_recon"):
        assert leaked not in origin


def test_origin_fallbacks_cover_recharge_skill_and_admin():
    assert billing._public_credit_history_origin(
        entry_type="recharge", ref_type="recharge_order", ref_id="20961202609038424308990e9f820e"
    ) == "充值订单 0e9f820e"
    assert billing._public_credit_history_origin(
        entry_type="skill_unlock", ref_type="skill_package", ref_id="media_edit_skill"
    ) == "技能：media_edit_skill"
    assert billing._public_credit_history_origin(entry_type="admin_deduct") == "管理员调整"
    assert billing._public_credit_history_origin(
        entry_type="pre_deduct", ref_type="wan_role_task", ref_id="pending"
    ) == "数字人视频任务"
    # 未知能力不编造：原样回显能力 id
    assert billing._public_credit_history_origin(
        entry_type="pre_deduct", meta={"capability_id": "unknown.capability"}
    ) == "unknown.capability"


def test_short_label_trims_description_at_first_punctuation():
    label = billing._short_capability_label("电商详情页生成流水线：商品主图自动分析卖点、生成多张竖版详情页。")
    assert label == "电商详情页生成流水线"
    assert billing._short_capability_label("") == ""
