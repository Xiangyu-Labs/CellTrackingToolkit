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
from celltrack.analysis.models import SUMMARY_METRICS, TEMPORAL_METRICS

COLORS = ("#156F62", "#D97706", "#2563EB", "#B42318", "#7C3AED", "#087EA4")
LINE_STYLES = ("-", "--", "-.", ":", (0, (5, 2, 1, 2)), (0, (3, 1, 1, 1)))
TYPE_COLORS = {"Random": "#B42318", "Mixed": "#D97706", "Directed": "#19704E"}
BACKGROUND = "#F7F9F8"

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial Unicode MS", "Noto Sans CJK SC", "PingFang SC", "DejaVu Sans"],
    "axes.titleweight": "semibold",
    "axes.labelcolor": "#33423E",
    "text.color": "#17211F",
})


def _png(fig: plt.Figure) -> bytes:
    output = io.BytesIO()
    fig.savefig(output, format="png", dpi=140, bbox_inches="tight", facecolor=BACKGROUND)
    plt.close(fig)
    return output.getvalue()


def _style(ax: plt.Axes) -> None:
    ax.set_facecolor("white")
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#C9D4D0")
    ax.grid(axis="y", color="#E4EBE8", linewidth=0.7, alpha=0.9)
    ax.tick_params(labelsize=8, colors="#53635F")


def _figure(rows: int = 1, columns: int = 1, width: float = 5.2, height: float = 3.8):
    fig, axes = plt.subplots(rows, columns, figsize=(columns * width, rows * height), squeeze=False)
    fig.patch.set_facecolor(BACKGROUND)
    return fig, axes


def _empty_figure(title: str, message: str) -> bytes:
    fig, axes = _figure(width=9, height=4.5)
    ax = axes[0, 0]
    ax.axis("off")
    ax.text(0.5, 0.58, title, ha="center", va="center", fontsize=17, fontweight="semibold")
    ax.text(0.5, 0.42, message, ha="center", va="center", fontsize=11, color="#61716D")
    return _png(fig)


def _tracks(bundle, prefer_long: bool) -> list:
    result = []
    for group in bundle.groups:
        result.extend(bundle.tracks_for(group, prefer_long))
    return result


def _summaries(bundle, prefer_long: bool) -> list:
    result = []
    for group in bundle.groups:
        result.extend(bundle.summaries_for(group, prefer_long))
    return result


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


def _plot_temporal(bundle, title: str, prefer_long: bool, metrics: tuple[tuple[str, str], ...]) -> bytes:
    columns = 3
    rows = math.ceil(len(metrics) / columns)
    fig, axes = _figure(rows, columns, width=4.3, height=3.2)
    fig.suptitle(title, fontsize=15, y=1.005)
    for ax, (attribute, label) in zip(axes.flat, metrics):
        for index, group in enumerate(bundle.groups):
            frames, mean, sem = _temporal_values(list(bundle.tracks_for(group, prefer_long)), attribute)
            if not len(frames):
                continue
            color = COLORS[index % len(COLORS)]
            ax.plot(frames, mean, color=color, linestyle=LINE_STYLES[index % len(LINE_STYLES)], linewidth=1.7, label=group)
            ax.fill_between(frames, mean - sem, mean + sem, color=color, alpha=0.12)
        ax.set_title(label, fontsize=10)
        ax.set_xlabel("Frame", fontsize=8)
        _style(ax)
    for ax in axes.flat[len(metrics):]:
        ax.set_visible(False)
    handles, labels = axes.flat[0].get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 0.975), ncol=min(6, len(labels)), frameon=False, fontsize=9)
        fig.subplots_adjust(top=0.89, hspace=0.42, wspace=0.28)
    return _png(fig)


def figure_cell_appearance(bundle) -> bytes:
    fig, axes = _figure(1, 3, width=4.6, height=4)
    fig.suptitle("Cell appearance over time", fontsize=15, y=1.01)
    for index, group in enumerate(bundle.groups):
        tracks = bundle.tracks_for(group, prefer_long=False)
        color = COLORS[index % len(COLORS)]
        first = [track.raw.timeframe[0] for track in tracks]
        last = [track.raw.timeframe[-1] for track in tracks]
        if not first:
            continue
        frames = np.arange(min(first), max(last) + 1)
        new = np.asarray([sum(value == frame for value in first) for frame in frames])
        cumulative = np.cumsum(new)
        active = np.asarray([sum(start <= frame <= end for start, end in zip(first, last)) for frame in frames])
        for ax, values, label in zip(axes[0], (cumulative, active, new), ("Cumulative appeared", "Active tracks", "New tracks")):
            ax.plot(frames, values, color=color, linestyle=LINE_STYLES[index % len(LINE_STYLES)], linewidth=1.8, label=group)
            ax.set_title(label, fontsize=11)
            ax.set_xlabel("Frame", fontsize=9)
            ax.set_ylabel("Track count", fontsize=9)
            _style(ax)
    axes[0, 0].legend(frameon=False, fontsize=8)
    fig.tight_layout()
    return _png(fig)


def figure_classification(bundle) -> bytes:
    fig, axes = _figure(1, 3, width=4.8, height=4.2)
    fig.suptitle("Trajectory classification", fontsize=15, y=1.01)
    types = ("Random", "Mixed", "Directed")
    x = np.arange(len(bundle.groups))
    bottom = np.zeros(len(bundle.groups))
    for trajectory_type in types:
        values = []
        for group in bundle.groups:
            by_dataset: dict[str, list] = defaultdict(list)
            for summary in bundle.summaries_for(group, True):
                by_dataset[summary.dataset].append(summary)
            dataset_percentages = [
                sum(item.trajectory_type == trajectory_type for item in summaries) / len(summaries) * 100
                for summaries in by_dataset.values() if summaries
            ]
            values.append(statistics.fmean(dataset_percentages) if dataset_percentages else 0.0)
        axes[0, 0].bar(x, values, bottom=bottom, label=trajectory_type, color=TYPE_COLORS[trajectory_type])
        bottom += np.asarray(values)
    axes[0, 0].set_xticks(x, bundle.groups, rotation=20, ha="right")
    axes[0, 0].set_ylabel("Tracks (%)")
    axes[0, 0].set_title("Composition")
    axes[0, 0].legend(frameon=False, fontsize=8)
    _style(axes[0, 0])

    directionality = [[value for _dataset, value in dataset_metric_values(bundle, group, "directionality")] for group in bundle.groups]
    turning = [[value for _dataset, value in dataset_metric_values(bundle, group, "turning_angle_std")] for group in bundle.groups]
    for ax, values, title, ylabel in (
        (axes[0, 1], directionality, "Directionality distribution", "Directionality"),
        (axes[0, 2], turning, "Turning-angle variability", "Std. turning angle (deg)"),
    ):
        positions = [index + 1 for index, group_values in enumerate(values) if group_values]
        nonempty = [group_values for group_values in values if group_values]
        if nonempty:
            plot = ax.boxplot(nonempty, positions=positions, patch_artist=True, showfliers=False, widths=0.5)
            for position, box in zip(positions, plot["boxes"]):
                box.set_facecolor(COLORS[(position - 1) % len(COLORS)])
                box.set_edgecolor(COLORS[(position - 1) % len(COLORS)])
                box.set_alpha(0.28)
        for index, group_values in enumerate(values):
            if not group_values:
                continue
            offsets = np.linspace(-0.1, 0.1, len(group_values)) if len(group_values) > 1 else np.asarray([0.0])
            ax.scatter(index + 1 + offsets, group_values, s=22, color=COLORS[index % len(COLORS)], alpha=0.82, zorder=3)
        labels = [f"{group}\nn={len(group_values)}" for group, group_values in zip(bundle.groups, values)]
        ax.set_xticks(np.arange(1, len(bundle.groups) + 1), labels)
        ax.tick_params(axis="x", rotation=20)
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        if ylabel == "Directionality":
            ax.set_ylim(0, 1)
        _style(ax)
    fig.tight_layout()
    return _png(fig)


def figure_turning_angle_distribution(bundle) -> bytes:
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
    fig.tight_layout()
    return _png(fig)


def _plot_msd(bundle, title: str, prefer_long: bool) -> bytes:
    fig, axes = _figure(1, 3, width=4.8, height=4.2)
    fig.suptitle(title, fontsize=15, y=1.01)
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
    x_label = "Time lag (min)" if bundle.parameters.frame_interval_minutes is not None else "Frame lag"
    y_label = "MSD (µm²)" if bundle.parameters.microns_per_pixel is not None else "MSD (px²)"
    for ax, subtitle in zip(axes[0, :2], ("Population mean MSD", "Log-log MSD")):
        ax.set_title(subtitle)
        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        ax.legend(frameon=False, fontsize=8)
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
    axes[0, 2].set_xticks(np.arange(1, len(bundle.groups) + 1), [f"{group}\nn={len(values)}" for group, values in zip(bundle.groups, alpha_values)])
    axes[0, 2].axhline(1, color="#8B9895", linewidth=1, linestyle="--")
    axes[0, 2].set_title("MSD exponent alpha")
    axes[0, 2].tick_params(axis="x", rotation=20)
    _style(axes[0, 2])
    fig.tight_layout()
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
    ax.set_title("MSD comparison (mean ± 95% CI)")
    ax.set_xlabel("Time lag (min)" if bundle.parameters.frame_interval_minutes is not None else "Frame lag")
    ax.set_ylabel("MSD (µm²)" if bundle.parameters.microns_per_pixel is not None else "MSD (px²)")
    ax.legend(frameon=False, fontsize=9)
    _style(ax)
    fig.tight_layout()
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
    return _trajectory_limit(_tracks(bundle, True))


def _angle_histogram_scale(bundle) -> tuple[np.ndarray, float]:
    edges = np.linspace(-math.pi, math.pi, bundle.parameters.angle_bins + 1)
    maximum = 1
    for group in bundle.groups:
        angles = [item.final_angle for item in bundle.summaries_for(group, True) if math.isfinite(item.final_angle)]
        counts, _ = np.histogram(angles, bins=edges)
        if len(counts):
            maximum = max(maximum, int(np.max(counts)))
    return edges, maximum * 1.05


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


def figure_representatives(bundle, group_index: int) -> bytes:
    if group_index >= len(bundle.groups):
        return _empty_figure("Representative trajectories", f"Group {group_index + 1} was not selected.")
    group = bundle.groups[group_index]
    selected = _pick_representatives(bundle, group)
    all_tracks = [track for values in selected.values() for track in values]
    if not all_tracks:
        return _empty_figure(f"{group}: representative trajectories", "No valid trajectories are available.")
    limit = _bundle_trajectory_limit(bundle)
    columns = bundle.parameters.representatives_per_type
    fig, axes = _figure(3, columns, width=3, height=2.8)
    fig.suptitle(f"{group}: representative trajectories", fontsize=15, y=1.005)
    for row, trajectory_type in enumerate(("Random", "Mixed", "Directed")):
        for column, ax in enumerate(axes[row]):
            tracks = selected[trajectory_type]
            if column >= len(tracks):
                ax.set_visible(False)
                continue
            track = tracks[column]
            x = np.asarray(track.raw.x) - track.raw.x[0]
            y = np.asarray(track.raw.y) - track.raw.y[0]
            for index in range(max(0, len(x) - 1)):
                ax.plot(x[index:index + 2], y[index:index + 2], color=plt.cm.viridis(index / max(1, len(x) - 1)), linewidth=1.5)
            ax.scatter([0], [0], s=20, color="#17211F", zorder=3)
            ax.scatter([x[-1]], [y[-1]], s=24, color=TYPE_COLORS[trajectory_type], marker="D", zorder=3)
            ax.set_xlim(-limit, limit)
            ax.set_ylim(-limit, limit)
            ax.set_aspect("equal")
            ax.set_title(f"{trajectory_type} | {track.raw.dataset}\nTrack {track.raw.track_id}", fontsize=8)
            _style(ax)
    fig.tight_layout()
    return _png(fig)


def figure_group_trajectories(bundle, group_index: int) -> bytes:
    if group_index >= len(bundle.groups):
        return _empty_figure("Group trajectory analysis", f"Group {group_index + 1} was not selected.")
    group = bundle.groups[group_index]
    tracks = list(bundle.tracks_for(group, True))
    summaries = list(bundle.summaries_for(group, True))
    if not tracks:
        return _empty_figure(f"{group}: trajectory analysis", "No valid trajectories are available.")
    limit = _bundle_trajectory_limit(bundle)
    fig = plt.figure(figsize=(15, 4.6))
    fig.patch.set_facecolor(BACKGROUND)
    axes = [fig.add_subplot(1, 3, 1), fig.add_subplot(1, 3, 2, projection="polar"), fig.add_subplot(1, 3, 3)]
    fig.suptitle(f"{group}: trajectory analysis", fontsize=15, y=1.01)
    summary_by_key = {(item.dataset, item.track_id): item for item in summaries}
    for track in tracks:
        summary = summary_by_key.get((track.raw.dataset, track.raw.track_id))
        trajectory_type = summary.trajectory_type if summary else "Mixed"
        x = np.asarray(track.raw.x) - track.raw.x[0]
        y = np.asarray(track.raw.y) - track.raw.y[0]
        axes[0].plot(x, y, color=TYPE_COLORS[trajectory_type], linewidth=0.8, alpha=0.35)
    axes[0].set_xlim(-limit, limit)
    axes[0].set_ylim(-limit, limit)
    axes[0].set_aspect("equal")
    axes[0].set_title("Centered trajectories")
    axes[0].set_xlabel("Delta x (px)")
    axes[0].set_ylabel("Delta y (px)")
    _style(axes[0])
    angles = [item.final_angle for item in summaries if math.isfinite(item.final_angle)]
    angle_edges, radial_limit = _angle_histogram_scale(bundle)
    axes[1].hist(angles, bins=angle_edges, color=COLORS[group_index % len(COLORS)], alpha=0.78)
    axes[1].set_ylim(0, radial_limit)
    axes[1].set_title("Final displacement angle")
    representatives = _pick_representatives(bundle, group, 3)
    for trajectory_type, selected in representatives.items():
        for track in selected:
            x = np.asarray(track.raw.x) - track.raw.x[0]
            y = np.asarray(track.raw.y) - track.raw.y[0]
            axes[2].plot(x, y, color=TYPE_COLORS[trajectory_type], linewidth=1.2, alpha=0.8, label=trajectory_type)
    handles, labels = axes[2].get_legend_handles_labels()
    unique = dict(zip(labels, handles))
    axes[2].legend(unique.values(), unique.keys(), frameon=False, fontsize=8)
    axes[2].set_xlim(-limit, limit)
    axes[2].set_ylim(-limit, limit)
    axes[2].set_aspect("equal")
    axes[2].set_title("Representative tracks")
    _style(axes[2])
    fig.tight_layout()
    return _png(fig)


def _significance_label(p_value: float) -> str:
    if p_value < 0.0001:
        return "****"
    if p_value < 0.001:
        return "***"
    if p_value < 0.01:
        return "**"
    if p_value < 0.05:
        return "*"
    return "ns"


def _add_brackets(ax: plt.Axes, comparisons: list[tuple[int, int, str]], data_maximum: float, data_minimum: float) -> None:
    if not comparisons:
        return
    span = max(data_maximum - data_minimum, abs(data_maximum) * 0.1, 1e-6)
    base = data_maximum + span * 0.08
    step = span * 0.12
    for level, (first, second, label) in enumerate(comparisons):
        height = base + level * step
        ax.plot([first, first, second, second], [height - step * 0.18, height, height, height - step * 0.18], color="#53635F", linewidth=0.8)
        ax.text((first + second) / 2, height + step * 0.03, label, ha="center", va="bottom", fontsize=8)
    current_bottom, current_top = ax.get_ylim()
    ax.set_ylim(current_bottom, max(current_top, base + len(comparisons) * step))


def figure_parameter_distributions(bundle) -> bytes:
    metrics = [(key, SUMMARY_METRICS[key]) for key in bundle.parameters.summary_metrics]
    columns = min(3, len(metrics))
    rows = math.ceil(len(metrics) / columns)
    fig, axes = _figure(rows, columns, width=4.2, height=3.4)
    fig.suptitle("Dataset-level parameter distributions", fontsize=15, y=1.005)
    result_by_metric = defaultdict(list)
    for result in statistical_results(bundle):
        result_by_metric[result.metric].append(result)
    for ax, (attribute, title) in zip(axes.flat, metrics):
        values = [
            [value for _dataset, value in dataset_metric_values(bundle, group, attribute)]
            for group in bundle.groups
        ]
        positions = [index + 1 for index, group_values in enumerate(values) if group_values]
        nonempty = [group_values for group_values in values if group_values]
        if nonempty:
            boxplot = ax.boxplot(nonempty, positions=positions, patch_artist=True, showfliers=False, widths=0.5)
            for position, box in zip(positions, boxplot["boxes"]):
                box.set_facecolor(COLORS[(position - 1) % len(COLORS)])
                box.set_edgecolor(COLORS[(position - 1) % len(COLORS)])
                box.set_alpha(0.28)
        for index, group_values in enumerate(values):
            if not group_values:
                continue
            offsets = np.linspace(-0.1, 0.1, len(group_values)) if len(group_values) > 1 else np.asarray([0.0])
            ax.scatter(index + 1 + offsets, group_values, s=22, color=COLORS[index % len(COLORS)], alpha=0.82, zorder=3)
        labels = [f"{group}\nn={len(group_values)}" for group, group_values in zip(bundle.groups, values)]
        ax.set_xticks(np.arange(1, len(bundle.groups) + 1), labels, rotation=20, ha="right")
        metric_results = result_by_metric[attribute]
        comparisons: list[tuple[int, int, str]] = []
        if len(bundle.groups) == 2:
            result = metric_results[0]
            if result.p_value is None:
                annotation = "Insufficient data"
            else:
                annotation = f"Mann–Whitney U p={result.p_value:.3g}\nr_rb={result.effect_size:.3f}"
                comparisons.append((1, 2, _significance_label(result.p_value)))
        else:
            overall = next(result for result in metric_results if result.test == "Kruskal-Wallis")
            if overall.p_value is None:
                annotation = "Kruskal–Wallis: Insufficient data"
            else:
                annotation = f"Kruskal–Wallis p={overall.p_value:.3g}\nε²={overall.effect_size:.3f}"
            group_positions = {group: index + 1 for index, group in enumerate(bundle.groups)}
            comparisons.extend(
                (group_positions[result.group_1], group_positions[result.group_2], _significance_label(result.p_adjusted))
                for result in metric_results
                if result.test == "Mann-Whitney U" and result.p_adjusted is not None and result.p_adjusted < 0.05
            )
        ax.text(0.98, 0.96, annotation, transform=ax.transAxes, ha="right", va="top", fontsize=7, color="#61716D")
        finite_values = [value for group_values in values for value in group_values]
        if finite_values:
            _add_brackets(ax, comparisons, max(finite_values), min(finite_values))
        ax.set_title(title, fontsize=9.5)
        _style(ax)
    for ax in axes.flat[len(metrics):]:
        ax.set_visible(False)
    fig.tight_layout()
    return _png(fig)


def render_all_figures(bundle) -> dict[str, bytes]:
    """Render configured figures from one shared analysis bundle."""
    selected = set(bundle.parameters.figure_types)
    temporal_metrics = [(key, TEMPORAL_METRICS[key]) for key in bundle.parameters.temporal_metrics]
    rendered: dict[str, bytes] = {}

    def add(filename: str, content: bytes) -> None:
        rendered[f"figure_{len(rendered) + 1:02d}_{filename}.png"] = content

    if "cell_appearance" in selected:
        add("cell_appearance", figure_cell_appearance(bundle))
    if "temporal_long" in selected:
        add("temporal_long", _plot_temporal(bundle, "Temporal trends | long tracks", True, temporal_metrics))
    if "classification" in selected:
        add("trajectory_classification", figure_classification(bundle))
    if "turning_angle_distribution" in selected:
        add("turning_angle_distribution", figure_turning_angle_distribution(bundle))
    if "msd_long" in selected:
        add("msd_long", _plot_msd(bundle, "Mean square displacement | long tracks", True))
    if "msd_summary" in selected:
        add("msd_summary", figure_msd_summary(bundle))
    if "representatives" in selected:
        for index, group in enumerate(bundle.groups):
            slug = re.sub(r"[^a-z0-9]+", "-", group.casefold()).strip("-") or f"group-{index + 1}"
            add(f"{slug}_representatives", figure_representatives(bundle, index))
    if "temporal_all" in selected:
        add("temporal_all", _plot_temporal(bundle, "Temporal trends | all tracks", False, temporal_metrics))
    if "msd_all" in selected:
        add("msd_all", _plot_msd(bundle, "Mean square displacement | all tracks", False))
    if "group_trajectories" in selected:
        for index, group in enumerate(bundle.groups):
            slug = re.sub(r"[^a-z0-9]+", "-", group.casefold()).strip("-") or f"group-{index + 1}"
            add(f"{slug}_trajectories", figure_group_trajectories(bundle, index))
    if "parameter_distributions" in selected:
        add("parameter_distributions", figure_parameter_distributions(bundle))
    return rendered
