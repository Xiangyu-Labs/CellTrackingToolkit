from __future__ import annotations

import csv

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
from celltrack.web.visualization import render_segmentation, render_tracking


router = APIRouter(prefix="/api")


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
    try:
        dataset = require_datasets([dataset_id])[0]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if kind == "segmentation" and segmentation_complete(dataset):
        renderer = render_segmentation
    elif kind == "tracking" and tracking_complete(dataset):
        renderer = render_tracking
    elif kind not in {"segmentation", "tracking"}:
        raise HTTPException(status_code=404, detail="Unknown result type")
    else:
        raise HTTPException(status_code=409, detail=f"{kind.title()} is not complete")
    try:
        content = renderer(dataset, frame_index)
    except (ValueError, OSError, csv.Error) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
