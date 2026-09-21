"""消费记录「消耗位置」：只讲业务位置，不暴露供应商/模型价格。"""

from __future__ import annotations

from backend.app.api import billing


def test_capability_origin_uses_business_name():
    assert billing._public_credit_history_origin(
        entry_type="pre_deduct",
        ref_type="comfly_proxy",
        meta={"capability_id": "comfly.daihuo.pipeline", "endpoint": "chat"},
    ).startswith("爆款TVC 整包成片")

    video = billing._public_credit_history_origin(
        entry_type="direct_charge",
        ref_type="comfly_proxy",
        meta={"capability_id": "video.generate", "endpoint": "video"},
    )
    assert video == "视频生成"


def test_origin_never_leaks_internal_provider_or_pricing():
    origin = billing._public_credit_history_origin(
        entry_type="sutui_chat",
        ref_type="sutui_chat",
        meta={"model": "deepseek-flash", "provider": "direct:deepseek", "_recon": {"sutui_pool": "bihuo"}},
    )
    assert origin == "对话"
    for leaked in ("deepseek", "bihuo", "direct:", "gpt", "comfly", "速推", "_recon"):
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
    # 未知能力不编造、也不回显可能带品牌的 id：给中性业务名
    assert billing._public_credit_history_origin(
        entry_type="pre_deduct", meta={"capability_id": "unknown.capability"}
    ) == "能力调用"


def test_short_label_trims_description_at_first_punctuation():
    label = billing._short_capability_label("电商详情页生成流水线：商品主图自动分析卖点、生成多张竖版详情页。")
    assert label == "电商详情页生成流水线"
    assert billing._short_capability_label("") == ""


def test_no_brand_or_model_name_leaks_in_any_known_capability():
    """任何已知能力的「消耗位置」都不允许出现上游平台/模型品牌。"""
    banned = (
        "速推", "必火", "comfly", "openai", "gpt", "veo", "seedance", "seedream", "grok", "gemini",
        "claude", "deepseek", "kling", "可灵", "即梦", "豆包", "qwen", "通义", "glm", "智谱", "文心",
        "minimax", "海螺", "midjourney", "flux", "sora", "hifly", "apiz", "new-api", "newapi",
        "nano-banana", "siliconflow", "硅基流动", "openrouter", "kimi", "moonshot", "阶跃", "星火",
    )
    catalog = billing._capability_catalog_labels()
    ids = set(catalog) | set(billing._CAPABILITY_ORIGIN_LABELS)
    assert len(ids) >= 20
    for capability_id in sorted(ids):
        for endpoint in ("chat", "image", "video"):
            origin = billing._public_credit_history_origin(
                entry_type="pre_deduct",
                ref_type="comfly_proxy",
                meta={"capability_id": capability_id, "endpoint": endpoint},
            )
            lowered = origin.casefold()
            for token in banned:
                assert token.casefold() not in lowered, (capability_id, endpoint, origin, token)


def test_chat_and_recharge_origins_are_brand_free():
    assert billing._public_credit_history_origin(entry_type="sutui_chat", ref_type="sutui_chat") == "对话"
    assert billing._public_credit_history_origin(
        entry_type="recharge", ref_type="recharge_order", ref_id="20961202609038424308990e9f820e"
    ) == "充值订单 0e9f820e"
    # 没有登记的能力也不能点名任何厂商
    origin = billing._public_credit_history_origin(
        entry_type="pre_deduct", meta={"capability_id": "future.vendor.thing", "endpoint": "chat"}
    )
    assert origin == "对话"
