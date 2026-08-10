import asyncio
import logging
from types import SimpleNamespace

from backend.app.api.h5_voice import H5LifecycleDiagnostic, h5_client_diagnostics


def test_h5_lifecycle_diagnostics_records_user_and_sanitized_context(caplog) -> None:
    body = H5LifecycleDiagnostic(
        event="resume_timeout",
        timeline_json='[{"event":"pagehide"}]\ncontinued',
        user_agent="Mobile Safari",
        path="/client/hikong#office",
        brand="hikong",
    )

    with caplog.at_level(logging.WARNING, logger="backend.app.api.h5_voice"):
        result = asyncio.run(h5_client_diagnostics(body, SimpleNamespace(id=42)))

    assert result == {"ok": True}
    assert "[h5_lifecycle] user_id=42 brand=hikong event=resume_timeout" in caplog.text
    assert 'timeline=[{"event":"pagehide"}] continued' in caplog.text
