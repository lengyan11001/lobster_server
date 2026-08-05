from datetime import datetime, timedelta

from backend.app.services.device_presence import DEVICE_ONLINE_TTL_SECONDS, is_device_online


def test_device_is_online_through_heartbeat_fast_ack_window():
    now = datetime.utcnow()

    assert is_device_online(now - timedelta(seconds=55), now=now) is True


def test_device_online_ttl_boundary_is_inclusive():
    now = datetime.utcnow()

    assert is_device_online(now - timedelta(seconds=DEVICE_ONLINE_TTL_SECONDS), now=now) is True


def test_device_is_offline_after_ttl_or_without_heartbeat():
    now = datetime.utcnow()

    assert is_device_online(now - timedelta(seconds=DEVICE_ONLINE_TTL_SECONDS + 1), now=now) is False
    assert is_device_online(None, now=now) is False
