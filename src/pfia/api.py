from __future__ import annotations

import argparse
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Event, Thread

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
)
from fastapi.staticfiles import StaticFiles

from pfia.config import Settings, get_settings
from pfia.errors import PFIAError
from pfia.services import PFIAService, build_app_context
from pfia.worker import worker_loop


logger = logging.getLogger("pfia.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    context = build_app_context(resolved_settings)
    service = PFIAService(context)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker_thread: Thread | None = None
        stop_event: Event | None = None

        if resolved_settings.embedded_worker:
            service.update_worker_heartbeat(mode="embedded")
            stop_event = Event()
            worker_thread = Thread(
                target=worker_loop,
                args=(service, resolved_settings),
                kwargs={"stop_event": stop_event, "heartbeat_mode": "embedded"},
                daemon=True,
                name="pfia-embedded-worker",
            )
            worker_thread.start()
            logger.info("Embedded worker started for single-service deployment mode.")

        try:
            yield
        finally:
            if stop_event is not None:
                stop_event.set()
            if worker_thread is not None:
                worker_thread.join(
                    timeout=max(2.0, resolved_settings.worker_poll_interval_s * 3)
                )
                logger.info("Embedded worker stopped.")

    app = FastAPI(title="PFIA", version="0.1.0", lifespan=lifespan)
    app.state.context = context
    app.state.service = service
    app.state.embedded_worker = bool(resolved_settings.embedded_worker)

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.exception_handler(PFIAError)
    async def pfia_error_handler(_: Request, exc: PFIAError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "message": exc.message},
        )

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready() -> JSONResponse:
        readiness = service.readiness()
        return JSONResponse(
            status_code=200 if readiness["ready"] else 503, content=readiness
        )

    @app.get("/metrics")
    async def metrics() -> PlainTextResponse:
        return PlainTextResponse(
            context.metrics.render().decode("utf-8"),
            media_type="text/plain; version=0.0.4",
        )

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse((static_dir / "index.html").read_text(encoding="utf-8"))

    @app.get("/api/demo/sample-file")
    async def demo_sample() -> FileResponse:
        demo_path = Path("data/demo/mobile_app_reviews.csv")
        if not demo_path.exists():
            raise HTTPException(status_code=404, detail="Demo sample is missing.")
        return FileResponse(path=demo_path, filename="mobile_app_reviews.csv")

    @app.post("/api/sessions/upload")
    async def upload(file: UploadFile = File(...)):
        payload = await file.read()
        return service.upload_file(
            file.filename or "reviews.csv", payload, file.content_type
        ).model_dump(mode="json")

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str):
        return service.get_session_detail(session_id)

    @app.get("/api/sessions/{session_id}/report")
    async def get_report(session_id: str):
        detail = service.get_session_detail(session_id)
        report = detail.get("report")
        if report is None:
            raise HTTPException(status_code=404, detail="Report is not available yet.")
        return report

    @app.post("/api/sessions/{session_id}/chat")
    async def chat(session_id: str, payload: dict[str, str]):
        question = (payload.get("question") or "").strip()
        if len(question) < 3:
            raise HTTPException(
                status_code=422, detail="Question must contain at least 3 characters."
            )
        return service.chat(session_id, question)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run PFIA API.")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    settings = get_settings()
    uvicorn.run(
        "pfia.api:create_app",
        factory=True,
        host=args.host or settings.host,
        port=args.port or settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
