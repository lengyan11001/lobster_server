from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    from backend.app.api.capabilities import router as capabilities_router
    from backend.app.services.sutui_billing_gate import assert_pricing_pre_deduct_allows_upstream_or_http
    from backend.app.services.sutui_pricing import (
        estimate_credits_from_pricing,
        pricing_is_free_fixed,
    )
    from mcp.http_server import _call_tool

    assert capabilities_router is not None
    assert callable(assert_pricing_pre_deduct_allows_upstream_or_http)
    assert callable(estimate_credits_from_pricing)
    assert callable(pricing_is_free_fixed)
    assert callable(_call_tool)
    print("[OK] deploy preflight import check passed")


if __name__ == "__main__":
    main()
