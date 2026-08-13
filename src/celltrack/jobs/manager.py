from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import uuid

from celltrack.datasets import Dataset, segmentation_complete
from celltrack.settings import PROJECT_ROOT, SEGMENTATION_CONFIG, TRACKING_CONFIG, settings


SEGMENT_PROGRESS_RE = re.compile(r"^\[(\d+)/(\d+)\]")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    kind: str
    dataset_ids: list[str]
    status: str = "queued"
    progress: int = 0
    total: int = 0
    current_dataset: str | None = None
    current_dataset_id: str | None = None
    completed_dataset_ids: list[str] = field(default_factory=list)
    item_progress: int = 0
    item_total: int = 0
    message: str = "Waiting to start"
    error: str | None = None
    created_at: str = field(default_factory=now_iso)
    completed_at: str | None = None
    cancel_requested: bool = False


class JobCancelled(Exception):
    """Raised when a queued or running job is cancelled by the user."""


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="celltrack-job")
        self._futures: dict[str, Future] = {}
        self._processes: dict[str, subprocess.Popen] = {}

    def submit(self, kind: str, datasets: list[Dataset], force: bool) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, dataset_ids=[item.id for item in datasets], total=len(datasets))
        with self._lock:
            self._jobs[job.id] = job
            self._futures[job.id] = self._executor.submit(self._run, job, datasets, force)
        return job

    def cancel(self, job_id: str) -> Job | None:
        process = None
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status in {"completed", "failed", "cancelled"}:
                return job
            job.cancel_requested = True
            job.status = "cancelling"
            job.message = "Cancelling"
            future = self._futures.get(job_id)
            if future is not None and future.cancel():
                job.status = "cancelled"
                job.message = "Cancelled"
                job.completed_at = now_iso()
            process = self._processes.get(job_id)
        if process is not None and process.poll() is None:
            process.terminate()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)

    def _update(self, job: Job, **values: object) -> None:
        with self._lock:
            for key, value in values.items():
                setattr(job, key, value)

    def _run(self, job: Job, datasets: list[Dataset], force: bool) -> None:
        if job.cancel_requested:
            self._update(job, status="cancelled", message="Cancelled", completed_at=now_iso())
            return
        self._update(job, status="running", message="Starting")
        try:
            for index, dataset in enumerate(datasets):
                self._raise_if_cancelled(job)
                self._update(
                    job,
                    current_dataset=dataset.relative_path,
                    current_dataset_id=dataset.id,
                    item_progress=0,
                    item_total=len(dataset.images) if job.kind == "segmentation" else 0,
                    message=f"Processing {dataset.name}",
                )
                if job.kind == "segmentation":
                    self._segment(job, dataset, force)
                elif job.kind == "tracking":
                    self._track(job, dataset, force)
                else:
                    raise ValueError(f"Unsupported job kind: {job.kind}")
                self._update(
                    job,
                    progress=index + 1,
                    completed_dataset_ids=[*job.completed_dataset_ids, dataset.id],
                )
            self._update(
                job,
                status="completed",
                current_dataset=None,
                current_dataset_id=None,
                message="Completed",
                completed_at=now_iso(),
            )
        except JobCancelled:
            self._update(job, status="cancelled", message="Cancelled", completed_at=now_iso())
        except Exception as exc:
            self._update(job, status="failed", error=str(exc), message="Failed", completed_at=now_iso())
        finally:
            with self._lock:
                self._processes.pop(job.id, None)

    @staticmethod
    def _raise_if_cancelled(job: Job) -> None:
        if job.cancel_requested:
            raise JobCancelled()

    @staticmethod
    def _write_manifest(path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _run_command(self, command: list[str], on_line=None, job: Job | None = None) -> None:
        if job is not None:
            self._raise_if_cancelled(job)
        environment = os.environ.copy()
        source_path = str(PROJECT_ROOT / "src")
        environment["PYTHONPATH"] = source_path + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
        process = subprocess.Popen(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            env=environment,
        )
        if job is not None:
            with self._lock:
                self._processes[job.id] = process
                should_cancel = job.cancel_requested
            if should_cancel:
                process.terminate()
        output_tail: list[str] = []
        assert process.stdout is not None
        for line in process.stdout:
            cleaned = line.rstrip()
            output_tail.append(cleaned)
            output_tail = output_tail[-40:]
            if on_line is not None:
                on_line(cleaned)
        process.stdout.close()
        return_code = process.wait()
        if job is not None:
            with self._lock:
                self._processes.pop(job.id, None)
            if job.cancel_requested:
                raise JobCancelled()
        if return_code:
            detail = "\n".join(output_tail).strip()
            raise RuntimeError(detail[-2000:] or f"Command failed with exit code {return_code}")

    def _segment(self, job: Job, dataset: Dataset, force: bool) -> None:
        if segmentation_complete(dataset) and not force:
            self._update(job, item_progress=len(dataset.images), item_total=len(dataset.images))
            return
        if force and dataset.segmentation_dir.exists():
            shutil.rmtree(dataset.segmentation_dir)
        if force and dataset.tracking_dir.exists():
            shutil.rmtree(dataset.tracking_dir)
        dataset.segmentation_dir.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable, "-u", "-m", "celltrack.cli", "segment", str(dataset.path),
            "--weights", str(settings.weights_path),
            "--output-dir", str(dataset.segmentation_dir),
            "--conf", str(SEGMENTATION_CONFIG["conf"]),
            "--iou", str(SEGMENTATION_CONFIG["iou"]),
            "--imgsz", str(SEGMENTATION_CONFIG["imgsz"]),
            "--no-annotated",
        ]
        def update_progress(line: str) -> None:
            match = SEGMENT_PROGRESS_RE.match(line)
            if match:
                current, total = (int(value) for value in match.groups())
                self._update(
                    job,
                    item_progress=current,
                    item_total=total,
                    message=f"Segmented {current} of {total} images",
                )

        self._run_command(command, on_line=update_progress, job=job)
        self._update(job, item_progress=len(dataset.images), item_total=len(dataset.images))
        self._write_manifest(dataset.segmentation_dir / "manifest.json", {
            "status": "completed", "completed_at": now_iso(), "dataset": dataset.relative_path,
            "image_count": len(dataset.images), **SEGMENTATION_CONFIG,
        })

    def _track(self, job: Job, dataset: Dataset, force: bool) -> None:
        if not segmentation_complete(dataset):
            raise ValueError(f"Run segmentation first: {dataset.relative_path}")
        manifest_path = dataset.tracking_dir / "manifest.json"
        if manifest_path.exists() and dataset.tracking_csv.exists() and not force:
            return
        if force and dataset.tracking_dir.exists():
            shutil.rmtree(dataset.tracking_dir)
        command = [
            sys.executable, "-m", "celltrack.cli", "track",
            "--data-path", str(dataset.path),
            "--labels-dir", str(dataset.labels_dir),
            "--output-dir", str(dataset.tracking_dir),
            "--distance-schedule", *[str(value) for value in TRACKING_CONFIG["distance_schedule"]],
            "--max-lookback", str(TRACKING_CONFIG["max_lookback"]),
            "--min-track-length", str(TRACKING_CONFIG["min_track_length"]),
        ]
        self._run_command(command, job=job)
        self._write_manifest(manifest_path, {
            "status": "completed", "completed_at": now_iso(), "dataset": dataset.relative_path,
            **TRACKING_CONFIG,
        })


jobs = JobManager()


def serialize_job(job: Job) -> dict[str, object]:
    return asdict(job)
