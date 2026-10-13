#!/usr/bin/env python3
"""Shared data preparation for grouped cell-tracking analysis."""

from __future__ import annotations

import ast
import csv
from dataclasses import dataclass
import io
import math
from pathlib import Path
import statistics
from typing import Iterable

import numpy as np
from scipy import stats


from celltrack.analysis.models import AnalysisParameters, DEFAULT_ANALYSIS_PARAMETERS, SUMMARY_METRICS


@dataclass(frozen=True)
class RawTrack:
    dataset: str
    track_id: int
    timeframe: tuple[int, ...]
    x: tuple[float, ...]
    y: tuple[float, ...]
    step_distance: tuple[float, ...]
    instantaneous_speed: tuple[float, ...]
    net_displacement: tuple[float, ...]
    directionality: tuple[float, ...]
    turning_angle: tuple[float, ...]
    area: tuple[float, ...]
    perimeter: tuple[float, ...]
    aspect_ratio: tuple[float, ...]
    roundness: tuple[float, ...]
    shape_change_rate: tuple[float, ...]


@dataclass(frozen=True)
class GroupedTrack:
    group: str
    raw: RawTrack

    @property
    def uid(self) -> str:
        return f"{self.raw.dataset}#{self.raw.track_id}"


@dataclass(frozen=True)
class TrackSummary:
    group: str
    dataset: str
    track_id: int
    observations: int
    first_frame: int
    last_frame: int
    total_path_length: float
    mean_speed: float
    net_displacement: float
    directionality: float
    mean_turning_angle: float
    turning_angle_std: float
    mean_area: float
    mean_perimeter: float
    mean_aspect_ratio: float
    mean_roundness: float
    mean_shape_change_rate: float
    trajectory_type: str
    final_angle: float


@dataclass(frozen=True)
class MsdSeries:
    group: str
    dataset: str
    track_id: int
    lag_frames: tuple[float, ...]
    values: tuple[float, ...]
    alpha: float | None


@dataclass(frozen=True)
class MsdSummaryPoint:
    lag: float
    mean: float
    ci_low: float | None
    ci_high: float | None
    n: int


@dataclass(frozen=True)
class TurningAngleDistributionPoint:
    angle_degrees: float
    mean_density: float
    ci_low: float | None
    ci_high: float | None
    n: int


@dataclass(frozen=True)
class StatisticalResult:
    metric: str
    test: str
    unit: str
    group_1: str
    group_2: str
    n_1: int
    n_2: int
    statistic: float | None
    p_value: float | None
    p_adjusted: float | None
    effect_size: float | None
    effect_size_type: str


@dataclass(frozen=True)
class AnalysisBundle:
    groups: tuple[str, ...]
    tracks: tuple[GroupedTrack, ...]
    summaries: tuple[TrackSummary, ...]
    msd: tuple[MsdSeries, ...]
    source_files_read: int
    parameters: AnalysisParameters

    def tracks_for(self, group: str, prefer_long: bool = True) -> tuple[GroupedTrack, ...]:
        tracks = tuple(track for track in self.tracks if track.group == group)
        if not prefer_long:
            return tracks
        long_tracks = tuple(track for track in tracks if len(track.raw.timeframe) >= self.parameters.long_track_min_observations)
        return long_tracks or (tracks if self.parameters.fallback_to_all_tracks else ())

    def summaries_for(self, group: str, prefer_long: bool = True) -> tuple[TrackSummary, ...]:
        summaries = tuple(summary for summary in self.summaries if summary.group == group)
        if not prefer_long:
            return summaries
        long_summaries = tuple(summary for summary in summaries if summary.observations >= self.parameters.long_track_min_observations)
        return long_summaries or (summaries if self.parameters.fallback_to_all_tracks else ())

    def msd_for(self, group: str, prefer_long: bool = True) -> tuple[MsdSeries, ...]:
        allowed = {(track.raw.dataset, track.raw.track_id) for track in self.tracks_for(group, prefer_long)}
        return tuple(series for series in self.msd if series.group == group and (series.dataset, series.track_id) in allowed)


def _finite_mean(values: Iterable[float], default: float = math.nan) -> float:
    cleaned = [float(value) for value in values if math.isfinite(float(value))]
    return statistics.fmean(cleaned) if cleaned else default


def _polygon_metrics(value: str | None) -> tuple[float, float, float, float]:
    if not value:
        return math.nan, math.nan, math.nan, math.nan
    try:
        polygon = ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return math.nan, math.nan, math.nan, math.nan
    if not polygon or len(polygon) < 3:
        return math.nan, math.nan, math.nan, math.nan
    points = [(float(point[0]), float(point[1])) for point in polygon]
    area = abs(sum(
        points[index][0] * points[(index + 1) % len(points)][1]
        - points[(index + 1) % len(points)][0] * points[index][1]
        for index in range(len(points))
    )) * 0.5
    perimeter = sum(math.dist(points[index], points[(index + 1) % len(points)]) for index in range(len(points)))
    width = max(point[0] for point in points) - min(point[0] for point in points)
    height = max(point[1] for point in points) - min(point[1] for point in points)
    minor = min(width, height)
    aspect_ratio = max(width, height) / minor if minor > 0 else math.nan
    roundness = 4 * math.pi * area / (perimeter * perimeter) if perimeter > 0 else math.nan
    return area, perimeter, aspect_ratio, roundness


def _load_raw_tracks(csv_path: Path, dataset: str) -> tuple[RawTrack, ...]:
    grouped: dict[int, list[dict[str, str]]] = {}
    with csv_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                grouped.setdefault(int(row["track_id"]), []).append(row)
            except (KeyError, TypeError, ValueError):
                continue

    result: list[RawTrack] = []
    for track_id, track_rows in grouped.items():
        ordered = sorted(track_rows, key=lambda row: int(row["timeframe"]))
        timeframe = np.asarray([int(row["timeframe"]) for row in ordered], dtype=float)
        x = np.asarray([float(row["x"]) for row in ordered], dtype=float)
        y = np.asarray([float(row["y"]) for row in ordered], dtype=float)
        if not len(timeframe):
            continue

        step = np.zeros(len(x), dtype=float)
        speed = np.zeros(len(x), dtype=float)
        if len(x) > 1:
            step[1:] = np.hypot(np.diff(x), np.diff(y))
            frame_delta = np.maximum(1.0, np.diff(timeframe))
            speed[1:] = step[1:] / frame_delta
        path = np.cumsum(step)
        net = np.hypot(x - x[0], y - y[0])
        directionality = np.divide(net, path, out=np.full_like(net, np.nan), where=path > 0)
        directionality = np.clip(directionality, 0.0, 1.0)

        turning = np.full(len(x), np.nan, dtype=float)
        for index in range(2, len(x)):
            previous = np.asarray((x[index - 1] - x[index - 2], y[index - 1] - y[index - 2]))
            current = np.asarray((x[index] - x[index - 1], y[index] - y[index - 1]))
            denominator = np.linalg.norm(previous) * np.linalg.norm(current)
            if denominator > 0:
                cosine = float(np.clip(np.dot(previous, current) / denominator, -1.0, 1.0))
                turning[index] = math.degrees(math.acos(cosine))

        polygon_metrics = [_polygon_metrics(row.get("polygon")) for row in ordered]
        area = np.asarray([item[0] for item in polygon_metrics], dtype=float)
        perimeter = np.asarray([item[1] for item in polygon_metrics], dtype=float)
        aspect_ratio = np.asarray([item[2] for item in polygon_metrics], dtype=float)
        roundness = np.asarray([item[3] for item in polygon_metrics], dtype=float)
        shape_change = np.full(len(area), np.nan, dtype=float)
        for index in range(1, len(area)):
            if math.isfinite(area[index - 1]) and math.isfinite(area[index]) and area[index - 1] > 0 and area[index] > 0:
                shape_change[index] = abs(math.log(area[index] / area[index - 1]))

        result.append(RawTrack(
            dataset=dataset,
            track_id=track_id,
            timeframe=tuple(int(value) for value in timeframe),
            x=tuple(float(value) for value in x),
            y=tuple(float(value) for value in y),
            step_distance=tuple(float(value) for value in step),
            instantaneous_speed=tuple(float(value) for value in speed),
            net_displacement=tuple(float(value) for value in net),
            directionality=tuple(float(value) for value in directionality),
            turning_angle=tuple(float(value) for value in turning),
            area=tuple(float(value) for value in area),
            perimeter=tuple(float(value) for value in perimeter),
            aspect_ratio=tuple(float(value) for value in aspect_ratio),
            roundness=tuple(float(value) for value in roundness),
            shape_change_rate=tuple(float(value) for value in shape_change),
        ))
    return tuple(result)


def _classify(directionality: float, parameters: AnalysisParameters) -> str:
    if directionality < parameters.random_directionality_max:
        return "Random"
    if directionality <= parameters.directed_directionality_min:
        return "Mixed"
    return "Directed"


def _summarize(track: GroupedTrack, parameters: AnalysisParameters) -> TrackSummary:
    raw = track.raw
    frame_span = max(1, raw.timeframe[-1] - raw.timeframe[0])
    total_path = sum(raw.step_distance)
    net = raw.net_displacement[-1]
    directionality = float(np.clip(net / total_path, 0.0, 1.0)) if total_path else 0.0
    turning = [value for value in raw.turning_angle if math.isfinite(value)]
    final_angle = math.atan2(raw.y[-1] - raw.y[0], raw.x[-1] - raw.x[0]) if net > 0 else math.nan
    return TrackSummary(
        group=track.group,
        dataset=raw.dataset,
        track_id=raw.track_id,
        observations=len(raw.timeframe),
        first_frame=raw.timeframe[0],
        last_frame=raw.timeframe[-1],
        total_path_length=total_path,
        mean_speed=total_path / frame_span,
        net_displacement=net,
        directionality=directionality,
        mean_turning_angle=_finite_mean(turning, 0.0),
        turning_angle_std=statistics.pstdev(turning) if len(turning) > 1 else 0.0,
        mean_area=_finite_mean(raw.area),
        mean_perimeter=_finite_mean(raw.perimeter),
        mean_aspect_ratio=_finite_mean(raw.aspect_ratio),
        mean_roundness=_finite_mean(raw.roundness),
        mean_shape_change_rate=_finite_mean(raw.shape_change_rate),
        trajectory_type=_classify(directionality, parameters),
        final_angle=final_angle,
    )


def _compute_msd(track: GroupedTrack, parameters: AnalysisParameters) -> MsdSeries | None:
    raw = track.raw
    x = np.asarray(raw.x, dtype=float)
    y = np.asarray(raw.y, dtype=float)
    timeframe = np.asarray(raw.timeframe, dtype=float)
    if len(x) < 3:
        return None
    lags: list[float] = []
    values: list[float] = []
    for lag in range(1, min(len(x) // 2, parameters.msd_max_lag) + 1):
        delta_x = x[lag:] - x[:-lag]
        delta_y = y[lag:] - y[:-lag]
        lags.append(float(np.median(timeframe[lag:] - timeframe[:-lag])))
        values.append(float(np.mean(delta_x * delta_x + delta_y * delta_y)))
    positive = [(lag, value) for lag, value in zip(lags, values) if lag > 0 and value > 0][:parameters.msd_fit_points]
    alpha: float | None = None
    if len(positive) >= parameters.msd_min_fit_points:
        log_lag = np.log([item[0] for item in positive])
        log_value = np.log([item[1] for item in positive])
        alpha = float(np.polyfit(log_lag, log_value, 1)[0])
    return MsdSeries(track.group, raw.dataset, raw.track_id, tuple(lags), tuple(values), alpha)


def prepare_analysis(
    groups: list[tuple[str, list[tuple[str, Path]]]],
    parameters: AnalysisParameters | None = None,
) -> AnalysisBundle:
    parameters = parameters or DEFAULT_ANALYSIS_PARAMETERS
    cache: dict[Path, tuple[RawTrack, ...]] = {}
    grouped_tracks: list[GroupedTrack] = []
    for group_name, sources in groups:
        for dataset_name, csv_path in sources:
            resolved = csv_path.resolve()
            if resolved not in cache:
                cache[resolved] = _load_raw_tracks(resolved, dataset_name)
            grouped_tracks.extend(GroupedTrack(group_name, raw) for raw in cache[resolved])
    summaries = tuple(_summarize(track, parameters) for track in grouped_tracks)
    msd = tuple(series for track in grouped_tracks if (series := _compute_msd(track, parameters)) is not None)
    return AnalysisBundle(
        groups=tuple(name for name, _sources in groups),
        tracks=tuple(grouped_tracks),
        summaries=summaries,
        msd=msd,
        source_files_read=len(cache),
        parameters=parameters,
    )


def analysis_to_csv(bundle: AnalysisBundle) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([field for field in TrackSummary.__dataclass_fields__])
    for summary in bundle.summaries:
        writer.writerow([getattr(summary, field) for field in TrackSummary.__dataclass_fields__])
    return buffer.getvalue().encode("utf-8-sig")


def msd_summary(
    bundle: AnalysisBundle,
    group: str,
    prefer_long: bool = True,
    filter_sparse_tail: bool = True,
) -> tuple[MsdSummaryPoint, ...]:
    """Aggregate track MSD within datasets, then calculate a group-level 95% CI."""
    series_list = bundle.msd_for(group, prefer_long)
    datasets = {series.dataset for series in series_list}
    by_dataset_lag: dict[tuple[str, float], list[float]] = {}
    for series in series_list:
        for lag, value in zip(series.lag_frames, series.values):
            if math.isfinite(lag) and math.isfinite(value) and value >= 0:
                by_dataset_lag.setdefault((series.dataset, float(lag)), []).append(float(value))
    by_lag: dict[float, list[float]] = {}
    for (_dataset, lag), values in by_dataset_lag.items():
        by_lag.setdefault(lag, []).append(statistics.fmean(values))
    minimum = max(1, math.ceil(0.5 * len(datasets))) if filter_sparse_tail else 1
    time_scale = bundle.parameters.frame_interval_minutes or 1.0
    distance_scale = (bundle.parameters.microns_per_pixel or 1.0) ** 2
    points: list[MsdSummaryPoint] = []
    for lag in sorted(by_lag):
        values = by_lag[lag]
        if len(values) < minimum:
            continue
        mean = statistics.fmean(values)
        low: float | None = None
        high: float | None = None
        if len(values) > 1:
            sem = statistics.stdev(values) / math.sqrt(len(values))
            margin = float(stats.t.ppf(0.975, len(values) - 1)) * sem
            low, high = mean - margin, mean + margin
        points.append(MsdSummaryPoint(
            lag=lag * time_scale,
            mean=mean * distance_scale,
            ci_low=None if low is None else low * distance_scale,
            ci_high=None if high is None else high * distance_scale,
            n=len(values),
        ))
    return tuple(points)


def dataset_metric_values(
    bundle: AnalysisBundle,
    group: str,
    metric: str,
    prefer_long: bool = True,
) -> tuple[tuple[str, float], ...]:
    """Return one equally weighted track mean per dataset."""
    by_dataset: dict[str, list[float]] = {}
    for item in bundle.summaries_for(group, prefer_long):
        value = float(getattr(item, metric))
        if math.isfinite(value):
            by_dataset.setdefault(item.dataset, []).append(value)
    return tuple(
        (dataset, statistics.fmean(values))
        for dataset, values in sorted(by_dataset.items())
        if values
    )


def turning_angle_distribution(
    bundle: AnalysisBundle,
    group: str,
    prefer_long: bool = True,
) -> tuple[TurningAngleDistributionPoint, ...]:
    """Return an equally weighted dataset summary over 18 bins from 0 to 180 degrees."""
    edges = np.linspace(0.0, 180.0, 19)
    histograms: list[np.ndarray] = []
    by_dataset: dict[str, list[float]] = {}
    for track in bundle.tracks_for(group, prefer_long):
        by_dataset.setdefault(track.raw.dataset, []).extend(
            value for value in track.raw.turning_angle if math.isfinite(value) and 0.0 <= value <= 180.0
        )
    for values in by_dataset.values():
        if values:
            histogram, _ = np.histogram(values, bins=edges, density=True)
            histograms.append(histogram.astype(float))
    if not histograms:
        return ()

    matrix = np.vstack(histograms)
    means = np.mean(matrix, axis=0)
    result: list[TurningAngleDistributionPoint] = []
    for index, mean in enumerate(means):
        low: float | None = None
        high: float | None = None
        if len(histograms) > 1:
            sem = float(np.std(matrix[:, index], ddof=1) / math.sqrt(len(histograms)))
            margin = float(stats.t.ppf(0.975, len(histograms) - 1)) * sem
            low, high = float(mean) - margin, float(mean) + margin
        result.append(TurningAngleDistributionPoint(
            angle_degrees=float((edges[index] + edges[index + 1]) / 2),
            mean_density=float(mean),
            ci_low=low,
            ci_high=high,
            n=len(histograms),
        ))
    return tuple(result)


def _holm_adjust(p_values: list[float]) -> list[float]:
    count = len(p_values)
    adjusted = [math.nan] * count
    running = 0.0
    for rank, index in enumerate(sorted(range(count), key=p_values.__getitem__)):
        running = max(running, (count - rank) * p_values[index])
        adjusted[index] = min(1.0, running)
    return adjusted


def statistical_results(
    bundle: AnalysisBundle,
    metrics: list[str] | tuple[str, ...] | None = None,
) -> tuple[StatisticalResult, ...]:
    """Calculate the non-parametric tests used by figures and CSV exports."""
    results: list[StatisticalResult] = []
    selected_metrics = tuple(metrics) if metrics is not None else tuple(bundle.parameters.summary_metrics)
    unknown = set(selected_metrics) - set(SUMMARY_METRICS)
    if unknown:
        raise ValueError(f"Unknown statistical metrics: {', '.join(sorted(unknown))}")
    for metric in selected_metrics:
        values = {
            group: [value for _dataset, value in dataset_metric_values(bundle, group, metric)]
            for group in bundle.groups
        }
        if len(bundle.groups) == 2:
            group_1, group_2 = bundle.groups
            first, second = values[group_1], values[group_2]
            if len(first) < 2 or len(second) < 2:
                results.append(StatisticalResult(
                    metric, "Mann-Whitney U", "dataset_mean", group_1, group_2, len(first), len(second),
                    None, None, None, None, "rank-biserial correlation",
                ))
                continue
            statistic, p_value = stats.mannwhitneyu(first, second, alternative="two-sided")
            effect = 2 * float(statistic) / (len(first) * len(second)) - 1
            results.append(StatisticalResult(
                metric, "Mann-Whitney U", "dataset_mean", group_1, group_2, len(first), len(second),
                float(statistic), float(p_value), float(p_value), effect, "rank-biserial correlation",
            ))
            continue

        all_sufficient = all(len(values[group]) >= 2 for group in bundle.groups)
        total_n = sum(len(values[group]) for group in bundle.groups)
        if all_sufficient:
            try:
                statistic, p_value = stats.kruskal(*(values[group] for group in bundle.groups))
            except ValueError:
                statistic, p_value = 0.0, 1.0
            denominator = total_n - len(bundle.groups)
            epsilon_squared = max(0.0, (float(statistic) - len(bundle.groups) + 1) / denominator) if denominator > 0 else math.nan
            results.append(StatisticalResult(
                metric, "Kruskal-Wallis", "dataset_mean", "|".join(bundle.groups), "", total_n, 0,
                float(statistic), float(p_value), float(p_value), epsilon_squared, "epsilon-squared",
            ))
        else:
            results.append(StatisticalResult(
                metric, "Kruskal-Wallis", "dataset_mean", "|".join(bundle.groups), "", total_n, 0,
                None, None, None, None, "epsilon-squared",
            ))

        pair_rows: list[StatisticalResult] = []
        valid_p_values: list[float] = []
        valid_indexes: list[int] = []
        for first_index, group_1 in enumerate(bundle.groups):
            for group_2 in bundle.groups[first_index + 1:]:
                first, second = values[group_1], values[group_2]
                if len(first) < 2 or len(second) < 2:
                    pair_rows.append(StatisticalResult(
                        metric, "Mann-Whitney U", "dataset_mean", group_1, group_2, len(first), len(second),
                        None, None, None, None, "rank-biserial correlation",
                    ))
                    continue
                statistic, p_value = stats.mannwhitneyu(first, second, alternative="two-sided")
                effect = 2 * float(statistic) / (len(first) * len(second)) - 1
                valid_indexes.append(len(pair_rows))
                valid_p_values.append(float(p_value))
                pair_rows.append(StatisticalResult(
                    metric, "Mann-Whitney U", "dataset_mean", group_1, group_2, len(first), len(second),
                    float(statistic), float(p_value), None, effect, "rank-biserial correlation",
                ))
        for row_index, adjusted in zip(valid_indexes, _holm_adjust(valid_p_values)):
            row = pair_rows[row_index]
            pair_rows[row_index] = StatisticalResult(
                row.metric, row.test, row.unit, row.group_1, row.group_2, row.n_1, row.n_2,
                row.statistic, row.p_value, adjusted, row.effect_size, row.effect_size_type,
            )
        results.extend(pair_rows)
    return tuple(results)


def statistics_to_csv(bundle: AnalysisBundle) -> bytes:
    fields = tuple(StatisticalResult.__dataclass_fields__)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(fields)
    metrics = list(bundle.parameters.summary_metrics)
    if "turning_angle_std" not in metrics:
        metrics.append("turning_angle_std")
    if "classification" in bundle.parameters.figure_types:
        if "directionality" not in metrics:
            metrics.append("directionality")
    for result in statistical_results(bundle, metrics):
        writer.writerow([getattr(result, field) for field in fields])
    return buffer.getvalue().encode("utf-8-sig")
