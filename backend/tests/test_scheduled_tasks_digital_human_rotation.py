from backend.app.api.scheduled_tasks import _digital_human_sequence_slot


def test_legacy_activation_assigns_distinct_slots_to_each_digital_human_task():
    ordered_ids = [10, 11, 12, 13, 14]
    payloads = {
        10: {"action": "local_bestseller_daily_video"},
        11: {"action": "shanjian_digital_human_video"},
        12: {"action": "native_wechat_poll"},
        13: {"action": "shanjian_digital_human_video"},
        14: {"action": "shanjian_digital_human_video"},
    }

    assert _digital_human_sequence_slot(11, ordered_ids, payloads) == 0
    assert _digital_human_sequence_slot(13, ordered_ids, payloads) == 1
    assert _digital_human_sequence_slot(14, ordered_ids, payloads) == 2


def test_sequence_slot_ignores_non_digital_human_task():
    assert _digital_human_sequence_slot(
        10,
        [10],
        {10: {"action": "local_bestseller_daily_video"}},
    ) is None
