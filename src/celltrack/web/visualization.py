from __future__ import annotations

import ast
import csv
import io
from pathlib import Path
from typing import Iterator

from PIL import Image, ImageDraw

from celltrack.datasets import Dataset


COLORS = (
    (21, 111, 98),
    (217, 119, 6),
    (37, 99, 235),
    (180, 35, 24),
    (124, 58, 237),
    (8, 126, 164),
    (190, 24, 93),
    (77, 99, 87),
)


def _frame(dataset: Dataset, frame_index: int) -> Path:
    if frame_index < 1 or frame_index > len(dataset.images):
        raise ValueError(f"Frame must be between 1 and {len(dataset.images)}")
    return dataset.images[frame_index - 1]


def _jpeg(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.convert("RGB").save(output, format="JPEG", quality=90, optimize=True)
    return output.getvalue()


def _header(image: Image.Image, text: str) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rectangle((0, 0, min(image.width, 520), 34), fill=(12, 26, 23, 190))
    draw.text((12, 10), text, fill=(255, 255, 255, 255))


def render_segmentation(dataset: Dataset, frame_index: int) -> bytes:
    image_path = _frame(dataset, frame_index)
    image = Image.open(image_path).convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    label_path = dataset.labels_dir / f"{image_path.stem}.txt"
    detections = 0
    if label_path.exists():
        for line in label_path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) < 7:
                continue
            class_id = int(parts[0])
            coords = [float(value) for value in parts[1:]]
            if len(coords) % 2:
                continue
            points = [
                (coords[index] * image.width, coords[index + 1] * image.height)
                for index in range(0, len(coords), 2)
            ]
            color = COLORS[class_id % len(COLORS)]
            draw.polygon(points, fill=(*color, 52), outline=(*color, 235), width=2)
            detections += 1
    composed = Image.alpha_composite(image, overlay).convert("RGB")
    _header(composed, f"Frame {frame_index}/{len(dataset.images)}  |  {detections} segments")
    image.close()
    return _jpeg(composed)


def _tracking_records(path: Path, content: bytes | None = None) -> list[dict[str, object]]:
    if content is None:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    else:
        rows = list(csv.DictReader(io.StringIO(content.decode("utf-8"), newline="")))
    records: list[dict[str, object]] = []
    for row in rows:
        records.append({
            "track_id": int(row["track_id"]),
            "timeframe": int(row["timeframe"]),
            "x": float(row["x"]),
            "y": float(row["y"]),
            "polygon": ast.literal_eval(row["polygon"]) if row.get("polygon") else None,
        })
    return records


def _render_tracking(
    dataset: Dataset,
    frame_index: int,
    tracking_records: list[dict[str, object]],
) -> bytes:
    image_path = _frame(dataset, frame_index)
    image = Image.open(image_path).convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    grouped: dict[int, list[dict[str, object]]] = {}
    for record in tracking_records:
        if int(record["timeframe"]) <= frame_index:
            grouped.setdefault(int(record["track_id"]), []).append(record)

    active = 0
    for track_id, records in grouped.items():
        ordered = sorted(records, key=lambda item: int(item["timeframe"]))
        points = [(float(item["x"]), float(item["y"])) for item in ordered]
        color = COLORS[(track_id - 1) % len(COLORS)]
        if len(points) > 1:
            draw.line(points, fill=(*color, 230), width=3)
        if points:
            x, y = points[-1]
            radius = 5
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(*color, 255), outline=(255, 255, 255, 255), width=1)
        current = next((item for item in reversed(ordered) if int(item["timeframe"]) == frame_index), None)
        if current is not None:
            active += 1
            polygon = current.get("polygon")
            if polygon:
                draw.polygon(polygon, fill=(*color, 38), outline=(*color, 220), width=2)

    composed = Image.alpha_composite(image, overlay).convert("RGB")
    _header(composed, f"Frame {frame_index}/{len(dataset.images)}  |  {len(grouped)} tracks  |  {active} active")
    image.close()
    return _jpeg(composed)


def render_tracking(dataset: Dataset, frame_index: int) -> bytes:
    return _render_tracking(dataset, frame_index, _tracking_records(dataset.tracking_csv))


def iter_rendered_frames(
    dataset: Dataset,
    kind: str,
    tracking_csv: bytes | None = None,
) -> Iterator[tuple[str, bytes]]:
    if kind not in {"segmentation", "tracking"}:
        raise ValueError(f"Unknown result type: {kind}")
    tracking_records = _tracking_records(dataset.tracking_csv, tracking_csv) if kind == "tracking" else None
    for frame_index, image_path in enumerate(dataset.images, start=1):
        if kind == "segmentation":
            content = render_segmentation(dataset, frame_index)
        else:
            content = _render_tracking(dataset, frame_index, tracking_records or [])
        yield f"{frame_index:04d}_{image_path.stem}_{kind}.jpg", content
