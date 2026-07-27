from __future__ import annotations

import asyncio

from backend.app.api import ip_content_studio as studio
from backend.app.models import IPContentDraftRecord


def _draft_record(*, user_id: int, group_id: str, batch_index: int, title: str) -> IPContentDraftRecord:
    return IPContentDraftRecord(
        record_id=f"{group_id}-{batch_index}-{title}",
        user_id=user_id,
        task="moments_candidate",
        platform="wechat_moments",
        title=title,
        content=f"content-{title}",
        meta={
            "group_id": group_id,
            "batch_index": batch_index,
            "batch_target_count": 2,
        },
    )


def test_scheduled_group_id_is_stable_and_scoped():
    first = studio._scheduled_ip_content_group_id("run-1", "moments_candidate")

    assert first == studio._scheduled_ip_content_group_id("run-1", "moments_candidate")
    assert first != studio._scheduled_ip_content_group_id("run-2", "moments_candidate")
    assert first != studio._scheduled_ip_content_group_id("run-1", "industry_hot_oral")
    assert first.startswith("ipd-")


def test_completed_batches_are_resumed_without_llm_call(db_session, test_user, monkeypatch):
    group_id = studio._scheduled_ip_content_group_id("run-complete", "moments_candidate")
    for batch_index in (1, 2):
        for item_index in (1, 2):
            db_session.add(
                _draft_record(
                    user_id=test_user.id,
                    group_id=group_id,
                    batch_index=batch_index,
                    title=f"b{batch_index}-{item_index}",
                )
            )
    db_session.commit()

    async def unexpected_llm_call(**_kwargs):
        raise AssertionError("completed resumed batches must not call the LLM")

    monkeypatch.setattr(studio, "_call_ip_content_llm", unexpected_llm_call)
    result = asyncio.run(
        studio._generate_and_save_ip_content_records(
            db=db_session,
            current_user=test_user,
            task_key="task2_moments",
            record_task="moments_candidate",
            platform="wechat_moments",
            rows=[],
            fallback_sources=[],
            memories=[],
            extra_requirements="",
            count=4,
            group_id=group_id,
            batch_size=2,
        )
    )

    assert result["status"] == "completed"
    assert result["count"] == 4
    assert result["completed_batches"] == 2
    assert all(batch.get("resumed") for batch in result["batches"])


def test_partial_batch_only_generates_missing_records(db_session, test_user, monkeypatch):
    group_id = studio._scheduled_ip_content_group_id("run-partial", "moments_candidate")
    db_session.add(
        _draft_record(
            user_id=test_user.id,
            group_id=group_id,
            batch_index=1,
            title="existing",
        )
    )
    db_session.commit()
    requested_counts: list[int] = []

    async def generate_missing(**kwargs):
        requested_counts.append(int(kwargs["count"]))
        count = int(kwargs["count"])
        return {
            "requirements": "requirements",
            "drafts": [
                {"title": f"generated-{index}", "body": f"body-{index}", "image_prompt": ""}
                for index in range(count)
            ],
            "source_items": [],
        }

    monkeypatch.setattr(studio, "_call_ip_content_llm", generate_missing)
    result = asyncio.run(
        studio._generate_and_save_ip_content_records(
            db=db_session,
            current_user=test_user,
            task_key="task2_moments",
            record_task="moments_candidate",
            platform="wechat_moments",
            rows=[],
            fallback_sources=[],
            memories=[],
            extra_requirements="",
            count=2,
            group_id=group_id,
            batch_size=2,
        )
    )

    assert requested_counts == [1]
    assert result["status"] == "completed"
    assert result["count"] == 2
    assert result["batches"][0]["resumed_count"] == 1

