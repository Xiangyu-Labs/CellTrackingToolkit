from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _path_from_env(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else default.resolve()


@dataclass(frozen=True)
class Settings:
    datasets_dir: Path
    workspace_dir: Path
    weights_path: Path

    @property
    def results_dir(self) -> Path:
        return self.workspace_dir / "results"

    @property
    def analysis_dir(self) -> Path:
        return self.workspace_dir / "analysis"


settings = Settings(
    datasets_dir=_path_from_env("CELLTRACK_DATASETS_DIR", PROJECT_ROOT / "Datasets"),
    workspace_dir=_path_from_env("CELLTRACK_WORKSPACE_DIR", PROJECT_ROOT / "workspace"),
    weights_path=_path_from_env(
        "CELLTRACK_WEIGHTS_PATH",
        PROJECT_ROOT / "models" / "segmentation" / "yolo11x-seg.pt",
    ),
)

IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff"}
SEGMENTATION_CONFIG = {"conf": 0.2, "iou": 0.4, "imgsz": 640}
TRACKING_CONFIG = {
    "distance_schedule": [5, 8, 12, 18, 25],
    "max_lookback": 5,
    "min_track_length": 3,
}
