from __future__ import annotations

from argparse import ArgumentParser
import csv
from dataclasses import dataclass, field
import json
from pathlib import Path
import math
import re

from PIL import Image

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}


@dataclass(frozen=True)
class FrameDetection:
    timeframe: int
    detection_id: int
    centroid_x: float
    centroid_y: float
    polygon: list[float] | list[tuple[float, float]] | None = None
    bbox: tuple[float, float, float, float] | None = None
    source_file: str | None = None

    @classmethod
    def from_centroid(
        cls,
        timeframe: int,
        detection_id: int,
        centroid_x: float,
        centroid_y: float,
    ) -> "FrameDetection":
        return cls(
            timeframe=timeframe,
            detection_id=detection_id,
            centroid_x=centroid_x,
            centroid_y=centroid_y,
        )

    @property
    def point(self) -> tuple[float, float]:
        return self.centroid_x, self.centroid_y


@dataclass(frozen=True)
class TrackObservation:
    track_id: int
    timeframe: int
    centroid_x: float
    centroid_y: float
    polygon: list[float] | list[tuple[float, float]] | None = None
    bbox: tuple[float, float, float, float] | None = None
    detection_id: int | None = None
    is_interpolated: bool = False


@dataclass(frozen=True)
class TrackingConfig:
    distance_schedule: list[int]
    lock_distance_schedule: set[int]
    max_lookback: int = 5
    min_track_length: int = 3


@dataclass
class TrackState:
    track_id: int
    observations: list[TrackObservation] = field(default_factory=list)
    last_frame_index: int = 0
    is_finished: bool = False

    @property
    def last_observation(self) -> TrackObservation:
        return self.observations[-1]


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(prog="celltrack track")
    parser.add_argument("--data-path", required=True)
    parser.add_argument("--labels-dir", help="YOLO label directory stored separately from images.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--distance-schedule", nargs="+", type=int, default=[5, 8, 12, 18, 25])
    parser.add_argument("--max-lookback", type=int, default=5)
    parser.add_argument("--min-track-length", type=int, default=3)
    return parser


def natural_sort_key(value: str) -> list[object]:
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"([0-9]+)", value)
    ]


def build_frame_index(data_dir: Path) -> dict[str, int]:
    image_stems = sorted(
        {
            path.stem
            for path in data_dir.iterdir()
            if path.suffix.lower() in IMAGE_SUFFIXES
        },
        key=natural_sort_key,
    )

    if image_stems:
        stems = image_stems
    else:
        stems = sorted(
            {
                path.stem
                for path in data_dir.iterdir()
                if path.suffix.lower() in {".txt", ".json"}
            },
            key=natural_sort_key,
        )

    return {stem: index for index, stem in enumerate(stems, start=1)}


def parse_yolo_segmentation_line(
    line: str,
) -> tuple[int | None, list[float] | None]:
    parts = line.strip().split()
    if len(parts) < 3:
        return None, None
    return int(parts[0]), [float(value) for value in parts[1:]]


def calculate_polygon_centroid(
    points: list[float] | list[tuple[float, float]],
) -> tuple[float, float]:
    if not points:
        return 0.0, 0.0

    if isinstance(points[0], (int, float)):
        vertices = [
            (float(points[index]), float(points[index + 1]))
            for index in range(0, len(points), 2)
        ]
    else:
        vertices = [(float(x), float(y)) for x, y in points]

    area = 0.0
    centroid_x = 0.0
    centroid_y = 0.0
    count = len(vertices)

    for index in range(count):
        next_index = (index + 1) % count
        x1, y1 = vertices[index]
        x2, y2 = vertices[next_index]
        cross = x1 * y2 - x2 * y1
        area += cross
        centroid_x += (x1 + x2) * cross
        centroid_y += (y1 + y2) * cross

    if area == 0:
        return (
            sum(x for x, _ in vertices) / count,
            sum(y for _, y in vertices) / count,
        )

    area *= 0.5
    return centroid_x / (6 * area), centroid_y / (6 * area)


def euclidean_distance(
    point_a: tuple[float, float],
    point_b: tuple[float, float],
) -> float:
    return math.dist(point_a, point_b)


def _nearest_neighbor_map(
    sources: dict[int, tuple[float, float]],
    targets: dict[int, tuple[float, float]],
) -> dict[int, int]:
    nearest: dict[int, int] = {}

    for source_id, source_point in sources.items():
        ranked = sorted(
            (
                (euclidean_distance(source_point, target_point), target_id)
                for target_id, target_point in targets.items()
            ),
            key=lambda item: (item[0], item[1]),
        )
        if not ranked:
            continue
        if len(ranked) > 1 and math.isclose(ranked[0][0], ranked[1][0]):
            continue
        nearest[source_id] = ranked[0][1]

    return nearest


def find_mutual_nearest_pairs(
    track_points: dict[int, tuple[float, float]],
    detection_points: dict[int, tuple[float, float]],
) -> set[tuple[int, int]]:
    if not track_points or not detection_points:
        return set()

    nearest_detection = _nearest_neighbor_map(track_points, detection_points)
    nearest_track = _nearest_neighbor_map(detection_points, track_points)

    return {
        (track_id, detection_id)
        for track_id, detection_id in nearest_detection.items()
        if nearest_track.get(detection_id) == track_id
    }


def polygon_to_bbox(
    polygon: list[tuple[float, float]],
) -> tuple[float, float, float, float]:
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def _normalized_polygon_to_pixels(
    coords: list[float],
    image_width: float,
    image_height: float,
) -> list[tuple[float, float]]:
    return [
        (coords[index] * image_width, coords[index + 1] * image_height)
        for index in range(0, len(coords), 2)
    ]


def _read_image_dimensions(data_dir: Path, stem: str) -> tuple[float, float, dict[str, object]]:
    json_path = data_dir / f"{stem}.json"
    if json_path.exists():
        payload = json.loads(json_path.read_text())
        width = float(payload.get("imageWidth", 1.0))
        height = float(payload.get("imageHeight", 1.0))
        return width, height, payload
    for suffix in IMAGE_SUFFIXES:
        image_path = data_dir / f"{stem}{suffix}"
        if image_path.exists():
            with Image.open(image_path) as image:
                width, height = image.size
            return float(width), float(height), {}
    return 1.0, 1.0, {}


def load_dataset(
    data_dir: Path,
    labels_dir: Path | None = None,
) -> dict[int, list[FrameDetection]]:
    frame_index = build_frame_index(data_dir)
    detections_by_frame: dict[int, list[FrameDetection]] = {}

    for stem, timeframe in frame_index.items():
        image_width, image_height, _payload = _read_image_dimensions(data_dir, stem)

        txt_path = (labels_dir or data_dir) / f"{stem}.txt"
        if txt_path.exists():
            detections: list[FrameDetection] = []
            for detection_id, line in enumerate(txt_path.read_text().splitlines(), start=1):
                class_id, coords = parse_yolo_segmentation_line(line)
                if class_id is None or coords is None:
                    continue
                polygon = _normalized_polygon_to_pixels(coords, image_width, image_height)
                centroid_x, centroid_y = calculate_polygon_centroid(polygon)
                detections.append(
                    FrameDetection(
                        timeframe=timeframe,
                        detection_id=detection_id,
                        centroid_x=centroid_x,
                        centroid_y=centroid_y,
                        polygon=polygon,
                        bbox=polygon_to_bbox(polygon),
                        source_file=txt_path.name,
                    )
                )
            detections_by_frame[timeframe] = detections

    return detections_by_frame


def _append_detection_to_track(
    track: TrackState,
    detection: FrameDetection,
    frame_index: int,
) -> None:
    track.observations.append(
        TrackObservation(
            track_id=track.track_id,
            timeframe=detection.timeframe,
            centroid_x=detection.centroid_x,
            centroid_y=detection.centroid_y,
            polygon=detection.polygon,
            bbox=detection.bbox,
            detection_id=detection.detection_id,
            is_interpolated=False,
        )
    )
    track.last_frame_index = frame_index


def _seed_track(
    track_id: int,
    detection: FrameDetection,
    frame_index: int,
) -> TrackState:
    track = TrackState(track_id=track_id, last_frame_index=frame_index)
    _append_detection_to_track(track, detection, frame_index)
    return track


def _build_cost_matrix(
    tracks: list[TrackState],
    detections: list[FrameDetection],
    distance_threshold: float,
) -> tuple[list[list[float]], dict[tuple[int, int], float]]:
    cost_matrix = [
        [1e9 for _ in detections]
        for _ in tracks
    ]
    distances: dict[tuple[int, int], float] = {}

    for track_index, track in enumerate(tracks):
        for detection_index, detection in enumerate(detections):
            distance = euclidean_distance(
                (track.last_observation.centroid_x, track.last_observation.centroid_y),
                detection.point,
            )
            distances[(track_index, detection_index)] = distance
            if distance <= distance_threshold:
                cost_matrix[track_index][detection_index] = distance

    return cost_matrix, distances


def _all_invalid(cost_matrix: list[list[float]]) -> bool:
    return all(
        value >= 1e9
        for row in cost_matrix
        for value in row
    )


def _linear_sum_assignment(
    cost_matrix: list[list[float]],
) -> tuple[list[int], list[int]]:
    try:
        from scipy.optimize import linear_sum_assignment as scipy_linear_sum_assignment
    except ModuleNotFoundError:
        return _linear_sum_assignment_fallback(cost_matrix)

    row_ind, col_ind = scipy_linear_sum_assignment(cost_matrix)
    return row_ind.tolist(), col_ind.tolist()


def _linear_sum_assignment_fallback(
    cost_matrix: list[list[float]],
) -> tuple[list[int], list[int]]:
    num_rows = len(cost_matrix)
    num_cols = len(cost_matrix[0]) if cost_matrix else 0
    if num_rows == 0 or num_cols == 0:
        return [], []

    if num_rows <= num_cols:
        best_rows = list(range(num_rows))
        best_cols: list[int] | None = None
        best_cost = math.inf
        for cols in itertools.permutations(range(num_cols), num_rows):
            total = sum(cost_matrix[row][col] for row, col in enumerate(cols))
            if total < best_cost:
                best_cost = total
                best_cols = list(cols)
        return best_rows, (best_cols or [])

    best_rows: list[int] | None = None
    best_cols = list(range(num_cols))
    best_cost = math.inf
    for rows in itertools.permutations(range(num_rows), num_cols):
        total = sum(cost_matrix[row][col] for col, row in enumerate(rows))
        if total < best_cost:
            best_cost = total
            best_rows = list(rows)
    return (best_rows or []), best_cols


def _finalize_finished_tracks(
    active_tracks: dict[int, TrackState],
    current_frame_index: int,
    max_lookback: int,
    finished_tracks: list[TrackState],
) -> None:
    expired_ids = [
        track_id
        for track_id, track in active_tracks.items()
        if current_frame_index - track.last_frame_index > max_lookback
    ]
    for track_id in expired_ids:
        finished_tracks.append(active_tracks.pop(track_id))


def group_track_rows(
    rows: list[TrackObservation],
) -> dict[int, list[TrackObservation]]:
    grouped: dict[int, list[TrackObservation]] = {}
    for row in rows:
        grouped.setdefault(row.track_id, []).append(row)
    for track_rows in grouped.values():
        track_rows.sort(key=lambda row: row.timeframe)
    return grouped


def tracks_to_records(rows: list[TrackObservation]) -> list[dict[str, object]]:
    records = [
        {
            "track_id": row.track_id,
            "timeframe": row.timeframe,
            "x": row.centroid_x,
            "y": row.centroid_y,
            "polygon": row.polygon,
            "bbox": row.bbox,
            "detection_id": row.detection_id,
            "is_interpolated": row.is_interpolated,
        }
        for row in rows
    ]
    records.sort(key=lambda record: (record["track_id"], record["timeframe"]))
    return records


def run_tracking_from_detections(
    detections_by_frame: dict[int, list[FrameDetection]],
    config: TrackingConfig,
) -> list[TrackObservation]:
    ordered_frames = sorted(detections_by_frame)
    if not ordered_frames:
        return []

    frame_to_index = {
        timeframe: index
        for index, timeframe in enumerate(ordered_frames)
    }
    next_track_id = 1
    active_tracks: dict[int, TrackState] = {}
    finished_tracks: list[TrackState] = []

    first_frame = ordered_frames[0]
    first_frame_index = frame_to_index[first_frame]
    for detection in detections_by_frame[first_frame]:
        active_tracks[next_track_id] = _seed_track(
            next_track_id,
            detection,
            first_frame_index,
        )
        next_track_id += 1

    for timeframe in ordered_frames[1:]:
        frame_index = frame_to_index[timeframe]
        _finalize_finished_tracks(
            active_tracks,
            frame_index,
            config.max_lookback,
            finished_tracks,
        )

        detections = list(detections_by_frame[timeframe])
        assigned_detection_ids: set[int] = set()
        assigned_track_ids: set[int] = set()

        for distance_threshold in config.distance_schedule:
            for lookback in range(1, config.max_lookback + 1):
                candidate_tracks = [
                    track
                    for track in active_tracks.values()
                    if track.track_id not in assigned_track_ids
                    and frame_index - track.last_frame_index == lookback
                ]
                candidate_detections = [
                    detection
                    for detection in detections
                    if detection.detection_id not in assigned_detection_ids
                ]

                if not candidate_tracks or not candidate_detections:
                    continue

                cost_matrix, distances = _build_cost_matrix(
                    candidate_tracks,
                    candidate_detections,
                    distance_threshold,
                )
                if _all_invalid(cost_matrix):
                    continue

                row_ind, col_ind = _linear_sum_assignment(cost_matrix)
                matched_pairs = [
                    (track_index, detection_index)
                    for track_index, detection_index in zip(row_ind, col_ind)
                    if distances[(track_index, detection_index)] <= distance_threshold
                ]
                if not matched_pairs:
                    continue

                if distance_threshold in config.lock_distance_schedule:
                    mutual_pairs = find_mutual_nearest_pairs(
                        {
                            track.track_id: (
                                track.last_observation.centroid_x,
                                track.last_observation.centroid_y,
                            )
                            for track in candidate_tracks
                        },
                        {
                            detection.detection_id: detection.point
                            for detection in candidate_detections
                        },
                    )
                    accepted_pairs = [
                        (track_index, detection_index)
                        for track_index, detection_index in matched_pairs
                        if (
                            candidate_tracks[track_index].track_id,
                            candidate_detections[detection_index].detection_id,
                        ) in mutual_pairs
                    ]
                else:
                    accepted_pairs = matched_pairs

                for track_index, detection_index in accepted_pairs:
                    track = candidate_tracks[track_index]
                    detection = candidate_detections[detection_index]
                    _append_detection_to_track(track, detection, frame_index)
                    assigned_track_ids.add(track.track_id)
                    assigned_detection_ids.add(detection.detection_id)

        for detection in detections:
            if detection.detection_id in assigned_detection_ids:
                continue
            active_tracks[next_track_id] = _seed_track(
                next_track_id,
                detection,
                frame_index,
            )
            next_track_id += 1

    finished_tracks.extend(active_tracks.values())

    rows = [
        observation
        for track in finished_tracks
        if len(track.observations) >= config.min_track_length
        for observation in track.observations
    ]
    rows.sort(key=lambda row: (row.track_id, row.timeframe))
    return rows


def _write_records_csv(
    output_path: Path,
    records: list[dict[str, object]],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(records[0].keys()) if records else []
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if fieldnames:
            writer.writeheader()
            writer.writerows(records)


def tracking_config_from_args(args: object) -> TrackingConfig:
    return TrackingConfig(
        distance_schedule=list(args.distance_schedule),
        lock_distance_schedule={5, 8, 12},
        max_lookback=int(args.max_lookback),
        min_track_length=int(args.min_track_length),
    )


def run_pipeline_command(args: object) -> int:
    data_dir = Path(args.data_path)
    output_dir = Path(args.output_dir)
    labels_dir = Path(args.labels_dir) if getattr(args, "labels_dir", None) else None
    detections_by_frame = load_dataset(data_dir, labels_dir)
    tracking_rows = run_tracking_from_detections(
        detections_by_frame,
        tracking_config_from_args(args),
    )
    tracking_records = tracks_to_records(tracking_rows)
    _write_records_csv(output_dir / "tracking_results.csv", tracking_records)
    print(f"Tracked {len({row.track_id for row in tracking_rows})} cells across {len(detections_by_frame)} frames")
    return 0


def dispatch_cli(args: object) -> int:
    return run_pipeline_command(args)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return dispatch_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
