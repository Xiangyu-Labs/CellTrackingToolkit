from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Iterable

from celltrack.settings import IMAGE_SUFFIXES, settings


def natural_key(value: str) -> list[object]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


@dataclass(frozen=True)
class Dataset:
    id: str
    name: str
    group_path: str
    relative_path: str
    path: Path
    images: tuple[Path, ...]
    duplicate_images: tuple[Path, ...] = ()

    @property
    def result_dir(self) -> Path:
        return settings.results_dir / self.id

    @property
    def segmentation_dir(self) -> Path:
        return self.result_dir / "segmentation"

    @property
    def labels_dir(self) -> Path:
        return self.segmentation_dir / "labels"

    @property
    def tracking_dir(self) -> Path:
        return self.result_dir / "tracking"

    @property
    def tracking_csv(self) -> Path:
        return self.tracking_dir / "tracking_results.csv"


def _file_digest(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _partition_duplicates(paths: tuple[Path, ...]) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """Split a frame sequence into unique frames and byte-identical repeats.

    Copies such as ``frame_001 (1).tif`` sort straight after the original and
    would otherwise enter the sequence as an extra, motionless frame. Only files
    that share a size are hashed, so discovery stays cheap on large datasets.
    """
    by_size: dict[int, list[Path]] = {}
    for path in paths:
        try:
            by_size.setdefault(path.stat().st_size, []).append(path)
        except OSError:
            by_size.setdefault(-1, []).append(path)
    repeated: set[Path] = set()
    for group in by_size.values():
        if len(group) < 2:
            continue
        identical: dict[str, list[Path]] = {}
        for path in group:
            try:
                digest = _file_digest(path)
            except OSError:
                continue
            identical.setdefault(digest, []).append(path)
        for matches in identical.values():
            if len(matches) < 2:
                continue
            # Keep the plainest name: "frame_001.tif" over "frame_001 (1).tif",
            # which natural ordering would otherwise put first.
            keeper = min(matches, key=lambda item: (len(item.name), natural_key(item.name)))
            repeated.update(path for path in matches if path != keeper)
    images = tuple(path for path in paths if path not in repeated)
    duplicates = tuple(path for path in paths if path in repeated)
    return images, duplicates


def _dataset_id(relative_path: str) -> str:
    return hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:12]


def discover_datasets(root: Path | None = None) -> list[Dataset]:
    root = root or settings.datasets_dir
    if not root.exists():
        return []
    datasets: list[Dataset] = []
    for directory in (path for path in root.rglob("*") if path.is_dir()):
        candidates = tuple(sorted(
            (path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES),
            key=lambda path: natural_key(path.name),
        ))
        if not candidates:
            continue
        images, duplicates = _partition_duplicates(candidates)
        relative = directory.relative_to(root).as_posix()
        parent = directory.parent.relative_to(root).as_posix()
        datasets.append(Dataset(
            id=_dataset_id(relative),
            name=directory.name,
            group_path="" if parent == "." else parent,
            relative_path=relative,
            path=directory,
            images=images,
            duplicate_images=duplicates,
        ))
    return sorted(datasets, key=lambda item: natural_key(item.relative_path))


def dataset_map(root: Path | None = None) -> dict[str, Dataset]:
    return {dataset.id: dataset for dataset in discover_datasets(root)}


def read_manifest(path: Path) -> dict[str, object] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def segmentation_complete(dataset: Dataset) -> bool:
    manifest = read_manifest(dataset.segmentation_dir / "manifest.json")
    if not manifest or manifest.get("status") != "completed":
        return False
    labels = [path for path in dataset.labels_dir.glob("*.txt") if path.is_file()]
    return len(labels) == len(dataset.images)


def tracking_complete(dataset: Dataset) -> bool:
    manifest = read_manifest(dataset.tracking_dir / "manifest.json")
    return bool(manifest and manifest.get("status") == "completed" and dataset.tracking_csv.is_file())


def serialize_dataset(dataset: Dataset) -> dict[str, object]:
    segment_manifest = read_manifest(dataset.segmentation_dir / "manifest.json")
    tracking_manifest = read_manifest(dataset.tracking_dir / "manifest.json")
    return {
        "id": dataset.id,
        "name": dataset.name,
        "group_path": dataset.group_path,
        "relative_path": dataset.relative_path,
        "image_count": len(dataset.images),
        "duplicate_image_count": len(dataset.duplicate_images),
        "preview_url": f"/api/datasets/{dataset.id}/preview",
        "segmentation": {
            "completed": segmentation_complete(dataset),
            "updated_at": segment_manifest.get("completed_at") if segment_manifest else None,
        },
        "tracking": {
            "completed": tracking_complete(dataset),
            "updated_at": tracking_manifest.get("completed_at") if tracking_manifest else None,
        },
    }


def require_datasets(ids: Iterable[str], root: Path | None = None) -> list[Dataset]:
    available = dataset_map(root)
    requested = list(ids)
    missing = [dataset_id for dataset_id in requested if dataset_id not in available]
    if missing:
        raise ValueError(f"Unknown dataset id: {', '.join(missing)}")
    return [available[dataset_id] for dataset_id in requested]
