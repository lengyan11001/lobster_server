from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_h5_chat_uses_mastra_and_has_hold_to_talk_controls():
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")

    assert 'id="composerVoiceBtn"' in html
    assert 'id="composerVoiceFeedback"' in html
    assert "上滑取消" in html
    assert "AI 调度助手" in html
    assert 'api("/api/mastra-chat/messages"' in script
    assert 'startVoiceCapture(evt, "composer")' in script
    assert 'params.set("resolve_intent", resolveIntent ? "1" : "0")' in script
    assert "finishComposerVoiceRecognition" in script


def test_h5_voice_layout_is_stable_and_mobile_friendly():
    styles = (ROOT / "h5_static" / "h5-app.css").read_text(encoding="utf-8")

    assert ".composer-surface" in styles
    assert ".composer-toolbar" in styles
    assert ".composer-voice-feedback" in styles
    assert "touch-action: none" in styles
    assert "width: min(310px, calc(100vw - 40px))" in styles


def test_streaming_progress_is_visible_without_exposing_internal_reasoning():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    mastra = (ROOT / "mastra_server" / "src" / "mastra" / "index.ts").read_text(encoding="utf-8")

    assert "const SHOW_INTERNAL_STEPS = true" in script
    assert 'write({ type: \'thinking\'' in mastra
    assert "toolDisplayName" in mastra
    assert "不要暴露 Mastra、MCP、速推、模型供应商" in mastra


def test_h5_chat_supports_isolated_sessions_attachments_and_permissions():
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    mastra = (ROOT / "mastra_server" / "src" / "mastra" / "index.ts").read_text(encoding="utf-8")

    for element_id in (
        "chatSessionsModal",
        "newChatSessionBtn",
        "composerFileInput",
        "composerAssetPicker",
        "composerPermissionBtn",
        "chatApprovalModal",
    ):
        assert f'id="{element_id}"' in html
    assert 'permission_mode: "confirm"' in script
    assert "closeAllMessageStreams()" in script
    assert "chatHistoryRequestSeq" in script
    assert "chatApprovalRequestSeq" in script
    assert 'session_id: state.activeChatSessionId' in script
    assert "attachments," in script
    assert "openai/gpt-5.6-sol" in mastra
    assert "approval_id: contextValue(context, 'approvalId')" in mastra


def test_mastra_concurrency_slot_is_handed_directly_to_next_waiter():
    mastra = (ROOT / "mastra_server" / "src" / "mastra" / "index.ts").read_text(encoding="utf-8")

    release_start = mastra.index("function releaseSlot()")
    release_end = mastra.index("function toolDisplayName", release_start)
    release_slot = mastra[release_start:release_end]
    handoff = release_slot.index("const next = waiters.shift()")
    decrement = release_slot.index("activeRequests = Math.max(0, activeRequests - 1)")

    assert "if (next)" in release_slot[handoff:decrement]
    assert "next()" in release_slot[handoff:decrement]
    assert "return" in release_slot[handoff:decrement]
