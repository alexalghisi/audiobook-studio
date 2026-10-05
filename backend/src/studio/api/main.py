from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from studio.api.routes import router
from studio.runtime import Runtime


def create_app(
    runtime: Runtime | None = None,
    static_dir: Path | None = None,
    runtime_factory: Callable[[], Runtime] = Runtime.from_settings,
) -> FastAPI:
    """With `runtime`, the app uses it as is. Otherwise it builds one at startup."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = runtime is None
        app.state.runtime = runtime or runtime_factory()
        try:
            yield
        finally:
            if owned:
                await app.state.runtime.aclose()

    app = FastAPI(title="Audiobook Studio", version="0.1.0", lifespan=lifespan)
    if runtime is not None:
        app.state.runtime = runtime
    app.include_router(router)
    if static_dir is not None and static_dir.is_dir():
        # Production build of the web UI, served from the same origin.
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
    return app


def run() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    static = os.environ.get("STUDIO_WEB_DIST")
    app = create_app(static_dir=Path(static) if static else None)
    uvicorn.run(
        app, host=os.environ.get("STUDIO_HOST", "127.0.0.1"), port=int(os.environ.get("STUDIO_PORT", "8700"))
    )


if __name__ == "__main__":
    run()
