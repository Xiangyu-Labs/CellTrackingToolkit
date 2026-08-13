from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
from typing import Callable
import uuid

from celltrack.analysis.models import AnalysisParameters
from celltrack.analysis.service import create_analysis
from celltrack.analysis.storage import analysis_payload


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class AnalysisTask:
    id: str
    groups: list[dict[str, object]] = field(default_factory=list)
    parameters: dict[str, object] = field(default_factory=dict)
    status: str = "queued"
    message: str = "Waiting to start"
    error: str | None = None
    result: dict[str, object] | None = None
    created_at: str = field(default_factory=now_iso)
    completed_at: str | None = None


class AnalysisTaskManager:
    def __init__(
        self,
        creator: Callable[[list[tuple[str, list[str]]], AnalysisParameters], dict[str, object]] = create_analysis,
    ) -> None:
        self._creator = creator
        self._tasks: dict[str, AnalysisTask] = {}
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="celltrack-analysis")

    def submit(self, groups: list[tuple[str, list[str]]], parameters: AnalysisParameters) -> AnalysisTask:
        copied_groups = [(name, list(dataset_ids)) for name, dataset_ids in groups]
        copied_parameters = parameters.model_copy(deep=True)
        task = AnalysisTask(
            id=uuid.uuid4().hex[:12],
            groups=[{"name": name, "dataset_count": len(dataset_ids)} for name, dataset_ids in copied_groups],
            parameters=copied_parameters.model_dump(),
        )
        with self._lock:
            self._tasks[task.id] = task
            self._futures[task.id] = self._executor.submit(
                self._run, task, copied_groups, copied_parameters
            )
        return task

    def get(self, task_id: str) -> AnalysisTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)

    def _update(self, task: AnalysisTask, **values: object) -> None:
        with self._lock:
            for key, value in values.items():
                setattr(task, key, value)

    def _run(
        self,
        task: AnalysisTask,
        groups: list[tuple[str, list[str]]],
        parameters: AnalysisParameters,
    ) -> None:
        self._update(task, status="running", message="Generating comparison")
        try:
            manifest = self._creator(groups, parameters)
            self._update(
                task,
                status="completed",
                message="Completed",
                result=analysis_payload(manifest),
                completed_at=now_iso(),
            )
        except Exception as exc:
            self._update(
                task,
                status="failed",
                message="Failed",
                error=str(exc),
                completed_at=now_iso(),
            )


def serialize_analysis_task(task: AnalysisTask) -> dict[str, object]:
    return {
        "id": task.id,
        "groups": task.groups,
        "parameters": task.parameters,
        "status": task.status,
        "message": task.message,
        "error": task.error,
        "result": task.result,
        "created_at": task.created_at,
        "completed_at": task.completed_at,
    }


analysis_tasks = AnalysisTaskManager()
