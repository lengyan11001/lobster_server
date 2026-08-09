import pytest
from fastapi import HTTPException

from backend.app.api import scheduled_tasks


def test_seedance_scheduled_payload_rejects_run_detail_text():
    payload = {
        "capability_id": "comfly.seedance.tvc.pipeline",
        "payload": {
            "action": "start_pipeline",
            "image_url": "https://example.test/demo.jpg",
            "task_text": "‹\n执行详情\n创意分镜头视频\n失败 · 08/09 21:06\n执行结果\nanalyze failed after 2 attempt(s)\n执行配置与参数",
        },
    }

    with pytest.raises(HTTPException) as exc_info:
        scheduled_tasks._validate_capability_task_payload(payload)

    assert exc_info.value.status_code == 400
    assert "执行详情或失败结果" in str(exc_info.value.detail)


def test_non_seedance_scheduled_payload_keeps_legacy_text_compatibility():
    payload = {
        "capability_id": "goal.image.pipeline",
        "payload": {
            "prompt": "执行详情作为普通素材说明的一部分",
        },
    }

    scheduled_tasks._validate_capability_task_payload(payload)
