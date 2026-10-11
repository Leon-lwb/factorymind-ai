"""Entry point: `python -m app` (spec §21)."""

from __future__ import annotations

import os

import uvicorn

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8100


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=os.getenv("AI_ENGINE_HOST", DEFAULT_HOST),
        port=int(os.getenv("AI_ENGINE_PORT", str(DEFAULT_PORT))),
        reload=False,
    )


if __name__ == "__main__":
    main()