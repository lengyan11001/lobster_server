import asyncio

from backend.app.api import shanjian_smart_clip


def test_task_info_flattens_completed_result(monkeypatch):
    async def fake_get(_path, _token, _params):
        return {
            "code": "Succeed",
            "data": {
                "taskId": "clip-task-1",
                "status": "completed",
                "result": {
                    "videoUrl": "https://example.test/final.mp4",
                    "coverUrl": "https://example.test/cover.jpg",
                    "duration": 7.2,
                },
            },
        }

    monkeypatch.setattr(shanjian_smart_clip, "_get", fake_get)

    result = asyncio.run(
        shanjian_smart_clip.task_info(
            shanjian_smart_clip.TaskBody(task_id="clip-task-1")
        )
    )

    assert result["ok"] is True
    assert result["status"] == "succeed"
    assert result["task_id"] == "clip-task-1"
    assert result["video_url"] == "https://example.test/final.mp4"
    assert result["cover_url"] == "https://example.test/cover.jpg"
    assert result["duration"] == 7.2


def test_task_info_normalizes_terminal_failure(monkeypatch):
    async def fake_get(_path, _token, _params):
        return {
            "code": "Succeed",
            "data": {
                "taskId": "clip-task-2",
                "status": "error",
                "errorCode": "UPSTREAM_FAILED",
                "errorMessage": "render failed",
            },
        }

    monkeypatch.setattr(shanjian_smart_clip, "_get", fake_get)

    result = asyncio.run(
        shanjian_smart_clip.task_info(
            shanjian_smart_clip.TaskBody(task_id="clip-task-2")
        )
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["error_code"] == "UPSTREAM_FAILED"
    assert result["message"] == "render failed"
