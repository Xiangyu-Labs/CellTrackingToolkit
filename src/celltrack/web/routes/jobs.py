from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from celltrack.datasets import require_datasets
from celltrack.jobs import jobs, serialize_job


router = APIRouter(prefix="/api/jobs")


class JobRequest(BaseModel):
    dataset_ids: list[str] = Field(min_length=1)
    force: bool = False


def _start(kind: str, request: JobRequest) -> dict[str, object]:
    try:
        datasets = require_datasets(request.dataset_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if kind == "tracking":
        unavailable = [dataset.relative_path for dataset in datasets if not dataset.labels_dir.exists()]
        if unavailable:
            raise HTTPException(status_code=400, detail=f"Run segmentation first: {', '.join(unavailable)}")
    return serialize_job(jobs.submit(kind, datasets, request.force))


@router.post("/segmentation", status_code=202)
def start_segmentation(request: JobRequest) -> dict[str, object]:
    return _start("segmentation", request)


@router.post("/tracking", status_code=202)
def start_tracking(request: JobRequest) -> dict[str, object]:
    return _start("tracking", request)


@router.get("")
def list_jobs() -> list[dict[str, object]]:
    return [serialize_job(job) for job in jobs.list()]


@router.get("/{job_id}")
def get_job(job_id: str) -> dict[str, object]:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return serialize_job(job)


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, object]:
    job = jobs.cancel(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return serialize_job(job)
