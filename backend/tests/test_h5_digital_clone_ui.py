from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
H5 = ROOT / "h5_static"


def test_avatar_clone_ui_exposes_v1_and_v2_training_fields():
    html = (H5 / "index.html").read_text(encoding="utf-8")

    assert 'id="assetAvatarVersion"' in html
    assert '<option value="v2">数字人 2.0（需授权视频）</option>' in html
    assert '<option value="v1">数字人 1.0</option>' in html
    assert 'id="assetAvatarAuthFile"' in html
    assert 'id="assetAvatarAuthText"' in html
    assert 'id="assetAvatarAgree"' in html


def test_avatar_clone_dispatches_to_the_selected_provider():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")

    assert '"/api/hifly/my/avatar/create-by-video-upload"' in script
    assert '"/api/hifly/my/avatar/create-by-image-upload"' in script
    assert '"/api/shanjian-digital-human/profile/train"' in script
    assert 'auth_video_asset_id: authAsset.asset_id' in script
    assert 'mode: sourceType === "video" ? "fast_video" : "image"' in script


def test_h5_app_exposes_digital_human_v2_profile_routes():
    from backend.app.h5_main import app

    paths = {route.path for route in app.routes}
    assert "/api/shanjian-digital-human/profile/train" in paths
    assert "/api/shanjian-digital-human/profile/task" in paths
    assert "/api/shanjian-digital-human/profiles/{profile_id}" in paths


def test_voice_clone_can_record_wav_and_release_microphone():
    html = (H5 / "index.html").read_text(encoding="utf-8")
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")

    assert 'id="assetVoiceRecordBtn"' in html
    assert 'id="assetVoiceRecordPreview"' in html
    assert "requestMicrophoneStream({ audio: true }" in script
    assert "function prepareAndroidMicrophoneCapture" in script
    assert 'new Blob([buffer], { type: "audio/wav" })' in script
    assert 'new File([wav], `voice-record-${stamp}.wav`' in script
    assert "state.assetVoiceRecordStream.getTracks().forEach((track) => track.stop())" in script
    assert "resetAssetVoiceRecording();" in script
