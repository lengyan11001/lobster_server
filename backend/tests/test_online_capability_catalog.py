from backend.app.services.mastra_online_capabilities import (
    CATALOG_VERSION,
    mastra_online_capabilities,
    validate_capability_catalog,
)


def test_catalog_metadata_complete():
    problems = validate_capability_catalog()
    assert not problems, problems


def test_catalog_has_version_and_layers():
    capabilities = mastra_online_capabilities()
    assert CATALOG_VERSION
    layers = {cfg["layer"] for cfg in capabilities.values()}
    assert {"produce", "publish", "ops", "plan", "discover"} & layers


def test_every_capability_declares_boundary_and_degrade():
    for cid, cfg in mastra_online_capabilities().items():
        assert cfg["boundary"].get("not_for"), cid
        assert cfg["degrade"], cid
        assert cfg["outputs"].get("status"), cid
