from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_h5_competitors_search_candidates_before_add():
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")

    assert 'id="personalCompetitorSearchResults"' in html
    assert 'id="personalAddCompetitorByChannelIdBtn"' in html
    assert 'id="personalAddCompetitorBtn">搜索账号<' in html
    assert "/api/ip-content/wechat-channels/users/search?q=" in script
    assert "/api/ip-content/douyin/users/search?q=" in script
    assert "/api/ip-content/wechat-channels/competitors/by-channel-id" in script
    assert "data-add-personal-competitor-candidate" in script
    assert "h5_personal_settings_wechat_channels_search" in script
    assert "candidate.username || candidate.finder_username || candidate.id" in script


def test_h5_template_save_filters_deleted_competitor_ids():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")

    assert "function personalExistingIntIds(values, rows)" in script
    assert "function personalTemplateResourceRows(kind)" in script
    assert "function personalCombinedIntRows(kind, rows)" in script
    assert "competitor_ids: personalExistingIntIds" in script
    assert 'personalCombinedIntRows("competitor", state.personalCompetitors)' in script
    assert "prunePersonalSelectedIntMap(state.personalSelectedCompetitors" in script
