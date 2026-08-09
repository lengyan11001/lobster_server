from backend.app.api.mcp_gateway import _UPSTREAM_HEADER_ALLOWLIST


def test_pipeline_billing_headers_are_forwarded_to_server_mcp():
    assert {
        "x-lobster-pipeline-precharged",
        "x-lobster-pipeline-id",
        "x-lobster-pipeline-capability",
    }.issubset(_UPSTREAM_HEADER_ALLOWLIST)
