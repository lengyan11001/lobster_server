import pytest


def test_social_leads_reddit_initial_steps():
    from backend.app.api.social_leads import _initial_steps

    steps = _initial_steps(
        {
            "platform": "reddit",
            "keywords": ["ai agency"],
            "communities": ["Entrepreneur"],
            "accounts": ["founder"],
            "post_ids": ["t3_abc"],
            "include_comments": True,
            "include_account_posts": True,
        }
    )

    assert [step["key"] for step in steps] == [
        "keyword_search",
        "community_feed",
        "account_profiles",
        "account_activity",
        "post_comments",
        "merge_leads",
    ]


def test_social_leads_x_initial_steps_keeps_collection_only():
    from backend.app.api.social_leads import _initial_steps

    steps = _initial_steps(
        {
            "platform": "x",
            "country": "UnitedStates",
            "keywords": ["ai automation"],
            "accounts": ["elonmusk"],
            "post_ids": ["1808168603721650364"],
            "include_comments": True,
            "include_account_posts": True,
        }
    )

    assert [step["key"] for step in steps] == [
        "trending",
        "keyword_search",
        "account_profiles",
        "account_activity",
        "post_comments",
        "merge_leads",
    ]


def test_social_leads_merges_reddit_candidates_from_rows():
    from backend.app.api.social_leads import _candidate_from_row, _merge_candidates

    class Row:
        id = 1
        platform = "reddit"
        source_type = "post_comment"
        item_key = "t1_c1"
        author_key = "startup_owner"
        author_name = "startup_owner"
        title = "Need help with marketing automation"
        description = "Looking for tools that can generate leads from Reddit."
        public_url = "https://www.reddit.com/r/startups/comments/abc"
        metrics = {"score": 12}
        created_at = None
        raw = {
            "__lobster_ip_content_meta": {"source_reason": "Reddit帖子评论 t3_abc"},
            "author": "startup_owner",
            "body": "Looking for tools that can generate leads from Reddit.",
            "score": 12,
            "permalink": "/r/startups/comments/abc/comment/c1",
        }

    candidate = _candidate_from_row(Row())
    merged = _merge_candidates([candidate])

    assert merged[0]["candidate_key"] == "startup_owner"
    assert merged[0]["platform"] == "reddit"
    assert "Reddit" in merged[0]["source_reason"]
    assert merged[0]["score"] >= 10


def test_social_leads_splits_reddit_subreddit_links_from_accounts():
    from backend.app.api.social_leads import _split_reddit_accounts_and_communities

    accounts, communities = _split_reddit_accounts_and_communities(
        ["https://www.reddit.com/r/SaaS", "u/founder", "reddit.com/user/buyer"],
        ["Entrepreneur"],
    )

    assert accounts == ["founder", "buyer"]
    assert communities == ["Entrepreneur", "SaaS"]


def test_social_leads_ignores_empty_source_rows():
    from backend.app.api.social_leads import _candidate_from_row

    class Row:
        id = 2
        platform = "reddit"
        source_type = "user_profile"
        item_key = "hash-only"
        author_key = None
        author_name = None
        title = None
        description = None
        public_url = None
        metrics = {}
        created_at = None
        raw = {"redditorInfoByName": None, "__lobster_ip_content_meta": {"source_reason": "Reddit账号 u/bad"}}

    assert _candidate_from_row(Row()) is None


@pytest.mark.asyncio
async def test_social_leads_x_keyword_step_uses_twitter_search_endpoint(monkeypatch):
    from backend.app.api import social_leads

    calls = []

    async def fake_execute_query_with_retry(**kwargs):
        calls.append(kwargs)
        return {
            "ok": True,
            "raw_item_count": 1,
            "query": {"query_id": "q1", "query_type": kwargs["query_type"]},
            "raw_response": {"data": [{"screen_name": "buyer", "name": "Buyer", "description": "Need AI workflows"}]},
        }

    monkeypatch.setattr(social_leads, "_execute_query_with_retry", fake_execute_query_with_retry)

    class Job:
        job_id = "x_test"
        user_id = 1
        status = "queued"
        stage = "queued"
        progress = 0
        error = ""
        completed_at = None
        result_payload = {}
        request_payload = {
            "platform": "x",
            "keywords": ["AI workflow"],
            "accounts": [],
            "post_ids": [],
            "communities": [],
            "search_type": "Latest",
            "max_items": 30,
        }
        meta = {"steps": [{"key": "keyword_search", "label": "关键词搜索", "status": "pending"}], "outputs": []}

    class DB:
        def commit(self): pass
        def refresh(self, row): pass

    row = await social_leads._execute_step(DB(), Job(), object(), "keyword_search")

    assert row.status == "running"
    assert calls[0]["query_type"] == "x_search"
    assert calls[0]["params"] == {"keyword": "AI workflow", "search_type": "Latest"}
