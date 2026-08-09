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
    assert 'data-camera-select-mode="photo"' in html
    assert 'data-camera-select-mode="video"' in html


def test_camera_capture_defaults_to_front_camera_and_preserves_captured_files() -> None:
    script = H5_APP.read_text(encoding="utf-8")

    assert 'cameraFacingMode: "user"' in script
    assert 'facingMode: { ideal: state.cameraFacingMode }' in script
    assert 'state.cameraFacingMode = state.cameraFacingMode === "user" ? "environment" : "user";' in script
    assert 'function selectedFilesForInput(inputId, multiple = false)' in script
    assert 'state.cameraCapturedFiles[inputId] = rows;' in script
    assert 'const files = selectedFilesForInput("assetLibraryUploadInput", true);' in script
    assert 'const selectedFile = selectedFilesForInput("assetAvatarFile")[0] || null;' in script
    assert 'const authFile = selectedFilesForInput("assetAvatarAuthFile")[0] || null;' in script


def test_camera_video_uses_supported_mime_and_avatar_teleprompter() -> None:
    script = H5_APP.read_text(encoding="utf-8")

    assert '"video/mp4;codecs=h264,aac"' in script
    assert '"video/webm;codecs=vp8,opus"' in script
    assert 'new MediaRecorder(state.cameraStream' in script
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
    assert ".camera-teleprompter" in style
    assert ".voice-fill-shell" in style
    assert "touch-action: none;" in style
