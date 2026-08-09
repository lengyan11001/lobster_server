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
    assert open_voice.index("requestMicrophoneStream") < open_voice.index("new WebSocket")
    assert "sessionNonce !== state.voiceSessionNonce || !state.voiceRecording" in open_voice
    assert 'new Error("语音识别连接超时，请重试")' in open_voice
    assert "}, 8000);" in open_voice
    assert "function isTransientMicrophoneStartError" in script
    assert "function prepareAndroidMicrophoneCapture" in script
    assert "window.LobsterAndroid.prepareMicrophoneCapture()" in script
    assert "function reportMicrophoneStartupFailure" in script
    assert 'apiUrl("/api/h5-chat/voice/diagnostics")' in script
    assert "IS_ANDROID_APP ? [0, 700, 1600, 3000] : [0]" in script
    assert 'typeof canRetry === "function" && !canRetry()' in script
    assert "attempt === 0 ? constraints : { audio: true }" in script
    assert "cleanupAssetVoiceRecordRuntime();" in open_voice

    stop_voice_start = script.index("function stopVoiceCapture")
    stop_voice_end = script.index("function stopComposerVoiceDurationTimer", stop_voice_start)
    assert "}, 12000);" in script[stop_voice_start:stop_voice_end]
    assert 'document.addEventListener("pointermove"' in script
    assert 'document.addEventListener("pointerup"' in script
    assert 'document.addEventListener("pointercancel"' in script
    assert 'window.addEventListener("pagehide"' in script
    assert 'document.addEventListener("visibilitychange"' in script
    assert "stopAssetVoiceRecording(false)" in script
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
    assert "#messagesView .messages" in designer_styles
    assert "flex: 1 1 0" in designer_styles
    assert "overscroll-behavior: contain" in designer_styles


def test_h5_chat_restores_composer_after_each_task_result():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")

    assert "function ensureConversationComposerReady" in script
    assert 'input.placeholder = "继续输入下一条指令"' in script
    assert "send.disabled = !!state.chatSubmitPending" in script
    assert 'if (ev.type === "final")' in script
    assert 'if (ev.type === "error")' in script
    assert script.count("ensureConversationComposerReady();") >= 3


def test_h5_chat_history_does_not_replay_old_approvals_or_speech():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")

    assert "function handleEvent(ev, bubble, messageId, options = {})" in script
    assert "const historical = options.historical === true;" in script
    assert 'if (!historical && ev.type === "approval_required" && ev.payload)' in script
    assert "handleEvent(ev, bot, msg.id, { historical: true });" in script
    assert "startSse(msg.id, bot, lastHistoryEventId);" in script
    assert "&last_event_id=${last}" in script


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


def test_h5_chat_has_a_global_floating_entry_on_authenticated_views():
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    styles = (ROOT / "h5_static" / "h5-designer-v2.css").read_text(encoding="utf-8")

    app_start = html.index('id="appPanel"')
    app_end = html.index("</main>", app_start)
    assert app_start < html.index('id="globalChatFab"') < app_end
    assert 'aria-label="打开 AI 调度助手"' in html
    assert 'id="departmentChatBtn"' not in html
    assert 'id="abilityChatBtn"' not in html
    assert '.global-chat-fab {' in styles
    assert 'body.messages-view-active .global-chat-fab' in styles
    assert 'function setupGlobalChatFabDrag(button)' in script
    assert 'brandStorageKey("lobster_h5_global_chat_fab_position")' in script
    assert 'button.addEventListener("pointermove"' in script
    assert 'setupGlobalChatFabDrag(globalChatFab);' in script
    assert 'globalChatFab?.addEventListener("click"' in script
    assert 'touch-action: none;' in styles
    assert '.global-chat-fab.is-dragging' in styles
    assert 'sourceView === "department"' in script
    assert 'sourceView === "ability"' in script
    assert "openContextChat(context);" in script
    assert "scrollMessagesToBottom();" in script
    assert "focusMessageInput();" in script


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
    assert "recent_history" in mastra
    assert "function recentContextFor" in mastra
    assert "options: { lastMessages: false }" in mastra
    assert "function runtimeContextFor" in mastra
    assert "context: runtimeContextFor(body)" in mastra
    assert "/internal/summarize" in mastra

    chat_input = mastra[mastra.index("function chatInputFor"):mastra.index("function validateChatBody")]
    assert "conversation_summary" not in chat_input
    assert "permissionNoticeFor" not in chat_input
