from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import shutil
import uuid
import zipfile

from celltrack.analysis.models import DEFAULT_ANALYSIS_PARAMETERS
from celltrack.analysis.style import DEFAULT_FIGURE_STYLE
from celltrack.settings import settings


ANALYSIS_ID_RE = re.compile(r"^[0-9a-f]{16}$")
MEDIA_TYPES = {
    ".png": "image/png",
    ".tiff": "image/tiff",
    ".pdf": "application/pdf",
    ".svg": "image/svg+xml",
}


def media_type(filename: str) -> str:
    return MEDIA_TYPES.get(Path(filename).suffix.lower(), "application/octet-stream")


def _alternate_name(filename: str, file_format: str) -> str:
    return f"{filename.removesuffix('.png')}.{file_format}"


class AnalysisNotFoundError(LookupError):
    pass


class AnalysisStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.analysis_dir

    def create(
        self,
        images: dict[str, bytes],
        csv_content: bytes,
        groups: list[dict[str, object]],
        parameters: dict[str, object],
        statistics_content: bytes = b"",
        style: dict[str, object] | None = None,
        derived_from: str | None = None,
    ) -> dict[str, object]:
        artifact_id = uuid.uuid4().hex[:16]
        self.root.mkdir(parents=True, exist_ok=True)
        result_dir = self.root / artifact_id
        temporary_dir = self.root / f".{artifact_id}.tmp"
        temporary_dir.mkdir()
        try:
            image_entries = []
            archived = []
            for filename, content in images.items():
                (temporary_dir / filename).write_bytes(content)
                archived.append(filename)
                extras = getattr(content, "extras", {})
                for file_format, extra in extras.items():
                    alternate = _alternate_name(filename, file_format)
                    (temporary_dir / alternate).write_bytes(extra)
                    archived.append(alternate)
                image_entries.append({
                    "name": filename,
                    "title": getattr(content, "label", "") or filename.removesuffix(".png").replace("_", " ").title(),
                    "formats": ["png", *extras],
                    "texts": list(getattr(content, "texts", ())),
                    "warnings": list(getattr(content, "warnings", ())),
                })
            csv_name = "cell-tracking-metrics.csv"
            statistics_name = "cell-tracking-statistics.csv"
            zip_name = "cell-tracking-analysis.zip"
            (temporary_dir / csv_name).write_bytes(csv_content)
            (temporary_dir / statistics_name).write_bytes(statistics_content)
            with zipfile.ZipFile(temporary_dir / zip_name, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for filename in archived:
                    archive.write(temporary_dir / filename, arcname=filename)
                archive.write(temporary_dir / csv_name, arcname=csv_name)
                archive.write(temporary_dir / statistics_name, arcname=statistics_name)
            manifest: dict[str, object] = {
                "schema_version": 5,
                "id": artifact_id,
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "images": image_entries,
                "groups": groups,
                "parameters": parameters,
                "csv_file": csv_name,
                "statistics_file": statistics_name,
                "archive_file": zip_name,
                "style": style or DEFAULT_FIGURE_STYLE.model_dump(),
                "derived_from": derived_from,
            }
            (temporary_dir / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary_dir.rename(result_dir)
            return manifest
        except Exception:
            shutil.rmtree(temporary_dir, ignore_errors=True)
            raise

    def read(self, artifact_id: str) -> dict[str, object]:
        self._validate_id(artifact_id)
        try:
            raw = json.loads((self.root / artifact_id / "manifest.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise AnalysisNotFoundError("Analysis result not found") from exc
        return self._normalize(raw)

    def list(self) -> list[dict[str, object]]:
        if not self.root.exists():
            return []
        results = []
        for directory in self.root.iterdir():
            if not directory.is_dir() or not ANALYSIS_ID_RE.fullmatch(directory.name):
                continue
            try:
                results.append(self.read(directory.name))
            except AnalysisNotFoundError:
                continue
        return sorted(results, key=lambda item: str(item["created_at"]), reverse=True)

    def delete(self, artifact_id: str) -> None:
        self.read(artifact_id)
        result_dir = self.root / artifact_id
        deleting_dir = self.root / f".{artifact_id}.deleting"
        try:
            result_dir.rename(deleting_dir)
        except FileNotFoundError as exc:
            raise AnalysisNotFoundError("Analysis result not found") from exc
        shutil.rmtree(deleting_dir)

    def image_file(self, artifact_id: str, filename: str) -> Path:
        manifest = self.read(artifact_id)
        allowed = {
            name
            for image in manifest["images"]
            for name in (str(image["name"]), *(
                _alternate_name(str(image["name"]), str(item)) for item in image["formats"] if item != "png"
            ))
        }
        if filename not in allowed:
            raise AnalysisNotFoundError("Analysis image not found")
        return self.root / artifact_id / filename

    def data_file(self, artifact_id: str, key: str) -> Path:
        manifest = self.read(artifact_id)
        filename = str(manifest[key])
        return self.root / artifact_id / filename

    @staticmethod
    def _validate_id(artifact_id: str) -> None:
        if not ANALYSIS_ID_RE.fullmatch(artifact_id):
            raise AnalysisNotFoundError("Analysis result not found")

    @staticmethod
    def _normalize(manifest: dict[str, object]) -> dict[str, object]:
        normalized = dict(manifest)
        normalized.setdefault("schema_version", 1)
        normalized.setdefault("parameters", DEFAULT_ANALYSIS_PARAMETERS.model_dump())
        normalized.setdefault("csv_file", "cell-tracking-metrics.csv")
        normalized.setdefault("archive_file", "cell-tracking-analysis-images.zip")
        normalized.setdefault("style", DEFAULT_FIGURE_STYLE.model_dump())
        normalized.setdefault("derived_from", None)
        images = normalized.get("images", [])
        normalized["images"] = [
            {"formats": ["png"], "texts": [], "warnings": [], **image} if isinstance(image, dict) else {
                "name": str(image),
                "title": str(image).removesuffix(".png").replace("_", " ").title(),
                "formats": ["png"],
                "texts": [],
                "warnings": [],
            }
            for image in images
        ]
        return normalized


analysis_store = AnalysisStore()


def analysis_payload(manifest: dict[str, object]) -> dict[str, object]:
    artifact_id = str(manifest["id"])
    payload = {
        **manifest,
        "result_url": f"/analysis/{artifact_id}",
        "download_url": f"/api/analysis/{artifact_id}/download",
        "csv_url": f"/api/analysis/{artifact_id}/csv",
        "images": [{
            **image,
            "url": f"/api/analysis/{artifact_id}/images/{image['name']}",
            "download_url": f"/api/analysis/{artifact_id}/images/{image['name']}/download",
            "downloads": {
                str(file_format): f"/api/analysis/{artifact_id}/images/"
                f"{image['name'] if file_format == 'png' else _alternate_name(str(image['name']), str(file_format))}/download"
                for file_format in image["formats"]
            },
        } for image in manifest["images"]],
    }
    if manifest.get("statistics_file"):
        payload["statistics_url"] = f"/api/analysis/{artifact_id}/statistics"
    return payload
