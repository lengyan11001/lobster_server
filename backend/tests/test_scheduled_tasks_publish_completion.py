from backend.app.api.scheduled_tasks import ScheduledTaskCompleteIn, _normalize_scheduled_completion_error


def _publish_completion(publish_result: dict) -> ScheduledTaskCompleteIn:
    return ScheduledTaskCompleteIn(
        result_text="发布中心任务已提交",
        result_payload={
            "task_kind": "client_workflow",
            "action": "publish_content",
            "local_result": {"publish_result": publish_result},
        },
    )


def test_publish_need_login_cannot_complete_successfully():
    body = _publish_completion(
        {
            "status": "need_login",
            "need_login": True,
            "error": "未登录，已打开浏览器登录页，请扫码登录后再重试发布",
        }
    )

    assert _normalize_scheduled_completion_error(body) == "未登录，已打开浏览器登录页，请扫码登录后再重试发布"


def test_publish_failed_status_uses_nested_error():
    body = _publish_completion({"status": "failed", "error": "页面未检测到发布成功提示"})

    assert _normalize_scheduled_completion_error(body) == "页面未检测到发布成功提示"


def test_publish_explicit_failure_without_error_gets_fallback():
    body = _publish_completion({"ok": False})

    assert _normalize_scheduled_completion_error(body) == "发布失败"


def test_publish_success_still_completes_successfully():
    body = _publish_completion({"task_id": 2, "status": "success", "result_url": ""})

    assert _normalize_scheduled_completion_error(body) == ""
