"""Render the complete md_image_index figure set from one shared bundle."""

from __future__ import annotations

from collections import defaultdict
import io
import math
import re
import statistics

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np


from celltrack.analysis.compute import (
    dataset_metric_values,
    msd_summary,
    statistical_results,
    turning_angle_distribution,
)
from celltrack.analysis.models import FIGURE_TYPES, SUMMARY_METRICS, TEMPORAL_METRICS

COLORS = ("#156F62", "#D97706", "#2563EB", "#B42318", "#7C3AED", "#087EA4")
LINE_STYLES = ("-", "--", "-.", ":", (0, (5, 2, 1, 2)), (0, (3, 1, 1, 1)))
TYPE_COLORS = {"Random": "#B42318", "Mixed": "#D97706", "Directed": "#19704E"}
BACKGROUND = "#FFFFFF"
GRID_COLUMNS = 3

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial Unicode MS", "Noto Sans CJK SC", "PingFang SC", "DejaVu Sans"],
    "axes.titleweight": "normal",
    "axes.labelcolor": "#33423E",
    "text.color": "#17211F",
})


def _png(fig: plt.Figure) -> bytes:
    output = io.BytesIO()
    fig.savefig(output, format="png", dpi=140, bbox_inches="tight", facecolor=BACKGROUND)
    plt.close(fig)
    return output.getvalue()


def _style(ax: plt.Axes) -> None:
    ax.set_facecolor(BACKGROUND)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#C9D4D0")
    ax.grid(axis="y", color="#E4EBE8", linewidth=0.7, alpha=0.9)
    ax.tick_params(labelsize=8, colors="#53635F")


def _figure(rows: int = 1, columns: int = 1, width: float = 5.2, height: float = 3.8):
    """Every figure uses constrained layout so titles, legends and brackets get room."""
    fig, axes = plt.subplots(
        rows, columns,
        figsize=(columns * width, rows * height),
        squeeze=False,
        layout="constrained",
    )
    fig.patch.set_facecolor(BACKGROUND)
    return fig, axes


def _grid_rows(count: int) -> int:
    return max(1, math.ceil(count / GRID_COLUMNS))


def _empty_figure(title: str, message: str) -> bytes:
    fig, axes = _figure(width=9, height=4.5)
    ax = axes[0, 0]
    ax.axis("off")
    ax.text(0.5, 0.58, title, ha="center", va="center", fontsize=17)
    ax.text(0.5, 0.42, message, ha="center", va="center", fontsize=11, color="#61716D")
    return _png(fig)


def _tracks(bundle, prefer_long: bool) -> list:
    result = []
    for group in bundle.groups:
        result.extend(bundle.tracks_for(group, prefer_long))
    return result


def _hide_unused(axes, used: int) -> None:
    for ax in axes.flat[used:]:
        ax.set_visible(False)


def _temporal_values(tracks: list, attribute: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    by_frame: dict[int, list[float]] = defaultdict(list)
    for track in tracks:
        values = getattr(track.raw, attribute)
        for frame, value in zip(track.raw.timeframe, values):
            if math.isfinite(value):
                by_frame[int(frame)].append(float(value))
    frames = np.asarray(sorted(by_frame), dtype=float)
    means = np.asarray([statistics.fmean(by_frame[int(frame)]) for frame in frames], dtype=float)
    sem = np.asarray([
        statistics.stdev(by_frame[int(frame)]) / math.sqrt(len(by_frame[int(frame)]))
        if len(by_frame[int(frame)]) > 1 else 0.0
        for frame in frames
    ], dtype=float)
    return frames, means, sem


def _plot_temporal_axes(bundle, axes, prefer_long: bool, metrics, linewidth: float = 1.4) -> None:
    for ax, (attribute, label) in zip(axes, metrics):
        for index, group in enumerate(bundle.groups):
            frames, mean, sem = _temporal_values(list(bundle.tracks_for(group, prefer_long)), attribute)
            if not len(frames):
                continue
            color = COLORS[index % len(COLORS)]
            ax.plot(frames, mean, color=color, linestyle=LINE_STYLES[index % len(LINE_STYLES)], linewidth=linewidth, label=group)
            ax.fill_between(frames, mean - sem, mean + sem, color=color, alpha=0.12)
        ax.set_title(label, fontsize=9)
        ax.set_xlabel("Frame", fontsize=8)
        _style(ax)


def _plot_temporal(bundle, title: str, prefer_long: bool, metrics) -> bytes:
    rows = _grid_rows(len(metrics))
    fig, axes = _figure(rows, GRID_COLUMNS, width=4.3, height=3.2)
    fig.suptitle(title, fontsize=15)
    _plot_temporal_axes(bundle, axes.flat, prefer_long, metrics, linewidth=1.7)
    _hide_unused(axes, len(metrics))
    handles, labels = axes.flat[0].get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, loc="outside lower center", ncol=min(6, len(labels)), frameon=False, fontsize=9)
    return _png(fig)


def _plot_cell_appearance_axes(bundle, axes) -> None:
    """Cells already present on the first imaged frame are not arrivals.

    Counting them as new tracks turns the first point of "New tracks" into a
    spike the size of the starting population, so that panel starts one frame
    later. The cumulative and active panels keep the first frame.

    A track that starts in the last ``minimum - 1`` frames can never reach
    ``minimum`` observations, so those frames would show arrivals falling to
    zero and active tracks collapsing. That is the end of the movie, not the
    cells, so every panel stops at the last frame where an arrival can still
    be counted.
    """
    minimum = bundle.parameters.appearance_min_observations
    starts = [track.raw.timeframe[0] for track in bundle.tracks if len(track.raw.timeframe)]
    ends = [track.raw.timeframe[-1] for track in bundle.tracks if len(track.raw.timeframe)]
    first_imaged = min(starts) if starts else 0
    last_countable = (max(ends) if ends else 0) - (minimum - 1)
    for index, group in enumerate(bundle.groups):
        tracks = [
            track for track in bundle.tracks_for(group, prefer_long=False)
            if len(track.raw.timeframe) >= minimum
        ]
        first = [track.raw.timeframe[0] for track in tracks]
        last = [track.raw.timeframe[-1] for track in tracks]
        if not first:
            continue
        frames = np.arange(min(first), max(min(first), min(max(last), last_countable)) + 1)
        new = np.asarray([sum(value == frame for value in first) for frame in frames])
        values_set = (np.cumsum(new), np.asarray([sum(start <= frame <= end for start, end in zip(first, last)) for frame in frames]), new)
        arriving = frames > first_imaged
        panels = (
            (frames, values_set[0], "Cumulative appeared"),
            (frames, values_set[1], "Active tracks"),
            (frames[arriving], values_set[2][arriving], "New tracks (after first frame)"),
        )
        for ax, (x_values, values, label) in zip(axes, panels):
            ax.plot(x_values, values, color=COLORS[index % len(COLORS)], linestyle=LINE_STYLES[index % len(LINE_STYLES)], linewidth=1.8, label=group)
            ax.set_title(label, fontsize=11)
            ax.set_xlabel("Frame", fontsize=9)
            ax.set_ylabel("Track count", fontsize=9)
            _style(ax)


def _appearance_subtitle(bundle) -> str:
    return f"tracks with at least {bundle.parameters.appearance_min_observations} observations"


def figure_cell_appearance(bundle) -> bytes:
    fig, axes = _figure(1, 3, width=4.6, height=4)
    fig.suptitle(f"Cell appearance over time\n({_appearance_subtitle(bundle)})", fontsize=14)
    _plot_cell_appearance_axes(bundle, axes[0])
    handles, labels = axes[0, 0].get_legend_handles_labels()
    if handles:
        axes[0, 0].legend(handles, labels, frameon=False, fontsize=8)
    return _png(fig)


def figure_classification(bundle) -> bytes:
    fig, axes = _figure(1, 3, width=4.8, height=4.2)
    fig.suptitle("Trajectory classification", fontsize=14)
    _plot_classification_axes(bundle, axes[0])
    return _png(fig)


def figure_turning_angle_distribution(bundle) -> bytes:
    """Step turning angles, histogrammed per dataset and averaged with a 95% CI."""
    distributions = [turning_angle_distribution(bundle, group, prefer_long=True) for group in bundle.groups]
    if not any(distributions):
        return _empty_figure("Step turning-angle distribution", "No valid turning-angle values are available.")
    fig, axes = _figure(width=8.2, height=4.8)
    ax = axes[0, 0]
    for index, (group, points) in enumerate(zip(bundle.groups, distributions)):
        if not points:
            continue
        color = COLORS[index % len(COLORS)]
        angles = np.asarray([point.angle_degrees for point in points])
        density = np.asarray([point.mean_density for point in points])
        ax.plot(angles, density, color=color, linewidth=2, label=f"{group} (n={points[0].n} datasets)")
        valid_ci = np.asarray([point.ci_low is not None and point.ci_high is not None for point in points])
        if any(valid_ci):
            low = np.asarray([max(0.0, point.ci_low) if point.ci_low is not None else point.mean_density for point in points])
            high = np.asarray([point.ci_high if point.ci_high is not None else point.mean_density for point in points])
            ax.fill_between(angles, low, high, where=valid_ci, color=color, alpha=0.14)
    ax.set_xlim(0, 180)
    ax.set_xticks(np.arange(0, 181, 30))
    ax.set_xlabel("Step turning angle (°)")
    ax.set_ylabel("Density")
    ax.set_title("Step turning-angle distribution (dataset mean ± 95% CI)")
    ax.legend(frameon=False, fontsize=8)
    _style(ax)
    return _png(fig)


def _msd_labels(bundle) -> tuple[str, str]:
    return (
        "Time lag (min)" if bundle.parameters.frame_interval_minutes is not None else "Frame lag",
        "MSD (µm²)" if bundle.parameters.microns_per_pixel is not None else "MSD (px²)",
    )


def _plot_msd(bundle, title: str, prefer_long: bool) -> bytes:
    fig, axes = _figure(1, 3, width=4.8, height=4.2)
    fig.suptitle(title, fontsize=14)
    alpha_values = []
    for index, group in enumerate(bundle.groups):
        series = bundle.msd_for(group, prefer_long)
        points = msd_summary(bundle, group, prefer_long=prefer_long, filter_sparse_tail=True)
        lag = np.asarray([point.lag for point in points])
        mean = np.asarray([point.mean for point in points])
        color = COLORS[index % len(COLORS)]
        if len(lag):
            axes[0, 0].plot(lag, mean, color=color, linestyle=LINE_STYLES[index % len(LINE_STYLES)], label=group)
            axes[0, 1].loglog(lag, mean, color=color, linestyle=LINE_STYLES[index % len(LINE_STYLES)], label=group)
            valid_ci = np.asarray([point.ci_low is not None and point.ci_high is not None for point in points])
            if any(valid_ci):
                low = np.asarray([max(0.0, point.ci_low) if point.ci_low is not None else point.mean for point in points])
                high = np.asarray([point.ci_high if point.ci_high is not None else point.mean for point in points])
                axes[0, 0].fill_between(lag, low, high, where=valid_ci, color=color, alpha=0.14)
        by_dataset: dict[str, list[float]] = defaultdict(list)
        for item in series:
            if item.alpha is not None and math.isfinite(item.alpha):
                by_dataset[item.dataset].append(item.alpha)
        alpha_values.append([statistics.fmean(values) for values in by_dataset.values() if values])
    x_label, y_label = _msd_labels(bundle)
    for ax, subtitle in zip(axes[0, :2], ("Population mean MSD", "Log-log MSD")):
        ax.set_title(subtitle)
        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend(handles, labels, frameon=False, fontsize=8)
        _style(ax)
    positions = [index + 1 for index, values in enumerate(alpha_values) if values]
    nonempty = [values for values in alpha_values if values]
    if nonempty:
        boxplot = axes[0, 2].boxplot(nonempty, positions=positions, patch_artist=True, showfliers=False)
        for position, box in zip(positions, boxplot["boxes"]):
            box.set_facecolor(COLORS[(position - 1) % len(COLORS)])
            box.set_alpha(0.28)
    for index, values in enumerate(alpha_values):
        offsets = np.linspace(-0.1, 0.1, len(values)) if len(values) > 1 else np.asarray([0.0])
        if values:
            axes[0, 2].scatter(index + 1 + offsets, values, s=22, color=COLORS[index % len(COLORS)], zorder=3)
    axes[0, 2].set_xticks(
        np.arange(1, len(bundle.groups) + 1),
        [f"{group}\nn={len(values)}" for group, values in zip(bundle.groups, alpha_values)],
        rotation=20, ha="right",
    )
    axes[0, 2].axhline(1, color="#8B9895", linewidth=1, linestyle="--")
    axes[0, 2].set_title("MSD exponent alpha")
    _style(axes[0, 2])
    return _png(fig)


def figure_msd_summary(bundle) -> bytes:
    fig, axes = _figure(width=8.2, height=4.8)
    ax = axes[0, 0]
    for index, group in enumerate(bundle.groups):
        points = msd_summary(bundle, group, prefer_long=True, filter_sparse_tail=True)
        if not points:
            continue
        lag = np.asarray([point.lag for point in points])
        mean = np.asarray([point.mean for point in points])
        color = COLORS[index % len(COLORS)]
        ax.plot(lag, mean, color=color, linestyle=LINE_STYLES[index % len(LINE_STYLES)], linewidth=2, label=group)
        valid_ci = [point.ci_low is not None and point.ci_high is not None for point in points]
        if any(valid_ci):
            low = np.asarray([max(0.0, point.ci_low) if point.ci_low is not None else point.mean for point in points])
            high = np.asarray([point.ci_high if point.ci_high is not None else point.mean for point in points])
            ax.fill_between(lag, low, high, where=np.asarray(valid_ci), color=color, alpha=0.16)
    x_label, y_label = _msd_labels(bundle)
    ax.set_title("MSD comparison (mean ± 95% CI)")
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(handles, labels, frameon=False, fontsize=9)
    _style(ax)
    return _png(fig)


def _trajectory_limit(tracks: list) -> float:
    maximum = 1.0
    for track in tracks:
        x = np.asarray(track.raw.x) - track.raw.x[0]
        y = np.asarray(track.raw.y) - track.raw.y[0]
        if len(x):
            maximum = max(maximum, float(np.max(np.abs(x))), float(np.max(np.abs(y))))
    return maximum * 1.08


def _bundle_trajectory_limit(bundle) -> float:
    """One spatial scale for every trajectory panel, so panels stay comparable."""
    return _trajectory_limit(_tracks(bundle, True))


def _apply_trajectory_limits(ax: plt.Axes, limit: float) -> None:
    ax.set_xlim(-limit, limit)
    ax.set_ylim(-limit, limit)
    ax.set_aspect("equal")


def _angle_histogram_scale(bundle) -> tuple[np.ndarray, float]:
    edges = np.linspace(-math.pi, math.pi, bundle.parameters.angle_bins + 1)
    maximum = 1.0
    for group in bundle.groups:
        percentages = _angle_histogram_percentages(bundle, group, edges)
        if len(percentages):
            maximum = max(maximum, float(np.max(percentages)))
    return edges, maximum * 1.05


def _angle_histogram_percentages(bundle, group: str, edges: np.ndarray) -> np.ndarray:
    angles = [
        item.final_angle
        for item in bundle.summaries_for(group, True)
        if item.net_displacement > 0 and math.isfinite(item.final_angle)
    ]
    if not angles:
        return np.zeros(len(edges) - 1, dtype=float)
    counts, _ = np.histogram(angles, bins=edges)
    return counts.astype(float) / len(angles) * 100.0


def _trajectory_xy(track) -> tuple[np.ndarray, np.ndarray]:
    return np.asarray(track.raw.x) - track.raw.x[0], np.asarray(track.raw.y) - track.raw.y[0]


def _pick_representatives(bundle, group: str, count_per_type: int | None = None) -> dict[str, list]:
    count_per_type = count_per_type or bundle.parameters.representatives_per_type
    tracks = {track.uid: track for track in bundle.tracks_for(group, True)}
    result: dict[str, list] = {}
    for trajectory_type in ("Random", "Mixed", "Directed"):
        summaries = [item for item in bundle.summaries_for(group, True) if item.trajectory_type == trajectory_type]
        summaries.sort(key=lambda item: item.directionality)
        if len(summaries) <= count_per_type:
            selected = summaries
        else:
            indexes = np.linspace(0, len(summaries) - 1, count_per_type).round().astype(int)
            selected = [summaries[index] for index in indexes]
        result[trajectory_type] = [tracks[f"{item.dataset}#{item.track_id}"] for item in selected if f"{item.dataset}#{item.track_id}" in tracks]
    return result


def _plot_representative_track(ax: plt.Axes, track, trajectory_type: str, limit: float, title_size: float = 7) -> None:
    x, y = _trajectory_xy(track)
    for index in range(max(0, len(x) - 1)):
        ax.plot(x[index:index + 2], y[index:index + 2], color=plt.cm.viridis(index / max(1, len(x) - 1)), linewidth=1.4)
    ax.scatter([0], [0], s=16, color="#17211F", zorder=3)
    ax.scatter([x[-1]], [y[-1]], s=20, color=TYPE_COLORS[trajectory_type], marker="D", zorder=3)
    _apply_trajectory_limits(ax, limit)
    ax.set_title(f"{track.raw.dataset}\nTrack {track.raw.track_id}", fontsize=title_size)
    _style(ax)


def figure_representatives(bundle, group_index: int) -> bytes:
    if group_index >= len(bundle.groups):
        return _empty_figure("Representative trajectories", f"Group {group_index + 1} was not selected.")
    group = bundle.groups[group_index]
    selected = _pick_representatives(bundle, group)
    if not any(selected.values()):
        return _empty_figure(f"{group}: representative trajectories", "No valid trajectories are available.")
    limit = _bundle_trajectory_limit(bundle)
    columns = bundle.parameters.representatives_per_type
    fig, axes = _figure(3, columns, width=3, height=2.8)
    fig.suptitle(f"{group}: representative trajectories (shared scale ±{limit:.0f} px)", fontsize=14)
    for row, trajectory_type in enumerate(("Random", "Mixed", "Directed")):
        tracks = selected[trajectory_type]
        for column, ax in enumerate(axes[row]):
            if column >= len(tracks):
                ax.set_visible(False)
                continue
            _plot_representative_track(ax, tracks[column], trajectory_type, limit, title_size=8)
            if column == 0:
                ax.set_ylabel(trajectory_type, fontsize=9)
    return _png(fig)


def _plot_group_trajectory_axes(bundle, group_index: int, axes, limit: float) -> None:
    group = bundle.groups[group_index]
    tracks = list(bundle.tracks_for(group, True))
    summaries = list(bundle.summaries_for(group, True))
    summary_by_key = {(item.dataset, item.track_id): item for item in summaries}
    for track in tracks:
        summary = summary_by_key.get((track.raw.dataset, track.raw.track_id))
        trajectory_type = summary.trajectory_type if summary else "Mixed"
        x, y = _trajectory_xy(track)
        axes[0].plot(x, y, color=TYPE_COLORS[trajectory_type], linewidth=0.8, alpha=0.35)
    _apply_trajectory_limits(axes[0], limit)
    axes[0].set_title("Centered trajectories")
    axes[0].set_xlabel("Delta x (px)")
    axes[0].set_ylabel("Delta y (px)")
    _style(axes[0])
    angle_edges, radial_limit = _angle_histogram_scale(bundle)
    percentages = _angle_histogram_percentages(bundle, group, angle_edges)
    axes[1].bar(
        (angle_edges[:-1] + angle_edges[1:]) / 2,
        percentages,
        width=np.diff(angle_edges),
        color=COLORS[group_index % len(COLORS)],
        alpha=0.78,
    )
    axes[1].set_ylim(0, radial_limit)
    axes[1].set_rlabel_position(292.5)
    axes[1].tick_params(labelsize=7, colors="#53635F")
    axes[1].set_title("Final displacement angle (% of tracks)", fontsize=10)
    axes[1].set_facecolor(BACKGROUND)
    representatives = _pick_representatives(bundle, group, 3)
    for trajectory_type, selected in representatives.items():
        for track in selected:
            x, y = _trajectory_xy(track)
            axes[2].plot(x, y, color=TYPE_COLORS[trajectory_type], linewidth=1.2, alpha=0.8, label=trajectory_type)
    handles, labels = axes[2].get_legend_handles_labels()
    unique = dict(zip(labels, handles))
    if unique:
        axes[2].legend(unique.values(), unique.keys(), frameon=False, fontsize=8)
    _apply_trajectory_limits(axes[2], limit)
    axes[2].set_title("Representative tracks")
    axes[2].set_xlabel("Delta x (px)")
    _style(axes[2])


def figure_group_trajectories(bundle, group_index: int) -> bytes:
    if group_index >= len(bundle.groups):
        return _empty_figure("Group trajectory analysis", f"Group {group_index + 1} was not selected.")
    group = bundle.groups[group_index]
    if not bundle.tracks_for(group, True):
        return _empty_figure(f"{group}: trajectory analysis", "No valid trajectories are available.")
    limit = _bundle_trajectory_limit(bundle)
    fig = plt.figure(figsize=(15, 4.6), facecolor=BACKGROUND, layout="constrained")
    axes = [fig.add_subplot(1, 3, 1), fig.add_subplot(1, 3, 2, projection="polar"), fig.add_subplot(1, 3, 3)]
    fig.suptitle(f"{group}: trajectory analysis (shared scale ±{limit:.0f} px)", fontsize=14)
    _plot_group_trajectory_axes(bundle, group_index, axes, limit)
    return _png(fig)


def _significance_label(p_value: float | None) -> str:
    if p_value is None or not math.isfinite(p_value):
        return "NA"
    if p_value < 0.0001:
        return "****"
    if p_value < 0.001:
        return "***"
    if p_value < 0.01:
        return "**"
    if p_value < 0.05:
        return "*"
    return "ns"


def _add_brackets(
    ax: plt.Axes,
    comparisons: list[tuple[int, int, str]],
    data_maximum: float,
    data_minimum: float,
    overall: str | None = None,
) -> None:
    """Reserve the headroom first, then draw inside the axes.

    Everything stays clipped to the axes so brackets can never spill into a
    neighbouring panel, and the omnibus caption sits in its own band above the
    highest bracket instead of on top of the significance markers.
    """
    if not comparisons and not overall:
        return
    span = max(data_maximum - data_minimum, abs(data_maximum) * 0.1, 1e-6)
    step = span * 0.16
    ordered = sorted(comparisons, key=lambda item: (item[1] - item[0], item[0]))
    base = data_maximum + step * 0.55
    top = base + max(0, len(ordered) - 1) * step + step * 0.55
    if overall:
        top += step * 1.3
    bottom, current_top = ax.get_ylim()
    ax.set_ylim(min(bottom, data_minimum - span * 0.05), max(current_top, top))
    for level, (first, second, label) in enumerate(ordered):
        height = base + level * step
        ax.plot(
            [first, first, second, second],
            [height - step * 0.2, height, height, height - step * 0.2],
            color="#53635F", linewidth=0.8, clip_on=True,
        )
        ax.text((first + second) / 2, height + step * 0.04, label, ha="center", va="bottom", fontsize=8, clip_on=True)
    if overall:
        ax.text(
            0.5, 0.985, overall, transform=ax.transAxes,
            ha="center", va="top", fontsize=7, color="#61716D",
        )


def _metric_results(bundle, metric: str):
    return statistical_results(bundle, [metric])


def _plot_metric_distribution(ax: plt.Axes, bundle, metric: str, title: str) -> None:
    """One point per dataset, the same unit the statistics are computed on."""
    values = [[value for _dataset, value in dataset_metric_values(bundle, group, metric)] for group in bundle.groups]
    positions = [index + 1 for index, group_values in enumerate(values) if group_values]
    nonempty = [group_values for group_values in values if group_values]
    if nonempty:
        boxplot = ax.boxplot(nonempty, positions=positions, patch_artist=True, showfliers=False, widths=0.5)
        for position, box in zip(positions, boxplot["boxes"]):
            box.set_facecolor(COLORS[(position - 1) % len(COLORS)])
            box.set_edgecolor(COLORS[(position - 1) % len(COLORS)])
            box.set_alpha(0.28)
    for index, group_values in enumerate(values):
        if group_values:
            offsets = np.linspace(-0.1, 0.1, len(group_values)) if len(group_values) > 1 else np.asarray([0.0])
            ax.scatter(index + 1 + offsets, group_values, s=22, color=COLORS[index % len(COLORS)], alpha=0.82, zorder=3)
    ax.set_xticks(
        np.arange(1, len(bundle.groups) + 1),
        [f"{group}\nn={len(group_values)}" for group, group_values in zip(bundle.groups, values)],
        rotation=20, ha="right",
    )
    results = _metric_results(bundle, metric)
    comparisons: list[tuple[int, int, str]] = []
    overall_label = None
    if len(bundle.groups) == 2:
        result = results[0]
        comparisons.append((1, 2, _significance_label(result.p_adjusted)))
        overall_label = "Mann-Whitney U: NA" if result.p_value is None else f"Mann-Whitney U p={result.p_value:.3g}; r_rb={result.effect_size:.3f}"
    else:
        overall = next(result for result in results if result.test == "Kruskal-Wallis")
        overall_label = "Kruskal-Wallis: NA" if overall.p_value is None else f"Kruskal-Wallis p={overall.p_value:.3g}; epsilon^2={overall.effect_size:.3f}"
        group_positions = {group: index + 1 for index, group in enumerate(bundle.groups)}
        pairwise = [result for result in results if result.test == "Mann-Whitney U"]
        if len(bundle.groups) > 3:
            pairwise = [result for result in pairwise if result.p_adjusted is not None and result.p_adjusted < 0.05]
        comparisons = [
            (group_positions[result.group_1], group_positions[result.group_2], _significance_label(result.p_adjusted))
            for result in pairwise
        ]
    finite_values = [value for group_values in values for value in group_values if math.isfinite(value)]
    if finite_values:
        _add_brackets(ax, comparisons, max(finite_values), min(finite_values), overall_label)
    elif overall_label:
        ax.text(0.5, 0.5, overall_label, transform=ax.transAxes, ha="center", va="center", fontsize=8)
    ax.set_title(title, fontsize=9.5)
    _style(ax)


def _plot_classification_axes(bundle, axes) -> None:
    types = ("Random", "Mixed", "Directed")
    x = np.arange(len(bundle.groups))
    bottom = np.zeros(len(bundle.groups))
    for trajectory_type in types:
        values = []
        for group in bundle.groups:
            by_dataset: dict[str, list] = defaultdict(list)
            for summary in bundle.summaries_for(group, True):
                by_dataset[summary.dataset].append(summary)
            percentages = [sum(item.trajectory_type == trajectory_type for item in items) / len(items) * 100 for items in by_dataset.values() if items]
            values.append(statistics.fmean(percentages) if percentages else 0.0)
        axes[0].bar(x, values, bottom=bottom, label=trajectory_type, color=TYPE_COLORS[trajectory_type])
        bottom += np.asarray(values)
    axes[0].set_xticks(x, bundle.groups, rotation=20, ha="right")
    axes[0].set_ylabel("Tracks (%)")
    axes[0].set_title("Composition", fontsize=9.5)
    # The bars fill 0-100 %, so the legend gets its own strip above them.
    axes[0].set_ylim(0, 114)
    axes[0].set_yticks(range(0, 101, 20))
    axes[0].spines["left"].set_bounds(0, 100)
    axes[0].legend(loc="upper center", ncol=len(types), frameon=False, fontsize=8, handlelength=1.2, columnspacing=1.0)
    _style(axes[0])
    _plot_metric_distribution(axes[1], bundle, "directionality", "Directionality distribution")
    axes[1].set_ylabel("Directionality")
    _plot_metric_distribution(axes[2], bundle, "turning_angle_std", "Turning-angle variability")
    axes[2].set_ylabel("Std. turning angle (deg)")


def _parameter_metrics(bundle) -> list[tuple[str, str]]:
    return [
        (key, SUMMARY_METRICS[key])
        for key in bundle.parameters.summary_metrics
        if key != "turning_angle_std"
    ]


def figure_parameter_distributions(bundle) -> bytes:
    metrics = _parameter_metrics(bundle)
    fig, axes = _figure(_grid_rows(len(metrics)), GRID_COLUMNS, width=4.2, height=3.4)
    fig.suptitle("Dataset-level parameter distributions", fontsize=14)
    for ax, (attribute, title) in zip(axes.flat, metrics):
        _plot_metric_distribution(ax, bundle, attribute, title)
    _hide_unused(axes, len(metrics))
    return _png(fig)


def figure_paper_cell_and_classification(bundle) -> bytes:
    fig, axes = _figure(2, 3, width=4.8, height=4.0)
    fig.suptitle("Cell appearance and trajectory classification", fontsize=15)
    _plot_cell_appearance_axes(bundle, axes[0])
    _plot_classification_axes(bundle, axes[1])
    handles, labels = axes[0, 0].get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, loc="outside lower center", ncol=len(labels), frameon=False, fontsize=9)
    return _png(fig)


def figure_paper_temporal_and_parameters(bundle) -> bytes:
    """Left and right grids share one layout so panel i matches panel i."""
    temporal_metrics = [(key, TEMPORAL_METRICS[key]) for key in bundle.parameters.temporal_metrics]
    parameter_metrics = _parameter_metrics(bundle)
    rows = max(_grid_rows(len(temporal_metrics)), _grid_rows(len(parameter_metrics)))
    fig = plt.figure(
        figsize=(GRID_COLUMNS * 2 * 4.2, rows * 4.4),
        facecolor=BACKGROUND,
        layout="constrained",
    )
    subfigures = fig.subfigures(1, 2, wspace=0.03)
    temporal_axes = subfigures[0].subplots(rows, GRID_COLUMNS, squeeze=False)
    parameter_axes = subfigures[1].subplots(rows, GRID_COLUMNS, squeeze=False)
    subfigures[0].suptitle("Long-track temporal trends", fontsize=14)
    subfigures[1].suptitle("Dataset-level parameter distributions", fontsize=14)
    _plot_temporal_axes(bundle, temporal_axes.flat, True, temporal_metrics)
    _hide_unused(temporal_axes, len(temporal_metrics))
    for ax, (metric, title) in zip(parameter_axes.flat, parameter_metrics):
        _plot_metric_distribution(ax, bundle, metric, title)
    _hide_unused(parameter_axes, len(parameter_metrics))
    handles, labels = temporal_axes.flat[0].get_legend_handles_labels()
    if handles:
        subfigures[0].legend(handles, labels, loc="outside lower center", ncol=len(labels), frameon=False)
    fig.suptitle("Temporal behavior and migration parameters", fontsize=17)
    return _png(fig)


def figure_paper_group_trajectories(bundle) -> bytes:
    rows = len(bundle.groups)
    limit = _bundle_trajectory_limit(bundle)
    fig = plt.figure(figsize=(15, 4.8 * rows), facecolor=BACKGROUND, layout="constrained")
    subfigures = fig.subfigures(rows, 1, squeeze=False)
    for row in range(rows):
        subfigure = subfigures[row, 0]
        row_axes = [
            subfigure.add_subplot(1, 3, 1),
            subfigure.add_subplot(1, 3, 2, projection="polar"),
            subfigure.add_subplot(1, 3, 3),
        ]
        _plot_group_trajectory_axes(bundle, row, row_axes, limit)
        subfigure.suptitle(bundle.groups[row], fontsize=13)
    fig.suptitle(f"Group trajectory analysis (shared scale ±{limit:.0f} px)", fontsize=15)
    return _png(fig)


def figure_supplementary_representatives(bundle) -> bytes:
    count = bundle.parameters.representatives_per_type
    limit = _bundle_trajectory_limit(bundle)
    horizontal = len(bundle.groups) == 2
    rows = 3 if horizontal else 3 * len(bundle.groups)
    columns = count * len(bundle.groups) if horizontal else count
    fig, axes = _figure(rows, columns, width=2.7, height=2.6)
    fig.suptitle(f"Representative trajectories (shared scale ±{limit:.0f} px)", fontsize=15)
    for group_index, group in enumerate(bundle.groups):
        selected = _pick_representatives(bundle, group)
        for type_index, trajectory_type in enumerate(("Random", "Mixed", "Directed")):
            row = type_index if horizontal else group_index * 3 + type_index
            start_column = group_index * count if horizontal else 0
            tracks = selected[trajectory_type]
            for column in range(count):
                ax = axes[row, start_column + column]
                if column >= len(tracks):
                    ax.set_visible(False)
                    continue
                _plot_representative_track(ax, tracks[column], trajectory_type, limit)
                if column == 0:
                    ax.set_ylabel(f"{group}\n{trajectory_type}", fontsize=8)
    return _png(fig)


def _slug(group: str, index: int) -> str:
    return re.sub(r"[^a-z0-9]+", "-", group.casefold()).strip("-") or f"group-{index + 1}"


def render_all_figures(bundle) -> dict[str, bytes]:
    """Render configured figures from one shared analysis bundle.

    Filenames are numbered by the canonical FIGURE_TYPES order rather than by
    render order, so a given figure keeps the same prefix regardless of which
    other figures were selected.
    """
    selected = set(bundle.parameters.figure_types)
    order = {key: index + 1 for index, key in enumerate(FIGURE_TYPES)}
    temporal_metrics = [(key, TEMPORAL_METRICS[key]) for key in bundle.parameters.temporal_metrics]
    rendered: dict[str, bytes] = {}

    def add(key: str, filename: str, content: bytes) -> None:
        rendered[f"figure_{order[key]:02d}_{filename}.png"] = content

    if "cell_appearance" in selected:
        add("cell_appearance", "cell_appearance", figure_cell_appearance(bundle))
    if "temporal_long" in selected:
        add("temporal_long", "temporal_long", _plot_temporal(bundle, "Temporal trends | long tracks", True, temporal_metrics))
    if "classification" in selected:
        add("classification", "trajectory_classification", figure_classification(bundle))
    if "turning_angle_distribution" in selected:
        add("turning_angle_distribution", "turning_angle_distribution", figure_turning_angle_distribution(bundle))
    if "msd_long" in selected:
        add("msd_long", "msd_long", _plot_msd(bundle, "Mean square displacement | long tracks", True))
    if "msd_summary" in selected:
        add("msd_summary", "msd_summary", figure_msd_summary(bundle))
    if "representatives" in selected:
        for index, group in enumerate(bundle.groups):
            add("representatives", f"{_slug(group, index)}_representatives", figure_representatives(bundle, index))
    if "temporal_all" in selected:
        add("temporal_all", "temporal_all", _plot_temporal(bundle, "Temporal trends | all tracks", False, temporal_metrics))
    if "msd_all" in selected:
        add("msd_all", "msd_all", _plot_msd(bundle, "Mean square displacement | all tracks", False))
    if "group_trajectories" in selected:
        for index, group in enumerate(bundle.groups):
            add("group_trajectories", f"{_slug(group, index)}_trajectories", figure_group_trajectories(bundle, index))
    if "parameter_distributions" in selected:
        add("parameter_distributions", "parameter_distributions", figure_parameter_distributions(bundle))
    if {"cell_appearance", "classification"} <= selected:
        rendered["paper_01_cell_and_classification.png"] = figure_paper_cell_and_classification(bundle)
    if {"temporal_long", "parameter_distributions"} <= selected:
        rendered["paper_02_temporal_and_parameters.png"] = figure_paper_temporal_and_parameters(bundle)
    if "group_trajectories" in selected:
        rendered["paper_03_group_trajectories.png"] = figure_paper_group_trajectories(bundle)
    if "representatives" in selected:
        rendered["supplementary_01_representative_trajectories.png"] = figure_supplementary_representatives(bundle)
    return rendered
