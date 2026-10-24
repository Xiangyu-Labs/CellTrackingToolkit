from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from celltrack.analysis.jobs import analysis_tasks, serialize_analysis_task
from celltrack.analysis.models import AnalysisParameters, analysis_options
from celltrack.analysis.service import rerender_groups
from celltrack.analysis.storage import AnalysisNotFoundError, analysis_payload, analysis_store, media_type
from celltrack.analysis.style import FigureStyle, style_options


router = APIRouter(prefix="/api/analysis")


class AnalysisGroup(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    dataset_ids: list[str] = Field(min_length=1)


class AnalysisRequest(BaseModel):
    groups: list[AnalysisGroup] = Field(min_length=2, max_length=6)
    parameters: AnalysisParameters = Field(default_factory=AnalysisParameters)
    style: FigureStyle | None = None


class RerenderGroup(BaseModel):
    source_index: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=40)


class RerenderUnits(BaseModel):
    """Physical scale. Lengths and times are converted; ratios and p-values stay the same."""

    microns_per_pixel: float | None = Field(default=None, gt=0, le=1000)
    frame_interval_minutes: float | None = Field(default=None, gt=0, le=10000)


class RerenderRequest(BaseModel):
    """Same datasets and parameters, new presentation; the old result stays.

    ``units`` is the only analysis setting that can change here, because it
    rescales numbers without changing which tracks are counted or how.
    """

    groups: list[RerenderGroup] | None = None
    style: FigureStyle = Field(default_factory=FigureStyle)
    units: RerenderUnits | None = None


def _not_found(exc: AnalysisNotFoundError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


@router.get("/options")
def options() -> dict[str, object]:
    return {**analysis_options(), "style": style_options()}


@router.post("", status_code=202)
def generate(request: AnalysisRequest) -> dict[str, object]:
    task = analysis_tasks.submit(
        [(group.name, group.dataset_ids) for group in request.groups],
        request.parameters,
        style=request.style,
    )
    return serialize_analysis_task(task)


@router.post("/{artifact_id}/rerender", status_code=202)
def rerender(artifact_id: str, request: RerenderRequest) -> dict[str, object]:
    try:
        manifest = analysis_store.read(artifact_id)
    except AnalysisNotFoundError as exc:
        raise _not_found(exc) from exc
    order = (
        [(group.source_index, group.name.strip()) for group in request.groups]
        if request.groups is not None
        else [(index, str(group["name"])) for index, group in enumerate(manifest["groups"])]
    )
    try:
        groups = rerender_groups(manifest, order)
        parameters = AnalysisParameters(**manifest["parameters"])
        if request.units is not None:
            parameters = parameters.model_copy(update=request.units.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    task = analysis_tasks.submit(groups, parameters, style=request.style, derived_from=artifact_id)
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
        return FileResponse(analysis_store.image_file(artifact_id, filename), media_type=media_type(filename))
    except AnalysisNotFoundError as exc:
        raise _not_found(exc) from exc


@router.get("/{artifact_id}/images/{filename}/download")
def image_download(artifact_id: str, filename: str) -> FileResponse:
    try:
        path = analysis_store.image_file(artifact_id, filename)
        return FileResponse(path, media_type=media_type(filename), filename=filename)
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
