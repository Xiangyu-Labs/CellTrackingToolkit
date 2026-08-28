from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import zipfile

from celltrack.datasets import Dataset
from celltrack.web.visualization import iter_rendered_frames


class ResultDataError(Exception):
    pass


SOURCE_ERRORS = (ValueError, OSError, csv.Error, SyntaxError, KeyError, TypeError)


def _source_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except SOURCE_ERRORS as exc:
        raise ResultDataError(str(exc)) from exc


def create_result_archive(dataset: Dataset, kind: str) -> Path:
    temporary = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    archive_path = Path(temporary.name)
    temporary.close()
    try:
        tracking_csv = _source_bytes(dataset.tracking_csv) if kind == "tracking" else None
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            frames = iter(iter_rendered_frames(dataset, kind, tracking_csv))
            while True:
                try:
                    filename, content = next(frames)
                except StopIteration:
                    break
                except SOURCE_ERRORS as exc:
                    raise ResultDataError(str(exc)) from exc
                archive.writestr(f"images/{filename}", content)
            if kind == "segmentation":
                for image_path in dataset.images:
                    label_path = dataset.labels_dir / f"{image_path.stem}.txt"
                    archive.writestr(f"data/labels/{label_path.name}", _source_bytes(label_path))
            elif kind == "tracking":
                archive.writestr("data/tracking_results.csv", tracking_csv)
            else:
                raise ValueError(f"Unknown result type: {kind}")
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    return archive_path
