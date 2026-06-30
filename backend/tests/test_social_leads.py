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


def test_social_leads_reddit_feed_sort_maps_search_sort_to_hot():
    from backend.app.api.social_leads import _reddit_feed_sort

    assert _reddit_feed_sort("RELEVANCE") == "HOT"
    assert _reddit_feed_sort("") == "HOT"
    assert _reddit_feed_sort("new") == "NEW"


def test_tikhub_collects_reddit_cell_feed_posts():
    from backend.app.api.ip_content_studio import _collect_items

    payload = {
        "data": {
            "subredditV3": {
                "elements": {
                    "edges": [
                        {
                            "node": {
                                "__typename": "CellGroup",
                                "groupId": "t3_abc123",
                                "cells": [
                                    {"__typename": "MetadataCell", "authorName": "u/founder", "createdAt": "2026-06-30T00:00:00+0000"},
                                    {"__typename": "TitleCell", "title": "Looking for better lead generation"},
                                    {"__typename": "PreviewTextCell", "text": "Need a tool that finds customers from Reddit."},
                                    {"__typename": "ActionCell", "score": 12, "commentCount": 3, "shareCount": 1},
                                ],
                            }
                        },
                        {"node": {"__typename": "CellGroup", "groupId": "sortcell", "cells": [{"__typename": "SortCell"}]}},
                    ]
                }
            }
        }
    }

    items = _collect_items(payload)

    assert len(items) == 1
    assert items[0]["post_id"] == "t3_abc123"
    assert items[0]["author"] == "founder"
    assert items[0]["title"] == "Looking for better lead generation"
    assert items[0]["num_comments"] == 3


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


def test_social_leads_payload_marks_idle_running_job_needs_resume(db_session, test_user):
    from datetime import timedelta

    from backend.app.api import social_leads
    from backend.app.models import CreativeGenerationJob

    old_time = social_leads._utcnow() - timedelta(seconds=30)
    row = CreativeGenerationJob(
        job_id="rd_idle",
        user_id=test_user.id,
        feature_type="reddit_leads",
        provider="tikhub",
        status="running",
        stage="running",
        progress=50,
        title="Reddit线索采集",
        request_payload={"platform": "reddit"},
        result_payload={},
        meta={
            "platform": "reddit",
            "current_step": "",
            "steps": [
                {"key": "community_feed", "label": "社区帖子采集", "status": "completed"},
                {"key": "merge_leads", "label": "线索归并", "status": "pending"},
            ],
            "outputs": [],
        },
        created_at=old_time,
        updated_at=old_time,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)

    payload = social_leads._job_payload(row, db=db_session, include_sources=True)

    assert payload["status"] == "running"
    assert payload["current_step"] == ""
    assert payload["needs_resume"] is True


def test_social_leads_idle_job_is_scheduled_by_server(db_session, test_user, monkeypatch):
    from datetime import timedelta

    from backend.app.api import social_leads
    from backend.app.models import CreativeGenerationJob

    scheduled = []

    async def fake_auto_run(job_id: str):
        return None

    def fake_create_task(coro):
        scheduled.append(coro)
        coro.close()
        return None

    monkeypatch.setattr(social_leads, "_auto_run_job", fake_auto_run)
    monkeypatch.setattr(social_leads.asyncio, "create_task", fake_create_task)

    old_time = social_leads._utcnow() - timedelta(seconds=30)
    row = CreativeGenerationJob(
        job_id="rd_idle_schedule",
        user_id=test_user.id,
        feature_type="reddit_leads",
        provider="tikhub",
        status="running",
        stage="running",
        progress=50,
        title="Reddit线索采集",
        request_payload={"platform": "reddit"},
        result_payload={},
        meta={
            "platform": "reddit",
            "current_step": "",
            "steps": [
                {"key": "community_feed", "label": "社区帖子采集", "status": "completed"},
                {"key": "merge_leads", "label": "线索归并", "status": "pending"},
            ],
            "outputs": [],
        },
        created_at=old_time,
        updated_at=old_time,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)

    assert social_leads._schedule_autorun_if_needed(row, db_session) is True
    db_session.refresh(row)

    assert len(scheduled) == 1
    assert (row.meta or {}).get("autorun_resume_requested_at")
    assert social_leads._needs_autorun_resume(row) is False


@pytest.mark.asyncio
async def test_social_leads_auto_run_completes_and_merges_sources(db_session, test_user, monkeypatch):
    from backend.app.api import social_leads
    from backend.app.models import CreativeGenerationJob, TikHubSourceItem

    async def fake_execute_query_with_retry(**kwargs):
        db = kwargs["db"]
        meta = kwargs["meta"]
        step_key = meta["step_key"]
        row = TikHubSourceItem(
            user_id=kwargs["current_user"].id,
            query_id="q_" + step_key,
            platform="reddit",
            source_type="subreddit_post",
            item_key="reddit_post_1",
            author_key="founder_buyer",
            author_name="founder_buyer",
            title="Need better lead generation",
            description="Looking for a tool that finds customers from Reddit communities.",
            public_url="https://www.reddit.com/r/SaaS/comments/abc",
            metrics={"score": 12, "num_comments": 4},
            raw={
                "author": "founder_buyer",
                "title": "Need better lead generation",
                "selftext": "Looking for a tool that finds customers from Reddit communities.",
                "permalink": "/r/SaaS/comments/abc",
                "score": 12,
                "num_comments": 4,
                "__lobster_ip_content_meta": {
                    "source": "social_leads",
                    "source_reason": meta["source_reason"],
                    "social_leads_job_id": meta["social_leads_job_id"],
                    "step_key": step_key,
                },
            },
        )
        db.add(row)
        db.commit()
        return {
            "ok": True,
            "raw_item_count": 1,
            "query": {"query_id": row.query_id, "query_type": kwargs["query_type"]},
            "raw_response": {"items": [row.raw]},
        }

    monkeypatch.setattr(social_leads, "_execute_query_with_retry", fake_execute_query_with_retry)

    req = {
        "platform": "reddit",
        "keywords": [],
        "accounts": [],
        "post_ids": [],
        "communities": ["SaaS"],
        "search_type": "post",
        "sort": "HOT",
        "time_range": "month",
        "max_items": 10,
        "include_comments": False,
        "include_account_posts": False,
    }
    row = CreativeGenerationJob(
        job_id="rd_autorun",
        user_id=test_user.id,
        feature_type="reddit_leads",
        provider="tikhub",
        status="queued",
        stage="queued",
        progress=0,
        title="Reddit线索采集",
        request_payload=req,
        result_payload={},
        meta={"platform": "reddit", "steps": social_leads._initial_steps(req), "outputs": [], "current_step": ""},
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)

    while True:
        step = social_leads._next_pending_step(row)
        if step is None:
            break
        row = await social_leads._execute_step(db_session, row, test_user, str(step["key"]))
        if row.status == "completed":
            break

    assert row.status == "completed"
    assert row.progress == 100
    candidates = (row.result_payload or {}).get("candidates") or []
    assert len(candidates) == 1
    assert candidates[0]["candidate_key"] == "founder_buyer"

    payload = social_leads._job_payload(row, db=db_session, include_sources=True)
    assert payload["needs_resume"] is False
    assert payload["source_summary"]["total"] == 1
    assert payload["steps"][-1]["key"] == "merge_leads"
    assert payload["steps"][-1]["status"] == "completed"


@pytest.mark.asyncio
async def test_social_leads_community_feed_uses_feed_sort_and_fails_loudly(monkeypatch):
    from backend.app.api import social_leads

    calls = []

    async def fake_execute_query_with_retry(**kwargs):
        calls.append(kwargs)
        return {
            "ok": False,
            "raw_item_count": 0,
            "error_message": "TikHub HTTP 400",
            "query": {"query_id": "q_bad", "error_message": "TikHub HTTP 400"},
        }

    monkeypatch.setattr(social_leads, "_execute_query_with_retry", fake_execute_query_with_retry)

    class Job:
        job_id = "rd_bad_sort"
        user_id = 1
        status = "queued"
        stage = "queued"
        progress = 0
        error = ""
        completed_at = None
        result_payload = {}
        request_payload = {
            "platform": "reddit",
            "keywords": [],
            "accounts": [],
            "post_ids": [],
            "communities": ["https://www.reddit.com/r/SaaS"],
            "sort": "RELEVANCE",
            "max_items": 30,
        }
        meta = {"steps": [{"key": "community_feed", "label": "社区帖子采集", "status": "pending"}], "outputs": []}

    class DB:
        def commit(self): pass
        def refresh(self, row): pass

    row = await social_leads._execute_step(DB(), Job(), object(), "community_feed")

    assert row.status == "failed"
    assert "采集失败" in row.error
    assert calls[0]["query_type"] == "reddit_subreddit_feed"
    assert calls[0]["params"]["subreddit_name"] == "SaaS"
    assert calls[0]["params"]["sort"] == "HOT"
