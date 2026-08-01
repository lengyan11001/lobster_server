from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
H5_APP = ROOT / "h5_static" / "h5-app.js"
H5_CSS = ROOT / "h5_static" / "h5-app.css"


def _source() -> str:
    return H5_APP.read_text(encoding="utf-8")


def test_video_reference_pickers_enable_multiple_selection() -> None:
    source = _source()

    for picker_id in (
        "workComflyAsset",
        "workSeedanceAsset",
        "taskComflyAsset",
        "taskSeedanceAsset",
        "workflowParamComflyAsset",
        "workflowParamSeedanceAsset",
    ):
        assert f'assetPickerControlHtml("{picker_id}"' in source
        start = source.index(f'assetPickerControlHtml("{picker_id}"')
        assert "multiple: true" in source[start : start + 240]


def test_multi_picker_keeps_all_selected_assets_and_supports_removal() -> None:
    source = _source()

    assert 'data-asset-multiple="${multiple ? "1" : "0"}"' in source
    assert '${multiple ? "multiple" : ""} hidden' in source
    assert "else drafts.push(normalizeUserUploadAsset(item));" in source
    assert "setAssetPickerSelectionRows(target" in source
    assert 'data-asset-picker-remove-index="${index}"' in source
    assert "rows.splice(index, 1);" in source
    assert "assetPickerRowsForValues(id, currentValues, box)" in source
    assert "确认选择（${drafts.length}）" in source


def test_video_requests_send_primary_and_additional_references() -> None:
    source = _source()

    assert "function assetPickerImagePayload(id, fieldLabel)" in source
    assert "reference_asset_ids: referenceAssetIds" in source
    assert "reference_image_urls: referenceImageUrls" in source
    for picker_id in (
        "workComflyAsset",
        "workSeedanceAsset",
        "taskComflyAsset",
        "taskSeedanceAsset",
        "workflowParamComflyAsset",
        "workflowParamSeedanceAsset",
    ):
        assert f'assetPickerImagePayload("{picker_id}"' in source


def test_multi_picker_preview_wraps_and_keeps_individual_items_visible() -> None:
    css = H5_CSS.read_text(encoding="utf-8")

    assert ".asset-picker-preview-item" in css
    assert "flex-wrap: wrap" in css
