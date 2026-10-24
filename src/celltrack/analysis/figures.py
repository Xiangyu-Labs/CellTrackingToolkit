"""Render the complete md_image_index figure set from one shared bundle.

Drawing is split in two layers. The ``_plot_*`` helpers put data on axes and
are the only code that decides what a panel shows. The ``_draw_*`` functions
lay those axes out inside a container, which is either a whole figure or one
panel of a composite figure, so a standalone figure and the same panel inside
a composite are drawn by identical code.

A ``FigureStyle`` is applied last, at save time, and only touches colours,
text, font size and output settings. With the default style every figure is
byte-for-byte what it was before styles existed.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
import io
import math
import re
import statistics
from string import ascii_uppercase
from typing import Callable, Iterator

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.text import Text
from matplotlib.ticker import Formatter
import numpy as np


from celltrack.analysis.compute import (
    dataset_metric_values,
    msd_summary,
    statistical_results,
    turning_angle_distribution,
)
from celltrack.analysis.models import FIGURE_TYPES, SUMMARY_METRICS, TEMPORAL_METRICS, unit_label
from celltrack.analysis.style import (
    BLOCKS as BLOCK_LABELS,
    DEFAULT_FIGURE_STYLE,
    DEFAULT_GROUP_COLORS,
    DEFAULT_TYPE_COLORS,
    CompositeFigure,
    FigureStyle,
    slug,
)

# COLORS, TYPE_COLORS and _STYLE are swapped by ``figure_style`` while a
# styled render runs, and restored afterwards.
COLORS = DEFAULT_GROUP_COLORS
LINE_STYLES = ("-", "--", "-.", ":", (0, (5, 2, 1, 2)), (0, (3, 1, 1, 1)))
TYPE_COLORS = dict(DEFAULT_TYPE_COLORS)
BACKGROUND = "#FFFFFF"
GRID_COLUMNS = 3
_STYLE = DEFAULT_FIGURE_STYLE

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial Unicode MS", "Noto Sans CJK SC", "PingFang SC", "DejaVu Sans"],
    "axes.titleweight": "normal",
    "axes.labelcolor": "#33423E",
    "text.color": "#17211F",
    # Keep text as text in PDF and SVG exports so it stays editable in
    # Illustrator, Inkscape or PowerPoint.
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "svg.fonttype": "none",
})

_MATH_DEFAULT = re.compile(r"\$\\mathdefault\{(.*?)\}\$")
_SAVE_OPTIONS = {
    "png": {},
    "tiff": {"pil_kwargs": {"compression": "tiff_lzw"}},
    "pdf": {"metadata": {"CreationDate": None}},
    "svg": {"metadata": {"Date": None}},
}


class RenderedFigure(bytes):
    """The PNG bytes of a figure, with its other formats and its texts.

    It is a ``bytes`` so every caller that only wants the PNG keeps working.
    ``texts`` lists the labels as drawn by default, before any replacement,
    which is what the figure editor offers for renaming. ``label`` names the
    figure on the result page when the file name alone is not enough.
    """

    extras: dict[str, bytes]
    texts: tuple[str, ...]
    label: str = ""

    def __new__(cls, png: bytes, extras: dict[str, bytes] | None = None, texts: tuple[str, ...] = ()):
        rendered = super().__new__(cls, png)
        rendered.extras = dict(extras or {})
        rendered.texts = tuple(texts)
        return rendered


@contextmanager
def figure_style(style: FigureStyle | None, groups: tuple[str, ...] = ()) -> Iterator[FigureStyle]:
    """Apply a style to every figure rendered inside the block."""
    global COLORS, TYPE_COLORS, _STYLE
    previous = COLORS, TYPE_COLORS, _STYLE
    style = style or DEFAULT_FIGURE_STYLE
    palette = list(DEFAULT_GROUP_COLORS)
    for index, group in enumerate(groups):
        if group in style.group_colors and index < len(palette):
            palette[index] = style.group_colors[group]
    COLORS = tuple(palette)
    TYPE_COLORS = {**DEFAULT_TYPE_COLORS, **style.type_colors}
    _STYLE = style
    try:
        yield style
    finally:
        COLORS, TYPE_COLORS, _STYLE = previous


def _is_label(text: str) -> bool:
    """Words, not tick numbers such as '120', '45°' or '10^2'."""
    return bool(re.search(r"[A-Za-z]", _MATH_DEFAULT.sub(r"\1", text)))


def _figure_texts(fig: plt.Figure) -> tuple[str, ...]:
    """Every distinct line of wording on a drawn figure, in drawing order."""
    found: dict[str, None] = {}
    for text in fig.findobj(Text):
        if not text.get_visible():
            continue
        for line in text.get_text().split("\n"):
            if line and _is_label(line):
                found.setdefault(line)
    return tuple(found)


def _rename(value: str, mapping: dict[str, str]) -> str:
    """Replace a whole label, or else any of its lines that match exactly.

    A line replaced with an empty string is dropped, so "Glutaraldehyde\nn=10"
    can lose its count line while keeping the name.
    """
    if value in mapping:
        return mapping[value]
    if "\n" not in value:
        return value
    lines = [mapping.get(line, line) for line in value.split("\n")]
    return "\n".join(line for line, original in zip(lines, value.split("\n")) if line or not original)


class _RenamedFormatter(Formatter):
    """Wraps an axis formatter and renames the labels it produces."""

    def __init__(self, inner: Formatter, mapping: dict[str, str]):
        self.inner = inner
        self.mapping = mapping

    def __call__(self, x, pos=None):
        return _rename(self.inner(x, pos), self.mapping)

    def format_ticks(self, values):
        return [_rename(item, self.mapping) for item in self.inner.format_ticks(values)]

    def get_offset(self):
        return self.inner.get_offset()

    def set_locs(self, locs):
        self.inner.set_locs(locs)


def _containers(container) -> Iterator:
    yield container
    for child in container.subfigs:
        yield from _containers(child)


def _hide(text: Text) -> None:
    text.set_visible(False)
    text.set_in_layout(False)


def _restyle(fig: plt.Figure, style: FigureStyle) -> None:
    """Rewrite and resize text on a drawn figure. Data artists are not touched."""
    mapping, scale = style.text, style.font_scale
    tick_labels: set[int] = set()
    for ax in fig.get_axes():
        for name, axis in (("x", ax.xaxis), ("y", ax.yaxis)):
            ticks = axis.get_major_ticks() + axis.get_minor_ticks()
            tick_labels.update(id(label) for tick in ticks for label in (tick.label1, tick.label2))
            # Tick labels are regenerated from the formatter on every draw,
            # so they are renamed there rather than on the text objects.
            if mapping:
                axis.set_major_formatter(_RenamedFormatter(axis.get_major_formatter(), mapping))
            if scale != 1.0 and ticks:
                size = ticks[0].label1.get_fontsize()
                ax.tick_params(axis=name, which="both", labelsize=size * scale)
    for text in fig.findobj(Text):
        if id(text) in tick_labels:
            continue
        original = text.get_text()
        if mapping and original:
            renamed = _rename(original, mapping)
            if renamed != original:
                text.set_text(renamed)
                if not renamed:
                    _hide(text)
        if scale != 1.0:
            text.set_fontsize(text.get_fontsize() * scale)
    if style.hide_figure_titles:
        for container in _containers(fig):
            if getattr(container, "_suptitle", None) is not None:
                _hide(container._suptitle)


def _png(fig: plt.Figure) -> bytes:
    style = _STYLE
    restyle = style.changes_rendering()
    if restyle:
        # Draw once so tick labels exist, record the default wording, then
        # restyle. The save below lays the figure out again around the new text.
        fig.canvas.draw()
        texts = _figure_texts(fig)
        _restyle(fig, style)
    output = io.BytesIO()
    fig.savefig(output, format="png", dpi=style.dpi, bbox_inches="tight", facecolor=BACKGROUND)
    if not restyle:
        texts = _figure_texts(fig)
    extras: dict[str, bytes] = {}
    for file_format in style.formats:
        if file_format == "png":
            continue
        buffer = io.BytesIO()
        fig.savefig(
            buffer, format=file_format, dpi=style.dpi, bbox_inches="tight",
            facecolor=BACKGROUND, **_SAVE_OPTIONS[file_format],
        )
        extras[file_format] = buffer.getvalue()
    plt.close(fig)
    return RenderedFigure(output.getvalue(), extras, texts)


def _new_figure(width: float, height: float) -> plt.Figure:
    fig = plt.figure(figsize=(width, height), layout="constrained")
    fig.patch.set_facecolor(BACKGROUND)
    return fig




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


def _draw_empty(container, title: str, message: str) -> None:
    ax = container.subplots(1, 1, squeeze=False)[0, 0]
    ax.axis("off")
    ax.text(0.5, 0.58, title, ha="center", va="center", fontsize=17)
    ax.text(0.5, 0.42, message, ha="center", va="center", fontsize=11, color="#61716D")


def _empty_figure(title: str, message: str) -> bytes:
    fig = _new_figure(1 * 9, 1 * 4.5)
    _draw_empty(fig, title, message)
    return _png(fig)


def _tracks(bundle, prefer_long: bool) -> list:
    result = []
    for group in bundle.groups:
        result.extend(bundle.tracks_for(group, prefer_long))
    return result


def _hide_unused(axes, used: int) -> None:
    for ax in axes.flat[used:]:
        ax.set_visible(False)


def _short_dataset(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# Data layer: put one kind of panel onto given axes.
# ---------------------------------------------------------------------------


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


def _time_axis(bundle):
    """Frames, or hours since the first imaged frame once the interval is known."""
    interval = bundle.parameters.frame_interval_minutes
    if interval is None:
        return "Frame", lambda frames: frames
    starts = [track.raw.timeframe[0] for track in bundle.tracks if len(track.raw.timeframe)]
    origin = min(starts) if starts else 0
    return "Time (h)", lambda frames: (np.asarray(frames, dtype=float) - origin) * interval / 60.0


def _distance_unit(bundle) -> str:
    return "µm" if bundle.parameters.microns_per_pixel is not None else "px"


def _unit_label(bundle, label: str) -> str:
    return unit_label(label, bundle.parameters.microns_per_pixel, bundle.parameters.frame_interval_minutes)


def _plot_temporal_axes(bundle, axes, prefer_long: bool, metrics, linewidth: float = 1.4) -> None:
    time_label, to_time = _time_axis(bundle)
    for ax, (attribute, label) in zip(axes, metrics):
        for index, group in enumerate(bundle.groups):
            frames, mean, sem = _temporal_values(list(bundle.tracks_for(group, prefer_long)), attribute)
            if not len(frames):
                continue
            color = COLORS[index % len(COLORS)]
            frames = to_time(frames)
            ax.plot(frames, mean, color=color, linestyle=LINE_STYLES[index % len(LINE_STYLES)], linewidth=linewidth, label=group)
            ax.fill_between(frames, mean - sem, mean + sem, color=color, alpha=0.12)
        ax.set_title(label, fontsize=9)
        ax.set_xlabel(time_label, fontsize=8)
        _style(ax)


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

    Groups can hold different numbers of samples, so a group total would let
    the group with more samples look like it has more cells. Each panel shows
    the per-sample mean with its SEM; a sample whose tracks are all too short
    still counts, as zero.
    """
    minimum = bundle.parameters.appearance_min_observations
    time_label, to_time = _time_axis(bundle)
    starts = [track.raw.timeframe[0] for track in bundle.tracks if len(track.raw.timeframe)]
    ends = [track.raw.timeframe[-1] for track in bundle.tracks if len(track.raw.timeframe)]
    first_imaged = min(starts) if starts else 0
    last_countable = (max(ends) if ends else 0) - (minimum - 1)
    for index, group in enumerate(bundle.groups):
        group_tracks = bundle.tracks_for(group, prefer_long=False)
        samples = sorted({track.raw.dataset for track in group_tracks})
        tracks = [track for track in group_tracks if len(track.raw.timeframe) >= minimum]
        if not tracks:
            continue
        start = min(track.raw.timeframe[0] for track in tracks)
        end = max(start, min(max(track.raw.timeframe[-1] for track in tracks), last_countable))
        frames = np.arange(start, end + 1)
        per_sample = np.zeros((3, len(samples), len(frames)))
        for row, sample in enumerate(samples):
            spans = [(track.raw.timeframe[0], track.raw.timeframe[-1]) for track in tracks if track.raw.dataset == sample]
            new = np.asarray([sum(first == frame for first, _last in spans) for frame in frames], dtype=float)
            per_sample[0, row] = np.cumsum(new)
            per_sample[1, row] = [sum(first <= frame <= last for first, last in spans) for frame in frames]
            per_sample[2, row] = new
        mean = per_sample.mean(axis=1)
        sem = per_sample.std(axis=1, ddof=1) / math.sqrt(len(samples)) if len(samples) > 1 else np.zeros_like(mean)
        arriving = frames > first_imaged
        panels = (
            (np.ones_like(arriving), "Cumulative appeared"),
            (np.ones_like(arriving), "Active tracks"),
            (arriving, "New tracks (after first frame)"),
        )
        color = COLORS[index % len(COLORS)]
        for panel, (ax, (shown, label)) in enumerate(zip(axes, panels)):
            x_values, values, spread = to_time(frames[shown]), mean[panel][shown], sem[panel][shown]
            ax.plot(x_values, values, color=color, linestyle=LINE_STYLES[index % len(LINE_STYLES)], linewidth=1.8, label=group)
            ax.fill_between(x_values, values - spread, values + spread, color=color, alpha=0.12, linewidth=0)
            ax.set_title(label, fontsize=11)
            ax.set_xlabel(time_label, fontsize=9)
            ax.set_ylabel("Tracks per sample", fontsize=9)
            _style(ax)


def _appearance_subtitle(bundle) -> str:
    return f"mean ± SEM per sample; tracks with at least {bundle.parameters.appearance_min_observations} observations"


def _msd_labels(bundle) -> tuple[str, str]:
    return (
        "Time lag (min)" if bundle.parameters.frame_interval_minutes is not None else "Frame lag",
        "MSD (µm²)" if bundle.parameters.microns_per_pixel is not None else "MSD (px²)",
    )


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
    dataset = _short_dataset(track.raw.dataset) if _STYLE.short_dataset_names else track.raw.dataset
    ax.set_title(f"{dataset}\nTrack {track.raw.track_id}", fontsize=title_size)
    _style(ax)


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
    axes[0].set_xlabel(f"Delta x ({_distance_unit(bundle)})")
    axes[0].set_ylabel(f"Delta y ({_distance_unit(bundle)})")
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
    axes[2].set_xlabel(f"Delta x ({_distance_unit(bundle)})")
    _style(axes[2])


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
        (key, _unit_label(bundle, SUMMARY_METRICS[key]))
        for key in bundle.parameters.summary_metrics
        if key != "turning_angle_std"
    ]


def _temporal_metrics(bundle) -> list[tuple[str, str]]:
    return [(key, _unit_label(bundle, TEMPORAL_METRICS[key])) for key in bundle.parameters.temporal_metrics]


# ---------------------------------------------------------------------------
# Layout layer: each ``_draw_*`` fills a container (a figure or a subfigure),
# and each block knows the size it is drawn at on its own.
# ---------------------------------------------------------------------------


def _draw_temporal(container, bundle, title: str, prefer_long: bool) -> None:
    metrics = _temporal_metrics(bundle)
    axes = container.subplots(_grid_rows(len(metrics)), GRID_COLUMNS, squeeze=False)
    container.suptitle(title, fontsize=15)
    _plot_temporal_axes(bundle, axes.flat, prefer_long, metrics, linewidth=1.7)
    _hide_unused(axes, len(metrics))
    handles, labels = axes.flat[0].get_legend_handles_labels()
    if handles:
        container.legend(handles, labels, loc="outside lower center", ncol=min(6, len(labels)), frameon=False, fontsize=9)


def _draw_cell_appearance(container, bundle) -> None:
    axes = container.subplots(1, 3, squeeze=False)
    container.suptitle(f"Cell appearance over time\n({_appearance_subtitle(bundle)})", fontsize=14)
    _plot_cell_appearance_axes(bundle, axes[0])
    handles, labels = axes[0, 0].get_legend_handles_labels()
    if handles:
        axes[0, 0].legend(handles, labels, frameon=False, fontsize=8)


def _draw_classification(container, bundle) -> None:
    axes = container.subplots(1, 3, squeeze=False)
    container.suptitle("Trajectory classification", fontsize=14)
    _plot_classification_axes(bundle, axes[0])


def _turning_distributions(bundle) -> list:
    return [turning_angle_distribution(bundle, group, prefer_long=True) for group in bundle.groups]


def _draw_turning_angle_distribution(container, bundle) -> None:
    """Step turning angles, histogrammed per dataset and averaged with a 95% CI."""
    distributions = _turning_distributions(bundle)
    if not any(distributions):
        _draw_empty(container, "Step turning-angle distribution", "No valid turning-angle values are available.")
        return
    ax = container.subplots(1, 1, squeeze=False)[0, 0]
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


def _draw_msd(container, bundle, title: str, prefer_long: bool) -> None:
    axes = container.subplots(1, 3, squeeze=False)
    container.suptitle(title, fontsize=14)
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


def _draw_msd_summary(container, bundle) -> None:
    ax = container.subplots(1, 1, squeeze=False)[0, 0]
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


def _draw_parameter_distributions(container, bundle) -> None:
    metrics = _parameter_metrics(bundle)
    axes = container.subplots(_grid_rows(len(metrics)), GRID_COLUMNS, squeeze=False)
    container.suptitle("Dataset-level parameter distributions", fontsize=14)
    for ax, (attribute, title) in zip(axes.flat, metrics):
        _plot_metric_distribution(ax, bundle, attribute, title)
    _hide_unused(axes, len(metrics))


def _draw_cell_and_classification(container, bundle) -> None:
    axes = container.subplots(2, 3, squeeze=False)
    container.suptitle("Cell appearance and trajectory classification", fontsize=15)
    _plot_cell_appearance_axes(bundle, axes[0])
    _plot_classification_axes(bundle, axes[1])
    handles, labels = axes[0, 0].get_legend_handles_labels()
    if handles:
        container.legend(handles, labels, loc="outside lower center", ncol=len(labels), frameon=False, fontsize=9)


def _paper_02_rows(bundle) -> int:
    return max(_grid_rows(len(_temporal_metrics(bundle))), _grid_rows(len(_parameter_metrics(bundle))))


def _draw_temporal_and_parameters(container, bundle) -> None:
    """Left and right grids share one layout so panel i matches panel i."""
    temporal_metrics = _temporal_metrics(bundle)
    parameter_metrics = _parameter_metrics(bundle)
    rows = _paper_02_rows(bundle)
    subfigures = container.subfigures(1, 2, wspace=0.03)
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
    container.suptitle("Temporal behavior and migration parameters", fontsize=17)


def _draw_group_trajectories_all(container, bundle) -> None:
    rows = len(bundle.groups)
    limit = _bundle_trajectory_limit(bundle)
    subfigures = container.subfigures(rows, 1, squeeze=False)
    for row in range(rows):
        subfigure = subfigures[row, 0]
        row_axes = [
            subfigure.add_subplot(1, 3, 1),
            subfigure.add_subplot(1, 3, 2, projection="polar"),
            subfigure.add_subplot(1, 3, 3),
        ]
        _plot_group_trajectory_axes(bundle, row, row_axes, limit)
        subfigure.suptitle(bundle.groups[row], fontsize=13)
    container.suptitle(f"Group trajectory analysis (shared scale ±{limit:.0f} {_distance_unit(bundle)})", fontsize=15)


def _representatives_grid(bundle) -> tuple[bool, int, int]:
    count = bundle.parameters.representatives_per_type
    horizontal = len(bundle.groups) == 2
    rows = 3 if horizontal else 3 * len(bundle.groups)
    columns = count * len(bundle.groups) if horizontal else count
    return horizontal, rows, columns


def _draw_representatives_all(container, bundle) -> None:
    count = bundle.parameters.representatives_per_type
    limit = _bundle_trajectory_limit(bundle)
    horizontal, rows, columns = _representatives_grid(bundle)
    axes = container.subplots(rows, columns, squeeze=False)
    container.suptitle(f"Representative trajectories (shared scale ±{limit:.0f} {_distance_unit(bundle)})", fontsize=15)
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


@dataclass(frozen=True)
class Block:
    """One panel layout: the size it is drawn at alone, and how to draw it."""

    size: Callable[[object], tuple[float, float]]
    draw: Callable[[object, object], None]


def _turning_size(bundle) -> tuple[float, float]:
    return (1 * 8.2, 1 * 4.8) if any(_turning_distributions(bundle)) else (1 * 9, 1 * 4.5)


def _representatives_size(bundle) -> tuple[float, float]:
    _horizontal, rows, columns = _representatives_grid(bundle)
    return columns * 2.7, rows * 2.6


# Sizes are written exactly as the standalone figures always computed them,
# so the default renders keep their exact pixels.
BLOCKS: dict[str, Block] = {
    "cell_appearance": Block(lambda b: (3 * 4.6, 1 * 4), _draw_cell_appearance),
    "classification": Block(lambda b: (3 * 4.8, 1 * 4.2), _draw_classification),
    "msd_long": Block(
        lambda b: (3 * 4.8, 1 * 4.2),
        lambda c, b: _draw_msd(c, b, "Mean square displacement | long tracks", True),
    ),
    "msd_all": Block(
        lambda b: (3 * 4.8, 1 * 4.2),
        lambda c, b: _draw_msd(c, b, "Mean square displacement | all tracks", False),
    ),
    "msd_summary": Block(lambda b: (1 * 8.2, 1 * 4.8), _draw_msd_summary),
    "turning_angle_distribution": Block(_turning_size, _draw_turning_angle_distribution),
    "temporal_long": Block(
        lambda b: (GRID_COLUMNS * 4.3, _grid_rows(len(_temporal_metrics(b))) * 3.2),
        lambda c, b: _draw_temporal(c, b, "Temporal trends | long tracks", True),
    ),
    "temporal_all": Block(
        lambda b: (GRID_COLUMNS * 4.3, _grid_rows(len(_temporal_metrics(b))) * 3.2),
        lambda c, b: _draw_temporal(c, b, "Temporal trends | all tracks", False),
    ),
    "parameter_distributions": Block(
        lambda b: (GRID_COLUMNS * 4.2, _grid_rows(len(_parameter_metrics(b))) * 3.4),
        _draw_parameter_distributions,
    ),
    "group_trajectories": Block(lambda b: (15, 4.8 * len(b.groups)), _draw_group_trajectories_all),
    "representatives": Block(_representatives_size, _draw_representatives_all),
    "cell_and_classification": Block(lambda b: (3 * 4.8, 2 * 4.0), _draw_cell_and_classification),
    "temporal_and_parameters": Block(
        lambda b: (GRID_COLUMNS * 2 * 4.2, _paper_02_rows(b) * 4.4),
        _draw_temporal_and_parameters,
    ),
}
assert set(BLOCKS) == set(BLOCK_LABELS), "style.BLOCKS and figures.BLOCKS must list the same panels"


def _render_block(key: str, bundle) -> bytes:
    block = BLOCKS[key]
    fig = _new_figure(*block.size(bundle))
    block.draw(fig, bundle)
    return _png(fig)


# ---------------------------------------------------------------------------
# Standalone figures.
# ---------------------------------------------------------------------------


def _plot_temporal(bundle, title: str, prefer_long: bool, metrics=None) -> bytes:
    rows = _grid_rows(len(_temporal_metrics(bundle)))
    fig = _new_figure(GRID_COLUMNS * 4.3, rows * 3.2)
    _draw_temporal(fig, bundle, title, prefer_long)
    return _png(fig)


def figure_cell_appearance(bundle) -> bytes:
    return _render_block("cell_appearance", bundle)


def figure_classification(bundle) -> bytes:
    return _render_block("classification", bundle)


def figure_turning_angle_distribution(bundle) -> bytes:
    return _render_block("turning_angle_distribution", bundle)


def _plot_msd(bundle, title: str, prefer_long: bool) -> bytes:
    fig = _new_figure(3 * 4.8, 1 * 4.2)
    _draw_msd(fig, bundle, title, prefer_long)
    return _png(fig)


def figure_msd_summary(bundle) -> bytes:
    return _render_block("msd_summary", bundle)


def figure_parameter_distributions(bundle) -> bytes:
    return _render_block("parameter_distributions", bundle)


def figure_paper_cell_and_classification(bundle) -> bytes:
    return _render_block("cell_and_classification", bundle)


def figure_paper_temporal_and_parameters(bundle) -> bytes:
    return _render_block("temporal_and_parameters", bundle)


def figure_paper_group_trajectories(bundle) -> bytes:
    return _render_block("group_trajectories", bundle)


def figure_supplementary_representatives(bundle) -> bytes:
    return _render_block("representatives", bundle)


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
    fig.suptitle(f"{group}: representative trajectories (shared scale ±{limit:.0f} {_distance_unit(bundle)})", fontsize=14)
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


def figure_group_trajectories(bundle, group_index: int) -> bytes:
    if group_index >= len(bundle.groups):
        return _empty_figure("Group trajectory analysis", f"Group {group_index + 1} was not selected.")
    group = bundle.groups[group_index]
    if not bundle.tracks_for(group, True):
        return _empty_figure(f"{group}: trajectory analysis", "No valid trajectories are available.")
    limit = _bundle_trajectory_limit(bundle)
    fig = plt.figure(figsize=(15, 4.6), facecolor=BACKGROUND, layout="constrained")
    axes = [fig.add_subplot(1, 3, 1), fig.add_subplot(1, 3, 2, projection="polar"), fig.add_subplot(1, 3, 3)]
    fig.suptitle(f"{group}: trajectory analysis (shared scale ±{limit:.0f} {_distance_unit(bundle)})", fontsize=14)
    _plot_group_trajectory_axes(bundle, group_index, axes, limit)
    return _png(fig)


# ---------------------------------------------------------------------------
# Composite figures.
# ---------------------------------------------------------------------------


def composite_layout(composite: CompositeFigure, bundle) -> tuple[tuple[float, float], list[float], list[list[float]]]:
    """Figure size, column widths and block heights, all in inches.

    Every block keeps its own aspect ratio and is drawn at least at its
    standalone size, so nothing gets more crowded than the figure it comes
    from. Columns are widened until they all reach the same height.
    """
    sizes = [[BLOCKS[key].size(bundle) for key in column] for column in composite.columns]
    per_width = [sum(height / width for width, height in column) for column in sizes]
    height = max(max(width for width, _height in column) * ratio for column, ratio in zip(sizes, per_width))
    widths = [height / ratio for ratio in per_width]
    heights = [[width * block_height / block_width for block_width, block_height in column] for width, column in zip(widths, sizes)]
    return (sum(widths), height), widths, heights


def figure_composite(bundle, composite: CompositeFigure) -> bytes:
    (width, height), widths, heights = composite_layout(composite, bundle)
    fig = _new_figure(width, height)
    columns = fig.subfigures(1, len(widths), squeeze=False, width_ratios=widths)[0]
    letters = iter(ascii_uppercase)
    for column_figure, keys, column_heights in zip(columns, composite.columns, heights):
        cells = column_figure.subfigures(len(keys), 1, squeeze=False, height_ratios=column_heights)[:, 0]
        for cell, key in zip(cells, keys):
            BLOCKS[key].draw(cell, bundle)
            if composite.panel_letters:
                cell.text(0.004, 0.996, next(letters, ""), ha="left", va="top", fontsize=20, fontweight="bold")
    if composite.title:
        fig.suptitle(composite.title, fontsize=18)
    rendered = _png(fig)
    rendered.label = composite.name
    return rendered


def composite_filename(index: int, composite: CompositeFigure) -> str:
    return f"composite_{index + 1:02d}_{slug(composite.name)}.png"


# ---------------------------------------------------------------------------
# Everything for one analysis.
# ---------------------------------------------------------------------------


def _slug(group: str, index: int) -> str:
    return slug(group, f"group-{index + 1}")


def render_all_figures(bundle, style: FigureStyle | None = None) -> dict[str, bytes]:
    """Render configured figures from one shared analysis bundle.

    Filenames are numbered by the canonical FIGURE_TYPES order rather than by
    render order, so a given figure keeps the same prefix regardless of which
    other figures were selected. Composite figures from the style follow.
    """
    with figure_style(style, tuple(bundle.groups)) as active:
        return _render_all(bundle, active)


def _render_all(bundle, style: FigureStyle) -> dict[str, bytes]:
    selected = set(bundle.parameters.figure_types)
    order = {key: index + 1 for index, key in enumerate(FIGURE_TYPES)}
    rendered: dict[str, bytes] = {}

    def add(key: str, filename: str, content: bytes) -> None:
        rendered[f"figure_{order[key]:02d}_{filename}.png"] = content

    if "cell_appearance" in selected:
        add("cell_appearance", "cell_appearance", figure_cell_appearance(bundle))
    if "temporal_long" in selected:
        add("temporal_long", "temporal_long", _plot_temporal(bundle, "Temporal trends | long tracks", True))
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
        add("temporal_all", "temporal_all", _plot_temporal(bundle, "Temporal trends | all tracks", False))
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
    for index, composite in enumerate(style.composites):
        rendered[composite_filename(index, composite)] = figure_composite(bundle, composite)
    return rendered
