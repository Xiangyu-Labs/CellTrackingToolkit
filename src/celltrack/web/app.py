from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from celltrack import __version__
from celltrack.analysis.storage import AnalysisNotFoundError, analysis_store
from celltrack.web.routes import analysis, datasets, jobs


STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Cell Tracking Studio", version=__version__)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(datasets.router)
app.include_router(jobs.router)
app.include_router(analysis.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "app": "cell-tracking-studio",
        "version": __version__,
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/analysis/tasks/{task_id}")
def analysis_task_page(task_id: str) -> FileResponse:
    return FileResponse(STATIC_DIR / "analysis-result.html")


@app.get("/analysis/{artifact_id}")
def analysis_result_page(artifact_id: str) -> FileResponse:
    try:
        analysis_store.read(artifact_id)
    except AnalysisNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(STATIC_DIR / "analysis-result.html")
