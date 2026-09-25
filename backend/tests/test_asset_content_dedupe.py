"""内容哈希去重（2026-09-25：小程序生成 + Online 同步各写一条，图片字节完全相同）。"""
from backend.app.api import assets as assets_api
from backend.app.api.assets import (
    RegisterAssetUrlReq,
    _asset_content_sha256,
    _find_asset_by_content_sha256,
    upsert_registered_assets,
)
from backend.app.models import Asset


def _seed_asset(db, user_id, *, asset_id, size, sha, url="https://cdn.example.test/seed.png", media_type="image"):
    row = Asset(
        asset_id=asset_id,
        user_id=int(user_id),
        filename=asset_id + ".png",
        media_type=media_type,
        file_size=int(size),
        source_url=url,
        meta={"content_sha256": sha},
    )
    db.add(row)
    db.commit()
    return row


def test_find_asset_by_content_sha256_matches_cached_hash(db_session, test_user):
    sha = "ab" * 32
    row = _seed_asset(db_session, test_user.id, asset_id="aaaa1111", size=1234, sha=sha)

    found = _find_asset_by_content_sha256(
        db_session, test_user.id, media_type="image", file_size=1234, content_sha256=sha
    )
    assert found is not None
    assert found.asset_id == row.asset_id

    miss = _find_asset_by_content_sha256(
        db_session, test_user.id, media_type="image", file_size=1234, content_sha256="00" * 32
    )
    assert miss is None

    size_miss = _find_asset_by_content_sha256(
        db_session, test_user.id, media_type="image", file_size=9999, content_sha256=sha
    )
    assert size_miss is None


def test_asset_content_sha256_uses_meta_cache_without_download(db_session, test_user, monkeypatch):
    sha = "cd" * 32
    row = _seed_asset(db_session, test_user.id, asset_id="bbbb2222", size=2048, sha=sha)

    def _boom(*_args, **_kwargs):
        raise AssertionError("cached content hash must not trigger a download")

    monkeypatch.setattr(assets_api, "_remote_content_info", _boom)
    assert _asset_content_sha256(row) == sha


def test_register_batch_reuses_existing_asset_when_bytes_match(db_session, test_user, monkeypatch):
    sha = "ef" * 32
    existing = _seed_asset(
        db_session,
        test_user.id,
        asset_id="cccc3333",
        size=4096,
        sha=sha,
        url="https://cdn.example.test/first-write.png",
    )
    monkeypatch.setattr(assets_api, "_remote_content_info", lambda url, **_kwargs: (4096, sha))

    body = [
        RegisterAssetUrlReq(
            url="https://cdn.example.test/second-write.png",
            media_type="image",
            filename="second-write.png",
            source_asset_id="batch-dup-1",
        )
    ]
    rows, created, updated = upsert_registered_assets(
        db_session, test_user.id, body, registered_from="online_batch"
    )

    assert created == 0
    assert [row.asset_id for row in rows] == [existing.asset_id]
    assert db_session.query(Asset).count() == 1


def test_register_batch_still_creates_when_content_differs(db_session, test_user, monkeypatch):
    _seed_asset(db_session, test_user.id, asset_id="dddd4444", size=4096, sha="11" * 32)
    monkeypatch.setattr(assets_api, "_remote_content_info", lambda url, **_kwargs: (1024, "22" * 32))

    body = [
        RegisterAssetUrlReq(
            url="https://cdn.example.test/brand-new.png",
            media_type="image",
            filename="brand-new.png",
            source_asset_id="batch-new-1",
        )
    ]
    rows, created, _updated = upsert_registered_assets(
        db_session, test_user.id, body, registered_from="online_batch"
    )

    assert created == 1
    assert len(rows) == 1
    assert rows[0].meta.get("content_sha256") == "22" * 32
    db_session.flush()
    assert db_session.query(Asset).count() == 2
