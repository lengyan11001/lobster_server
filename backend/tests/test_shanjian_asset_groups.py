from backend.app.api.shanjian_digital_human import CreateVideoBody, _template_meta_from_body


def test_asset_groups_reach_template_meta():
    body = CreateVideoBody(style_id="style-1", asset_groups=["数字人口播素材"])
    meta = _template_meta_from_body(body)
    assert meta is not None
    assert meta["asset_groups"] == ["数字人口播素材"]


def test_digital_human_asset_groups_alias_reaches_template_meta():
    body = CreateVideoBody(style_id="style-1", digital_human_asset_groups=["数字人口播素材"])
    meta = _template_meta_from_body(body)
    assert meta is not None
    assert meta["asset_groups"] == ["数字人口播素材"]
