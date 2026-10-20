from __future__ import annotations

import os

import uvicorn


def main() -> None:
    port = int(os.environ.get("NOTE_PORT") or "8050")
    uvicorn.run(
        "backend.app.note_main:app",
        host=os.environ.get("NOTE_HOST", "127.0.0.1"),
        port=port,
        workers=int(os.environ.get("NOTE_WORKERS") or "2"),
        log_level=os.environ.get("NOTE_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
