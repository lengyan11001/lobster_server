import pytest
from fastapi import HTTPException

from backend.app.api.scheduled_tasks import _normalize_goal_video_task_payload


def test_goal_video_single_asset_does_not_require_candidate_group():
    payload = {
        "capability_id": "goal.video.pipeline",
        "payload": {
            "source_mode": "reference_image",
            "reference_image_url": "https://example.com/demo.jpg",
            "candidate_group": "",
        },
    }

    _normalize_goal_video_task_payload(payload)

    assert payload["payload"]["source_mode"] == "reference_image"
    assert payload["payload"]["candidate_group"] == ""


def test_goal_video_single_asset_requires_reference():
    payload = {
        "capability_id": "goal.video.pipeline",
        "payload": {
            "source_mode": "reference_image",
            "candidate_group": "",
        },
    }

    with pytest.raises(HTTPException) as exc:
        _normalize_goal_video_task_payload(payload)

    assert exc.value.status_code == 400
    assert "素材图片" in str(exc.value.detail)


def test_goal_video_asset_group_still_requires_candidate_group():
    payload = {
        "capability_id": "goal.video.pipeline",
        "payload": {
            "source_mode": "asset_random",
            "candidate_group": "",
        },
    }

    with pytest.raises(HTTPException) as exc:
        _normalize_goal_video_task_payload(payload)

    assert exc.value.status_code == 400
    assert "备选素材组" in str(exc.value.detail)
