from __future__ import annotations

import csv
from pathlib import Path

from starlette.background import BackgroundTask
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from celltrack.datasets import (
    discover_datasets,
    require_datasets,
    segmentation_complete,
    serialize_dataset,
    tracking_complete,
)
from celltrack.settings import SEGMENTATION_CONFIG, TRACKING_CONFIG, settings
from celltrack.web.result_exports import ResultDataError, create_result_archive
from celltrack.web.visualization import render_segmentation, render_tracking


router = APIRouter(prefix="/api")


def _result_renderer(dataset, kind: str):
    if kind == "segmentation" and segmentation_complete(dataset):
        return render_segmentation
    if kind == "tracking" and tracking_complete(dataset):
        return render_tracking
    if kind not in {"segmentation", "tracking"}:
        raise HTTPException(status_code=404, detail="Unknown result type")
    raise HTTPException(status_code=409, detail=f"{kind.title()} is not complete")


def _dataset(dataset_id: str):
    try:
        return require_datasets([dataset_id])[0]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


RESULT_DATA_ERRORS = (ValueError, OSError, csv.Error, SyntaxError, KeyError, TypeError)


@router.get("/overview")
def overview() -> dict[str, object]:
    datasets = discover_datasets()
    serialized = [serialize_dataset(dataset) for dataset in datasets]
    return {
        "datasets_root": str(settings.datasets_dir),
        "weights_ready": settings.weights_path.is_file(),
        "parameters": {"segmentation": SEGMENTATION_CONFIG, "tracking": TRACKING_CONFIG},
        "counts": {
            "datasets": len(datasets),
            "segmented": sum(bool(item["segmentation"]["completed"]) for item in serialized),
            "tracked": sum(bool(item["tracking"]["completed"]) for item in serialized),
        },
        "datasets": serialized,
    }


@router.get("/datasets/{dataset_id}/preview")
def preview(dataset_id: str) -> FileResponse:
    try:
        dataset = require_datasets([dataset_id])[0]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(dataset.images[0])


@router.get("/datasets/{dataset_id}/results/{kind}/frames/{frame_index}")
def result_frame(dataset_id: str, kind: str, frame_index: int) -> Response:
    dataset = _dataset(dataset_id)
    renderer = _result_renderer(dataset, kind)
    try:
        content = renderer(dataset, frame_index)
    except RESULT_DATA_ERRORS as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/datasets/{dataset_id}/results/{kind}/frames/{frame_index}/download")
def download_result_frame(dataset_id: str, kind: str, frame_index: int) -> Response:
    dataset = _dataset(dataset_id)
    renderer = _result_renderer(dataset, kind)
    try:
        content = renderer(dataset, frame_index)
    except RESULT_DATA_ERRORS as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    filename = f"{dataset_id}-{frame_index:04d}-{kind}.jpg"
    return Response(
        content,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/datasets/{dataset_id}/results/{kind}/download")
def download_results(dataset_id: str, kind: str) -> FileResponse:
    dataset = _dataset(dataset_id)
    _result_renderer(dataset, kind)
    try:
        archive_path = create_result_archive(dataset, kind)
    except ResultDataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Could not create result archive") from exc
    return FileResponse(
        archive_path,
        media_type="application/zip",
        filename=f"{dataset_id}-{kind}-results.zip",
        background=BackgroundTask(Path.unlink, archive_path, missing_ok=True),
    )
