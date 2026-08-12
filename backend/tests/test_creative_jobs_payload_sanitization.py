from backend.app.api.creative_jobs import _compact_job_payload


def test_compact_job_payload_omits_media_data_urls_and_base64_blobs():
    payload = {
        "status": "completed",
        "data": [
            {
                "url": "https://cdn.example.com/out.png",
                "data_url": "data:image/png;base64," + ("a" * 5000),
                "b64_json": "b" * 5000,
            }
        ],
    }

    compacted = _compact_job_payload(payload)

    assert compacted["status"] == "completed"
    assert compacted["data"][0]["url"] == "https://cdn.example.com/out.png"
    assert compacted["data"][0]["data_url"]["omitted"] is True
    assert compacted["data"][0]["data_url"]["kind"] == "data_url"
    assert compacted["data"][0]["b64_json"]["omitted"] is True
    assert compacted["data"][0]["b64_json"]["kind"] == "base64"


def test_compact_job_payload_truncates_large_raw_response_but_keeps_small_fields():
    payload = {
        "prompt": "make a product image",
        "raw_response": {"body": "x" * 30000},
        "items": [{"title": "useful"}],
    }

    compacted = _compact_job_payload(payload, string_limit=1024, max_items=10)

    assert compacted["prompt"] == "make a product image"
    assert compacted["items"] == [{"title": "useful"}]
    assert compacted["raw_response"]["omitted"] is True
    assert compacted["raw_response"]["kind"] == "raw_payload"


def test_saved_assets_and_meta_are_compacted_when_applied_to_job():
    from backend.app.api.creative_jobs import CreativeJobPatchBody, _apply_payload

    row = type(
        "Row",
        (),
        {
            "provider_task_id": None,
            "status": "running",
            "stage": None,
            "progress": None,
            "title": None,
            "prompt": None,
            "request_payload": None,
            "result_payload": None,
            "saved_assets": None,
            "asset_ids": None,
            "error": None,
            "meta": {},
            "completed_at": None,
        },
    )()

    _apply_payload(
        row,
        CreativeJobPatchBody(
            saved_assets=[{"asset_id": "asset-1", "data_url": "data:image/png;base64," + ("x" * 5000)}],
            meta={"raw_response": {"body": "y" * 20000}},
        ),
    )

    assert row.saved_assets[0]["asset_id"] == "asset-1"
    assert row.saved_assets[0]["data_url"]["omitted"] is True
    assert row.meta["raw_response"]["omitted"] is True
