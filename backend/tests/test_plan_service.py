from backend.app.services.plan import scoring, service
from backend.app.services.plan.catalog import PLAN_CAPABILITIES
from backend.app.services.plan.skills import SKILLS
from backend.app.services.mastra_online_capabilities import (
    mastra_online_capabilities,
    validate_capability_catalog,
)


def test_plan_registry_complete():
    assert len(SKILLS) == 16
    assert service.selftest() == []


def test_scoring_deterministic():
    scores = {name: 8 for name, _w, _r in scoring.DIMENSIONS}
    first = scoring.total(scores)
    second = scoring.total(dict(scores))
    assert first == second
    assert 70 <= first <= 90


def test_scoring_verdict_thresholds():
    high = {name: 9 for name, _w, _r in scoring.DIMENSIONS}
    low = {name: 3 for name, _w, _r in scoring.DIMENSIONS}
    assert scoring.verdict(scoring.total(high)) == "做"
    assert scoring.verdict(scoring.total(low)) == "不做"


def test_plan_capabilities_registered():
    caps = mastra_online_capabilities()
    for key in PLAN_CAPABILITIES:
        assert key in caps, key
        assert caps[key]["layer"] == "plan"
        assert caps[key]["task_kind"] == "server_action"
        assert caps[key]["side_effect"] == "read"
    assert validate_capability_catalog() == []
