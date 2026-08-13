from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from celltrack.analysis.jobs import analysis_tasks, serialize_analysis_task
from celltrack.analysis.models import AnalysisParameters, analysis_options
from celltrack.analysis.storage import AnalysisNotFoundError, analysis_payload, analysis_store


router = APIRouter(prefix="/api/analysis")


class AnalysisGroup(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    dataset_ids: list[str] = Field(min_length=1)


class AnalysisRequest(BaseModel):
    groups: list[AnalysisGroup] = Field(min_length=2, max_length=6)
    parameters: AnalysisParameters = Field(default_factory=AnalysisParameters)


def _not_found(exc: AnalysisNotFoundError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


@router.get("/options")
def options() -> dict[str, object]:
    return analysis_options()


@router.post("", status_code=202)
def generate(request: AnalysisRequest) -> dict[str, object]:
    task = analysis_tasks.submit(
        [(group.name, group.dataset_ids) for group in request.groups],
        request.parameters,
    )
    return serialize_analysis_task(task)


@router.get("")
def history() -> list[dict[str, object]]:
    return [analysis_payload(manifest) for manifest in analysis_store.list()]


@router.get("/tasks/{task_id}")
def task_status(task_id: str) -> dict[str, object]:
    task = analysis_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Analysis task not found")
    return serialize_analysis_task(task)


@router.get("/{artifact_id}")
def result(artifact_id: str) -> dict[str, object]:
    try:
        return analysis_payload(analysis_store.read(artifact_id))
    except AnalysisNotFoundError as exc:
        raise _not_found(exc) from exc


@router.delete("/{artifact_id}", status_code=204)
def delete_result(artifact_id: str) -> Response:
    try:
        analysis_store.delete(artifact_id)
    except AnalysisNotFoundError as exc:
        raise _not_found(exc) from exc
    return Response(status_code=204)


@router.get("/{artifact_id}/images/{filename}")
def image(artifact_id: str, filename: str) -> FileResponse:
    try:
        return FileResponse(analysis_store.image_file(artifact_id, filename), media_type="image/png")
    except AnalysisNotFoundError as exc:
        raise _not_found(exc) from exc


@router.get("/{artifact_id}/images/{filename}/download")
def image_download(artifact_id: str, filename: str) -> FileResponse:
    try:
        path = analysis_store.image_file(artifact_id, filename)
        return FileResponse(path, media_type="image/png", filename=filename)
    except AnalysisNotFoundError as exc:
        raise _not_found(exc) from exc


@router.get("/{artifact_id}/download")
def download(artifact_id: str) -> FileResponse:
    try:
        path = analysis_store.data_file(artifact_id, "archive_file")
        return FileResponse(path, media_type="application/zip", filename="cell-tracking-analysis.zip")
    except AnalysisNotFoundError as exc:
        raise _not_found(exc) from exc


@router.get("/{artifact_id}/csv")
def csv_download(artifact_id: str) -> FileResponse:
    try:
        path = analysis_store.data_file(artifact_id, "csv_file")
        return FileResponse(path, media_type="text/csv", filename="cell-tracking-metrics.csv")
    except AnalysisNotFoundError as exc:
        raise _not_found(exc) from exc


@router.get("/{artifact_id}/statistics")
def statistics_download(artifact_id: str) -> FileResponse:
    try:
        path = analysis_store.data_file(artifact_id, "statistics_file")
        return FileResponse(path, media_type="text/csv", filename="cell-tracking-statistics.csv")
    except (AnalysisNotFoundError, KeyError) as exc:
        raise _not_found(AnalysisNotFoundError("Analysis statistics not found")) from exc
