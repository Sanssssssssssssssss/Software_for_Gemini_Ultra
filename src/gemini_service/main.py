from __future__ import annotations

import uvicorn

from .app import create_app
from .core.config import get_settings

app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "gemini_service.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.env == "development",
        factory=False,
    )


if __name__ == "__main__":
    run()
