from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.api import scheduled_tasks
from backend.app.api.ip_content_studio import _ENDPOINTS
from backend.app.api.linkedin_mining import (
    _company_employee_candidates,
    _fallback_summary_report,
    _linkedin_company_url,
    _linkedin_profile_url,
    _normalize_candidate_from_row,
    _profile_discovery_candidates,
    _require_query_success,
)


def test_linkedin_endpoints_use_current_web_v2_contract():
    assert _ENDPOINTS["linkedin_user_profile"] == {
        "platform": "linkedin",
        "source_type": "user_profile",
        "method": "GET",
        "path": "/api/v1/linkedin/web_v2/get_user_profile",
        "allowed_params": {"url"},
    }
    assert _ENDPOINTS["linkedin_user_comments"]["path"] == "/api/v1/linkedin/web_v2/get_user_posts"
    assert _ENDPOINTS["linkedin_user_comments"]["default_params"] == {"type": "comments"}
    assert _ENDPOINTS["linkedin_user_reactions"]["default_params"] == {"type": "reactions"}
    assert _ENDPOINTS["linkedin_company_profile"]["allowed_params"] == {"url"}
    assert _ENDPOINTS["linkedin_post_comments"]["allowed_params"] == {
        "urn",
        "sort_by",
        "page",
        "pagination_token",
        "share_urn",
    }
    assert not any("/api/v1/linkedin/web/" in spec.get("path", "") for spec in _ENDPOINTS.values())


def test_linkedin_v2_full_urls_are_built_from_saved_slugs():
    assert _linkedin_profile_url("williamhgates") == "https://www.linkedin.com/in/williamhgates/"
    assert _linkedin_company_url("rapidapi") == "https://www.linkedin.com/company/rapidapi/"


def test_linkedin_primary_query_failure_is_not_treated_as_empty_success():
    with pytest.raises(Exception, match="HTTP 404"):
        _require_query_success(
            {
                "ok": False,
                "query": {"http_status": 404, "error_message": "Not Found"},
            },
            "LinkedIn profile",
        )


def test_linkedin_v2_embedded_candidates_are_normalized():
    similar = _profile_discovery_candidates(
        {
            "people_also_viewed": [
                {
                    "profile_link": "https://www.linkedin.com/in/melindagates",
                    "name": "Melinda French Gates",
                    "about": "Pivotal",
                }
            ]
        }
    )
    employees = _company_employee_candidates(
        {
            "name": "Rapid",
            "employees": [
                {
                    "link": "https://www.linkedin.com/in/example-person",
                    "title": "Example Person",
                }
            ],
        },
        "rapidapi",
    )

    assert similar[0]["profile_url"].endswith("/melindagates")
    assert similar[0]["headline"] == "Pivotal"
    assert employees[0]["name"] == "Example Person"
    assert employees[0]["company_name"] == "Rapid"


@pytest.mark.asyncio
async def test_linkedin_activity_query_applies_v2_type_default(monkeypatch):
    from backend.app.api import ip_content_studio

    captured = {}

    class Response:
        status_code = 200
        headers = {}

        @staticmethod
        def json():
            return {"code": 200, "data": []}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers, params):
            captured.update({"url": url, "params": params})
            return Response()

    monkeypatch.setattr(ip_content_studio.httpx, "AsyncClient", lambda **_kwargs: Client())
    monkeypatch.setattr(ip_content_studio, "_tikhub_api_key", lambda: "test-token")

    await ip_content_studio._call_tikhub(
        "linkedin_user_comments",
        {"url": "https://www.linkedin.com/in/williamhgates/", "start": 0},
        {},
    )

    assert captured["url"].endswith("/api/v1/linkedin/web_v2/get_user_posts")
    assert captured["params"] == {
        "type": "comments",
        "url": "https://www.linkedin.com/in/williamhgates/",
        "start": 0,
    }


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
