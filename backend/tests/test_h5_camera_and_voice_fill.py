from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
H5_HTML = ROOT / "h5_static" / "index.html"
H5_APP = ROOT / "h5_static" / "h5-app.js"
H5_STYLE = ROOT / "h5_static" / "h5-designer-v2.css"


def test_asset_library_and_avatar_fields_expose_camera_capture() -> None:
    html = H5_HTML.read_text(encoding="utf-8")

    assert 'data-camera-target="assetLibraryUploadInput" data-camera-mode="media"' in html
    assert 'data-camera-target="assetAvatarFile" data-camera-mode="avatar"' in html
    assert 'data-camera-target="assetAvatarAuthFile" data-camera-mode="video"' in html
    assert 'id="assetAvatarTrainingText" data-voice-input' in html
    assert 'id="cameraCaptureModal"' in html
    assert 'id="cameraFacingSwitch"' in html
    assert 'id="cameraPermissionSettingsBtn"' in html
    assert 'id="cameraPermissionRetryBtn"' in html
    assert 'data-camera-select-mode="photo"' in html
    assert 'data-camera-select-mode="video"' in html


def test_camera_capture_defaults_to_front_camera_and_preserves_captured_files() -> None:
    script = H5_APP.read_text(encoding="utf-8")

    assert 'cameraFacingMode: "user"' in script
    assert 'facingMode: { ideal: state.cameraFacingMode }' in script
    assert 'width: { ideal: 1280 }' in script
    assert 'height: { ideal: 720 }' in script
    assert 'function cameraCanvasSize(live, longSide = 1280)' in script
    assert 'function drawCameraFrameToCanvas(canvas, live)' in script
    assert 'function createCameraRecordingRuntime(live)' in script
    assert 'window.LobsterAndroid.openAppSettings()' in script
    assert 'android.settings.MANAGE_APPLICATIONS_SETTINGS' in script
    assert 'await applyMinimumCameraZoom(stream);' in script
    assert 'await track.applyConstraints({ advanced: [{ zoom: minimumZoom }] });' in script
    assert 'state.cameraFacingMode = state.cameraFacingMode === "user" ? "environment" : "user";' in script
    assert 'function selectedFilesForInput(inputId, multiple = false)' in script
    assert 'state.cameraCapturedFiles[inputId] = rows;' in script
    assert 'const files = selectedFilesForInput("assetLibraryUploadInput", true);' in script
    assert 'const selectedFile = selectedFilesForInput("assetAvatarFile")[0] || null;' in script
    assert 'const authFile = selectedFilesForInput("assetAvatarAuthFile")[0] || null;' in script


def test_camera_controls_are_reenabled_after_stream_opening_finishes() -> None:
    script = H5_APP.read_text(encoding="utf-8")

    assert "state.cameraOpening = true;\n      syncCameraCaptureUi();" in script
    assert "const playPromise = live.play();" in script
    assert "await live.play()" not in script
    assert (
        "if (nonce === state.cameraSessionNonce) {\n"
        "          state.cameraOpening = false;\n"
        "          syncCameraCaptureUi();\n"
        "        }"
    ) in script


def test_camera_video_uses_supported_mime_and_avatar_teleprompter() -> None:
    script = H5_APP.read_text(encoding="utf-8")

    assert '"video/mp4;codecs=h264,aac"' in script
    assert '"video/webm;codecs=vp8,opus"' in script
    assert 'const runtime = createCameraRecordingRuntime($("cameraLiveVideo"));' in script
    assert 'const recordingStream = runtime?.stream || state.cameraStream;' in script
    assert 'new MediaRecorder(recordingStream' in script
    assert 'state.cameraTargetId === "assetAvatarAuthFile"' in script
    assert '$("assetAvatarTrainingText")?.value || defaultAssetAvatarTrainingText()' in script
    assert '$("assetAvatarAuthText")?.value || defaultAssetAvatarAuthText()' in script
    assert 'file.name} 已自动选中，可使用或重拍' in script


def test_prompt_fields_use_hold_to_talk_without_intent_resolution() -> None:
    script = H5_APP.read_text(encoding="utf-8")

    assert 'data-voice-input rows="3"' in script
    assert 'async function startFieldVoiceCapture(evt, field, button)' in script
    assert 'state.voiceFieldCancelled = state.voiceFieldStartY - Number(evt.clientY || 0) >= 58;' in script
    assert 'stopVoiceCapture(evt);' in script
    assert 'finishFieldVoiceRecognition(state.voiceDraft);' in script
    assert 'const ws = new WebSocket(voiceWsUrl(target === "voice"));' in script
    assert 'field.dispatchEvent(new Event("input", { bubbles: true }));' in script
    assert 'field.dispatchEvent(new Event("change", { bubbles: true }));' in script
    assert 'voiceFillObserver.observe(document.body, { childList: true, subtree: true });' in script


def test_camera_and_voice_controls_have_mobile_safe_layout() -> None:
    style = H5_STYLE.read_text(encoding="utf-8")

    assert ".capture-input-row" in style
    assert "grid-template-columns: minmax(0, 1fr) auto;" in style
    assert ".camera-capture-sheet" in style
    assert "min-height: 100dvh;" in style
    assert "object-fit: cover;" in style
    assert ".camera-teleprompter" in style
    assert ".voice-fill-shell" in style
    assert "touch-action: none;" in style


def test_ios_webclip_boot_and_resume_have_recovery_controls() -> None:
    html = H5_HTML.read_text(encoding="utf-8")
    script = H5_APP.read_text(encoding="utf-8")
    style = H5_STYLE.read_text(encoding="utf-8")

    assert 'background: #e5e7eb;' in html
    assert 'revealCachedShell' not in html
    assert 'id="h5RecoveryPanel"' in html
    assert 'id="h5RecoveryReload"' in html
    assert 'class="panel login" id="loginPanel"' in html
    assert 'var bootReloadKey = "lobster_h5_cold_boot_reloaded";' in html
    assert 'window.sessionStorage.getItem(bootReloadKey) === "1"' in html
    assert 'window.sessionStorage.setItem(bootReloadKey, "1")' in html
    assert 'window.__h5RecoveryGuard = { arm: arm, ready: hide, show: show };' in html
    assert 'async function recoverH5AfterResume(reason = "resume")' in script
    assert 'recoverH5AfterResume("visibility")' in script
    assert 'recoverH5AfterResume("pageshow_cache")' in script
    assert 'markH5PageReady("boot_ready");' in script
    assert 'recovered_cold_boot: recoveredColdBoot' in script
    assert ".h5-recovery-panel" in style
