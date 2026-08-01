from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_h5_chat_uses_mastra_and_has_hold_to_talk_controls():
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    voice_api = (ROOT / "backend" / "app" / "api" / "h5_voice.py").read_text(encoding="utf-8")

    assert 'id="composerVoiceBtn"' in html
    assert 'id="composerVoiceFeedback"' in html
    assert 'id="composerVoiceCancelBtn"' in html
    assert "上滑或点击取消" in html
    assert "AI 调度助手" in html
    assert 'api("/api/mastra-chat/messages"' in script
    assert 'startVoiceCapture(evt, "composer")' in script
    assert 'params.set("resolve_intent", resolveIntent ? "1" : "0")' in script
    assert "finishComposerVoiceRecognition" in script
    assert 'state.voiceStatus = "requesting"' in script
    assert "正在请求麦克风..." in script
    assert "ensure_installation_slot" not in voice_api


def test_h5_chat_voice_permission_and_cancellation_are_race_safe():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")

    open_voice_start = script.index("async function openVoiceRealtimeSession")
    open_voice_end = script.index("async function startVoiceCapture", open_voice_start)
    open_voice = script[open_voice_start:open_voice_end]
    assert open_voice.index("navigator.mediaDevices.getUserMedia") < open_voice.index("new WebSocket")
    assert "sessionNonce !== state.voiceSessionNonce || !state.voiceRecording" in open_voice
    assert 'new Error("语音识别连接超时，请重试")' in open_voice
    assert "}, 8000);" in open_voice

    stop_voice_start = script.index("function stopVoiceCapture")
    stop_voice_end = script.index("function stopComposerVoiceDurationTimer", stop_voice_start)
    assert "}, 12000);" in script[stop_voice_start:stop_voice_end]
    assert 'document.addEventListener("pointermove"' in script
    assert 'document.addEventListener("pointerup"' in script
    assert 'document.addEventListener("pointercancel"' in script
    assert 'window.addEventListener("pagehide"' in script
    assert 'document.addEventListener("visibilitychange"' in script
    assert "resetComposerVoiceCapture" in script


def test_h5_voice_layout_is_stable_and_mobile_friendly():
    styles = (ROOT / "h5_static" / "h5-app.css").read_text(encoding="utf-8")
    designer_styles = (ROOT / "h5_static" / "h5-designer-v2.css").read_text(encoding="utf-8")

    assert ".composer-surface" in styles
    assert ".composer-toolbar" in styles
    assert ".composer-voice-feedback" in styles
    assert "touch-action: none" in styles
    assert "width: min(310px, calc(100vw - 40px))" in styles
    assert ".composer-voice-cancel" in styles
    assert "body.messages-view-active" in designer_styles
    assert "#messagesView.active" in designer_styles
    assert "position: fixed" in designer_styles


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
    assert "return provider.chat(modelId)" in mastra
    assert "approval_id: contextValue(context, 'approvalId')" in mastra


def test_mastra_concurrency_slot_is_handed_directly_to_next_waiter():
    mastra = (ROOT / "mastra_server" / "src" / "mastra" / "index.ts").read_text(encoding="utf-8")

    release_start = mastra.index("function releaseSlot()")
    release_end = mastra.index("function toolDisplayName", release_start)
    release_slot = mastra[release_start:release_end]
    handoff = release_slot.index("const next = waiters.shift()")
    decrement = release_slot.index("activeRequests = Math.max(0, activeRequests - 1)")

    assert "if (next)" in release_slot[handoff:decrement]
    assert "next.resolve()" in release_slot[handoff:decrement]
    assert "return" in release_slot[handoff:decrement]


def test_mastra_bounds_context_and_searches_large_tool_catalogs():
    mastra = (ROOT / "mastra_server" / "src" / "mastra" / "index.ts").read_text(encoding="utf-8")

    assert "TokenLimiterProcessor" in mastra
    assert "LOBSTER_MASTRA_CONTEXT_TOKEN_LIMIT || 48000" in mastra
    assert "LOBSTER_MASTRA_LAST_MESSAGES || 10" in mastra
    assert "new ToolSearchProcessor" in mastra
    assert "topK: 3" in mastra
    assert "list_system_capabilities" in mastra
    assert "conversation_summary" in mastra
    assert "function runtimeContextFor" in mastra
    assert "context: runtimeContextFor(body)" in mastra
    assert "/internal/summarize" in mastra

    chat_input = mastra[mastra.index("function chatInputFor"):mastra.index("function validateChatBody")]
    assert "conversation_summary" not in chat_input
    assert "permissionNoticeFor" not in chat_input
