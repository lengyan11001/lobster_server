import asyncio

from backend.app.api import shanjian_smart_clip


def test_news_mixcut_material_composition_uses_current_upstream_values():
    assert shanjian_smart_clip._normalize_material_composition("order") == "order"
    assert shanjian_smart_clip._normalize_material_composition("sequential") == "order"
    assert shanjian_smart_clip._normalize_material_composition("random") == "random"
    assert shanjian_smart_clip._normalize_material_composition("unknown") == "random"


def test_introduce_card_name_is_never_empty_when_description_is_sent(monkeypatch):
    captured = {}

    async def fake_post(_path, _token, body):
        captured.update(body)
        return {"code": "Succeed", "data": {"taskId": "clip-task-3"}}

    monkeypatch.setattr(shanjian_smart_clip, "_post", fake_post)
    body = shanjian_smart_clip.SubmitClipBody(
        title="模板标题",
        style_id="template-1",
        scene="newsMixCutting",
        materials=[{"type": "video", "fileUrl": "https://example.test/base.mp4"}],
        introduce_name="",
        introduce_description="模板介绍",
    )

    import asyncio
    from types import SimpleNamespace

    result = asyncio.run(
        shanjian_smart_clip.submit_clip(
            body,
            current_user=SimpleNamespace(id=1),
            db=SimpleNamespace(),
        )
    )

    assert result["task_id"] == "clip-task-3"
    assert captured["introduceCard"] == {"name": "模板标题", "description": "模板介绍"}


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
