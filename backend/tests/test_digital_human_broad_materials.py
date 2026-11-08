"""回归：所有「自动选素材」入口共用同一套宽松策略。

2026-11-08 用户反馈：工作流能带素材了，但小程序「录音生成」还是老逻辑
（必须 AI 点名分组、最多 8 条、点错就空）。这里把三个入口锁在同一套规则上：
分组内随机、最多 20 条、不做文案关键词闸门。
"""
from __future__ import annotations

from backend.app.api import h5_recorder_extract
from backend.app.models import Asset
from backend.app.services.digital_human_materials import (
    MATERIAL_PICK_LIMIT,
    asset_group_of,
    broad_pick,
    resolve_group_materials,
)


class _FakeAsset:
    def __init__(self, asset_id: str, media_type: str = "image", group: str = "组A", filename: str = ""):
        self.asset_id = asset_id
        self.media_type = media_type
        self.source_url = f"https://cdn.example.com/{asset_id}.jpg"
        self.filename = filename or asset_id


def test_broad_pick_caps_at_limit_and_keeps_members():
    items = [{"id": i} for i in range(50)]
    picked = broad_pick(items, 20)
    assert len(picked) == 20
    assert {item["id"] for item in picked} <= {item["id"] for item in items}
    assert len(broad_pick([], 20)) == 0


def test_recorder_uses_ai_named_group_broadly():
    groups = {"组A": [_FakeAsset(f"a{i}") for i in range(30)]}
    params = h5_recorder_extract._normalize_ai_params(
        {"use_materials": True, "material_group": "组A"},
        groups,
        [],
    )
    assert params["material_group"] == "组A"
    assert len(params["materials"]) == MATERIAL_PICK_LIMIT


def test_recorder_falls_back_to_template_group_when_ai_omits_it():
    groups = {
        "组A": [_FakeAsset(f"a{i}") for i in range(3)],
        "数字人口播素材": [_FakeAsset(f"t{i}") for i in range(25)],
    }
    params = h5_recorder_extract._normalize_ai_params(
        {"use_materials": True, "material_group": ""},
        groups,
        [],
        ["数字人口播素材"],
    )
    assert params["material_group"] == "数字人口播素材"
    assert len(params["materials"]) == MATERIAL_PICK_LIMIT
    assert all(item["fileUrl"].startswith("https://") for item in params["materials"])


def test_recorder_falls_back_to_largest_group_when_template_unknown():
    groups = {
        "小组": [_FakeAsset("s0")],
        "大组": [_FakeAsset(f"b{i}") for i in range(6)],
    }
    params = h5_recorder_extract._normalize_ai_params(
        {"use_materials": True, "material_group": "不存在的组"},
        groups,
        [],
        ["另一个不存在的模板组"],
    )
    assert params["material_group"] == "大组"
    assert len(params["materials"]) == 6


def test_asset_group_of_reads_both_meta_shapes():
    assert asset_group_of({"creative_candidate_group": "组A"}) == "组A"
    assert asset_group_of({"creative_candidate_groups": ["组B", "组C"]}) == "组B"
    assert asset_group_of(None) == ""


def test_resolve_group_materials_reads_assets_by_group(db_session, test_user):
    rows = []
    for i in range(30):
        rows.append(
            Asset(
                asset_id=f"grp-{i}",
                user_id=test_user.id,
                filename=f"g{i}.jpg",
                media_type="image",
                source_url=f"https://cdn.example.com/g{i}.jpg",
                meta={"creative_candidate_group": "数字人口播素材"},
            )
        )
    rows.append(
        Asset(
            asset_id="other-1",
            user_id=test_user.id,
            filename="o.jpg",
            media_type="image",
            source_url="https://cdn.example.com/o.jpg",
            meta={"creative_candidate_group": "别的组"},
        )
    )
    db_session.add_all(rows)
    db_session.commit()

    picked = resolve_group_materials(db_session, [test_user.id], ["数字人口播素材"])
    assert len(picked) == MATERIAL_PICK_LIMIT
    assert all(item["fileUrl"].startswith("https://cdn.example.com/g") for item in picked)


def test_recorder_resolver_uses_full_asset_scope():
    """录音生成必须和云端一样按分组查全库，而不是只看最近 300 条。"""
    calls = []

    def _resolver(groups):
        calls.append(list(groups))
        return [
            {"asset_id": f"r{i}", "type": "image", "fileUrl": f"https://cdn.example.com/r{i}.jpg", "name": f"r{i}.jpg"}
            for i in range(20)
        ]

    params = h5_recorder_extract._normalize_ai_params(
        {"use_materials": True, "material_group": ""},
        {"数字人口播素材": [_FakeAsset("only-recent")]},
        [],
        ["数字人口播素材"],
        resolver=_resolver,
    )
    assert calls == [["数字人口播素材"]]
    assert len(params["materials"]) == 20
    assert params["material_group"] == "数字人口播素材"
