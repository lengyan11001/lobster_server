from backend.app.api.homepage import _HOME_HTML


def test_public_homepage_shows_the_required_icp_record_link():
    assert "必火AI员工" in _HOME_HTML
    assert "粤ICP备2026043577号" in _HOME_HTML
    assert 'href="https://beian.miit.gov.cn/"' in _HOME_HTML
    assert 'target="_blank"' in _HOME_HTML
