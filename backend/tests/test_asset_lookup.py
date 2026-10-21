"""提交任务前的素材校验：已删素材必须能识别出来，不能再带进任务。"""

from backend.app.api.assets import AssetLookupBody, lookup_assets
from backend.app.models import Asset


def _add_asset(db_session, user_id, asset_id):
    db_session.add(
        Asset(
            asset_id=asset_id,
            user_id=user_id,
            filename=f"{asset_id}.mp4",
            media_type="video",
            file_size=100,
            source_url=f"https://example.test/{asset_id}.mp4",
        )
    )


def test_lookup_reports_missing_assets(db_session, test_user):
    _add_asset(db_session, test_user.id, "keep0001")
    db_session.commit()

    result = lookup_assets(
        AssetLookupBody(asset_ids=["keep0001", "gone0002", "keep0001"]),
        current_user=test_user,
        db=db_session,
    )

    assert result["found"] == ["keep0001"]
    assert result["missing"] == ["gone0002"]


def test_lookup_deduplicates_and_handles_empty(db_session, test_user):
    empty = lookup_assets(AssetLookupBody(asset_ids=[]), current_user=test_user, db=db_session)
    assert empty == {"ok": True, "found": [], "missing": []}

    _add_asset(db_session, test_user.id, "dup00001")
    db_session.commit()
    result = lookup_assets(
        AssetLookupBody(asset_ids=["dup00001", "dup00001", "  "]),
        current_user=test_user,
        db=db_session,
    )
    assert result["found"] == ["dup00001"]
    assert result["missing"] == []
