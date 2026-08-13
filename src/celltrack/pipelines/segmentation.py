#!/usr/bin/env python3
"""Run YOLO instance-segmentation inference on an image or image directory."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


from celltrack.settings import IMAGE_SUFFIXES, SEGMENTATION_CONFIG, settings


def natural_sort_key(path: Path) -> list[object]:
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"([0-9]+)", str(path))
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Use yolo11x-seg.pt to segment every image in a target folder."
    )
    parser.add_argument(
        "source",
        type=Path,
        help="Input image or directory. Directories are searched recursively.",
    )
    parser.add_argument(
        "--weights",
        type=Path,
        default=settings.weights_path,
        help=f"Model weights (default: {settings.weights_path}).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Output directory.",
    )
    parser.add_argument("--conf", type=float, default=SEGMENTATION_CONFIG["conf"], help="Confidence threshold.")
    parser.add_argument("--iou", type=float, default=SEGMENTATION_CONFIG["iou"], help="NMS IoU threshold.")
    parser.add_argument("--imgsz", type=int, default=SEGMENTATION_CONFIG["imgsz"], help="Inference image size.")
    parser.add_argument(
        "--device",
        default=None,
        help="Inference device, for example cpu, 0, or mps (default: auto).",
    )
    parser.add_argument(
        "--no-annotated",
        action="store_true",
        help="Save labels only and skip annotated image copies.",
    )
    return parser.parse_args(argv)


def find_images(source: Path) -> tuple[list[Path], Path]:
    source = source.expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Input path does not exist: {source}")

    if source.is_file():
        if source.suffix.lower() not in IMAGE_SUFFIXES:
            raise ValueError(f"Unsupported image format: {source.suffix}")
        return [source], source.parent

    images = sorted(
        (
            path
            for path in source.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        ),
        key=natural_sort_key,
    )
    if not images:
        raise FileNotFoundError(f"No supported images found under: {source}")
    return images, source


def validate_threshold(name: str, value: float) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1, got {value}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    validate_threshold("conf", args.conf)
    validate_threshold("iou", args.iou)

    weights = args.weights.expanduser().resolve()
    if not weights.is_file():
        raise FileNotFoundError(f"Weights file does not exist: {weights}")

    images, source_root = find_images(args.source)
    output_dir = args.output_dir.expanduser().resolve()
    annotated_dir = output_dir / "images"
    labels_dir = output_dir / "labels"

    try:
        from ultralytics import YOLO
    except ImportError:
        print(
            "Missing dependency 'ultralytics'. Install it with: "
            "python3 -m pip install ultralytics",
            file=sys.stderr,
        )
        return 2

    print(f"Weights: {weights}")
    print(f"Images:  {len(images)}")
    print(f"Output:  {output_dir}")
    print(f"conf={args.conf}, iou={args.iou}, imgsz={args.imgsz}")

    model = YOLO(str(weights))
    predict_kwargs = {
        "conf": args.conf,
        "iou": args.iou,
        "imgsz": args.imgsz,
        "verbose": False,
    }
    if args.device is not None:
        predict_kwargs["device"] = args.device

    total_instances = 0
    processed = 0
    # Submit one frame at a time so each label is written immediately and the
    # web UI can report real per-image progress without a large initial batch.
    for image_path in images:
        result = model.predict(source=str(image_path), **predict_kwargs)[0]
        relative_path = image_path.relative_to(source_root)
        annotated_path = annotated_dir / relative_path
        label_path = (labels_dir / relative_path).with_suffix(".txt")
        label_path.parent.mkdir(parents=True, exist_ok=True)

        if not args.no_annotated:
            annotated_path.parent.mkdir(parents=True, exist_ok=True)
            result.save(filename=str(annotated_path))
        # Create an empty label file as well, so every input frame has a match.
        label_path.touch()
        result.save_txt(str(label_path), save_conf=False)

        instances = len(result.boxes) if result.boxes is not None else 0
        total_instances += instances
        processed += 1
        print(f"[{processed}/{len(images)}] {relative_path}: {instances} instances")

    print(f"Done: {processed} images, {total_instances} instances")
    if not args.no_annotated:
        print(f"Annotated images: {annotated_dir}")
    print(f"YOLO labels:      {labels_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
