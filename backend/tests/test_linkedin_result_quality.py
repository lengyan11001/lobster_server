from pathlib import Path
from types import SimpleNamespace

from backend.app.api import scheduled_tasks
from backend.app.api.linkedin_mining import _fallback_summary_report, _normalize_candidate_from_row


def test_placeholder_keyword_uses_personal_default_template(monkeypatch):
    monkeypatch.setattr(
        scheduled_tasks,
        "_h5_dh_context_params",
        lambda _db, _user_id: {"keyword_texts": ["算力", "科技"]},
    )

    payload = scheduled_tasks._enrich_linkedin_mining_keywords(
        object(),
        payload={"keywords": ["LinkedIn线索挖掘"]},
        target_user_id=31,
    )

    assert payload["keywords"] == ["算力", "科技"]
    assert payload["keyword_source"] == "personal_default_template"


def test_explicit_linkedin_keyword_is_preserved(monkeypatch):
    monkeypatch.setattr(
        scheduled_tasks,
        "_h5_dh_context_params",
        lambda _db, _user_id: {"keyword_texts": ["算力"]},
    )

    payload = scheduled_tasks._enrich_linkedin_mining_keywords(
        object(),
        payload={"keywords": ["warehouse automation"]},
        target_user_id=31,
    )

    assert payload == {"keywords": ["warehouse automation"]}


def test_source_title_is_used_as_candidate_headline():
    row = SimpleNamespace(
        id=1,
        raw={"raw": {"public_identifier": "aleeza", "full_name": "Aleeza Wang"}},
        author_key=None,
        author_name=None,
        item_key="1260152987",
        title="Global Marketing Manager - Warehouse Automation",
        description=None,
        public_url="https://www.linkedin.com/in/aleeza",
        source_type="user_search",
        created_at=None,
    )

    candidate = _normalize_candidate_from_row(row)

    assert candidate["headline"] == "Global Marketing Manager - Warehouse Automation"


def test_profile_url_is_not_reported_as_public_contact():
    row = SimpleNamespace(
        request_payload={},
        result_payload={
            "candidates": [
                {
                    "name": "Aleeza Wang",
                    "url": "https://www.linkedin.com/in/aleeza",
                    "contact": {},
                    "score": 69,
                }
            ],
            "lead_summary": {"summary": {"with_public_contact": 0}},
        },
    )

    report = _fallback_summary_report(row)

    assert report["priority_leads"][0]["contact_status"] == "profile_only"
    assert report["contact_list"][0]["contact"] == ""


def test_h5_does_not_treat_entire_result_payload_as_media():
    source = (Path(__file__).parents[2] / "h5_static" / "h5-app.js").read_text(encoding="utf-8")

    assert "function renderLinkedinMiningResult" in source
    assert "查看 LinkedIn 主页" in source
    assert "else {\n        add(payload);" not in source
