from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
H5 = ROOT / "h5_static"


def _live_executor_view() -> str:
    html = (H5 / "index.html").read_text(encoding="utf-8")
    return html.split('id="liveExecutorView"', 1)[1].split('id="recorderView"', 1)[0]


def test_live_executor_console_has_six_business_actions():
    view = _live_executor_view()
    expected = {
        "douyin_nurture": "抖音养号",
        "douyin_leads": "抖音获客",
        "moments_content": "朋友圈图文",
        "douyin_publish": "抖音发布",
        "channels_publish": "视频号发布",
        "wechat_takeover": "微信接管",
    }
    for action, label in expected.items():
        assert f'data-live-action="{action}"' in view
        assert f"<span>{label}</span>" in view
    for old in ("总经办", "市场部", "设计部", "客服部"):
        assert f"<span>{old}</span>" not in view


def test_live_executor_publish_uses_latest_bestseller_then_latest_video():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    assert "async function liveExecutorLatestPublishVideo()" in script
    assert 'q: "local_bestseller"' in script
    assert 'media_type: "video"' in script
    assert "内容记录里还没有视频" in script
    assert 'const latest = await api(`/api/assets?${buildQuery()}`' in script
    assert 'if (asset) source = "local_bestseller";' in script


def test_live_executor_publish_plan_targets_douyin_and_channels_accounts():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    assert "async function liveExecutorPublishPlan(platform)" in script
    assert 'action: "publish_content"' in script
    assert 'platform: meta.key' in script
    assert 'account_nickname: String(account.nickname || "").trim()' in script
    assert "没有找到在线的${label}发布账号" in script
    assert 'ai_publish_copy: false' in script


def test_live_executor_moments_uses_normal_ip_content_node():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    assert "async function liveExecutorMomentsPlan(batchId = \"\")" in script
    assert 'taskKind: "ip_content_daily"' in script
    assert 'tasks: ["moments_candidate"]' in script
    assert "moments_count: 1" in script
    assert "workflow_node_id: workflowNodeId" in script
    assert "workflow_template_id: workflowNodeId" in script


def test_live_executor_moments_creates_auto_publish_child():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    assert "function liveExecutorMomentsPublishPlan(parentPlan, installationId = \"\")" in script
    assert 'title: "朋友圈发布"' in script
    assert 'source_mode: "parent_latest_run"' in script
    assert "source_workflow_node_id: workflowNodeId" in script
    assert "workflow_parent_node_id: workflowNodeId" in script
    assert 'platform: "wechat_moments"' in script
    assert 'account_nickname: "本机微信"' in script
    assert "scheduledTaskId: data.task && data.task.id" in script
    assert "tasks.some((task) => task.runId || task.scheduledTaskId)" in script


def test_live_executor_actions_are_wired_and_grid_fits_six_buttons():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    css = (H5 / "h5-app.css").read_text(encoding="utf-8")
    for key in ("douyin_nurture", "douyin_leads", "moments_content", "douyin_publish", "channels_publish", "wechat_takeover"):
        assert f'{key}:' in script
    assert 'await submitLiveExecutorPlan(liveExecutorNurturePlan(), task);' in script
    assert 'await submitLiveExecutorPlan(plan, task);' in script
    assert 'const platform = key === "douyin_publish" ? "douyin" : "wechat_channels";' in script
    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in css
    assert ".live-executor-actions button:nth-child(6)" in css

def test_live_executor_redesign_has_device_rail_queue_and_results():
    view = _live_executor_view()
    html = (H5 / "index.html").read_text(encoding="utf-8")
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    css = (H5 / "h5-app.css").read_text(encoding="utf-8")

    for node_id in (
        "liveExecutorDeviceRail",
        "liveExecutorTaskList",
        "liveExecutorSummaryQueued",
        "liveExecutorSummaryRunning",
        "liveExecutorSummaryDone",
        "liveExecutorSummaryFailed",
        "liveExecutorClearDoneBtn",
    ):
        assert f'id="{node_id}"' in view

    for action in ("all", "active", "done", "failed"):
        assert f'data-live-filter="{action}"' in view
    assert "只显示最近一条" not in view

    assert "function liveExecutorStorageKey()" in script
    assert "function liveExecutorV3Persist()" in script
    assert "function liveExecutorV3Restore()" in script
    assert "live.queues[key]" in script
    assert "queueSeq" in script
    assert "data-live-device" in script
    assert "data-live-filter" in script
    assert "live-executor-v3" in html
    assert ".live-executor-v3 .live-executor-device-card" in css
    assert ".live-executor-v3 .live-executor-task-card" in css
    assert ".live-executor-v3 .live-executor-latest-card" in css
