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
import textwrap
import warnings
from typing import Callable, Iterator

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib import patheffects, transforms
from matplotlib.colors import to_rgba
from matplotlib.lines import Line2D
from matplotlib.text import Text
from matplotlib.ticker import Formatter, MaxNLocator
import numpy as np
from PIL import Image


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
STAT_CAPTION = "stat-caption"
STAT_MARK = "stat-mark"
STAT_BRACKET = "stat-bracket"
# Dataset points on distribution panels; the axes are widened so none is cut.
STRIP_POINTS = "strip-points"
MEDIAN_COLOR = "#33423E"
WHISKER_COLOR = "#7A8884"
# Shortest plot area a squeezed print layout may leave, in millimetres.
MIN_PANEL_MM = 12

plt.rcParams.update({
    "font.family": "sans-serif",
    # Arial or a metric twin first (journals ask for it); the CJK fonts cover
    # group names written in Chinese.
    "font.sans-serif": ["Arial", "Helvetica", "Liberation Sans", "Arial Unicode MS", "Noto Sans CJK SC", "PingFang SC", "DejaVu Sans"],
    "mathtext.default": "regular",
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
    # Text that overlaps other text at print size; only checked when a print
    # width is set.
    warnings: tuple[str, ...] = ()

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


# ---------------------------------------------------------------------------
# Print size: lay a figure out at the size it will be printed.
# ---------------------------------------------------------------------------


def _drawn_texts(fig: plt.Figure) -> list[Text]:
    """Visible text as drawn: tick labels only for ticks inside the view."""
    tick_labels: set[int] = set()
    drawn: list[Text] = []
    for ax in fig.get_axes():
        for axis in ax._axis_map.values():
            ticks = axis.get_major_ticks() + axis.get_minor_ticks()
            tick_labels.update(id(label) for tick in ticks for label in (tick.label1, tick.label2))
            if ax.get_visible() and axis.get_visible():
                for tick in axis._update_ticks():
                    drawn.extend(label for label in (tick.label1, tick.label2) if label.get_visible())
    drawn.extend(text for text in fig.findobj(Text) if id(text) not in tick_labels)
    return [text for text in drawn if text.get_visible() and text.get_text().strip() and _shown(text)]


def _shown(text: Text) -> bool:
    axes = text.axes
    return axes is None or axes.get_visible()


def _wrap_to(text: Text, available: float, renderer, vertical: bool = False) -> bool:
    box = text.get_window_extent(renderer)
    used = box.height if vertical else box.width
    if available <= 0 or used <= available * 1.01:
        return False
    ratio = available / used * 0.95
    lines = []
    for line in text.get_text().split("\n"):
        chars = max(6, int(len(line) * ratio))
        head, unit = line.rsplit(" (", 1) if " (" in line else (line, "")
        if len(line) > chars and unit and len(head) <= chars and len(unit) + 1 <= chars:
            # Keep a unit in brackets whole: "Shape change rate" / "(per 10 min)".
            lines.append(f"{head}\n({unit}")
        else:
            lines.append(textwrap.fill(line, chars, break_long_words=False, break_on_hyphens=False) if len(line) > chars else line)
    wrapped = "\n".join(lines)
    if wrapped == text.get_text():
        return False
    text.set_text(wrapped)
    return True


def _slot(ax: plt.Axes, renderer) -> tuple[float, float]:
    """Width and height, in pixels, of the grid cell an axes was placed in.

    Wrapping against the cell rather than the axes keeps one squeezed panel
    from making its own title wrap ever narrower.
    """
    spec = ax.get_subplotspec()
    if spec is None:
        box = ax.get_window_extent(renderer)
        return box.width, box.height
    cell = spec.get_position(ax.figure)
    parent = ax.figure.bbox
    return cell.width * parent.width, cell.height * parent.height


def _overlapping(texts: list[Text], renderer, gap_points: float = 0.0) -> bool:
    """True when any two texts overlap or come closer than ``gap_points``."""
    pad = gap_points / 2 * texts[0].figure.dpi / 72 if texts else 0.0
    boxes = [text.get_window_extent(renderer).padded(pad) for text in texts if text.get_visible() and text.get_text()]
    return any(first.overlaps(second) for index, first in enumerate(boxes) for second in boxes[index + 1:])


def _lift_stat_captions(fig: plt.Figure) -> None:
    """Move the test summary from inside a panel to just above it.

    Inside the panel it has one line of room; above it, it can wrap.
    """
    for ax in fig.get_axes():
        captions = [text for text in ax.texts if text.get_gid() == STAT_CAPTION and text.get_visible()]
        for caption in captions:
            caption.set_transform(transforms.offset_copy(ax.transAxes, fig=ax.figure, y=2, units="points"))
            caption.set_position((0.5, 1.0))
            caption.set_verticalalignment("bottom")


def _wrap_long_text(fig: plt.Figure) -> bool:
    """Wrap text wider than its panel and turn crowded tick labels."""
    renderer = fig.canvas.get_renderer()
    changed = False
    for ax in fig.get_axes():
        if not ax.get_visible():
            continue
        width, height = _slot(ax, renderer)
        for text in (ax.title, ax._left_title, ax._right_title, ax.xaxis.label):
            if text.get_visible() and text.get_text():
                changed |= _wrap_to(text, width * 0.9, renderer)
        if ax.yaxis.label.get_visible() and ax.yaxis.label.get_text():
            changed |= _wrap_to(ax.yaxis.label, height * 0.9, renderer, vertical=True)
        captions = [text for text in ax.texts if text.get_gid() == STAT_CAPTION and text.get_visible()]
        for caption in captions:
            changed |= _wrap_to(caption, width * 0.9, renderer)
        if captions and ax.title.get_text():
            lines = sum(caption.get_text().count("\n") + 1 for caption in captions)
            pad = 4 + lines * captions[0].get_fontsize() * 1.25
            if abs(ax.title.get_position()[1] - 1.0) < 1e-9 and getattr(ax, "_caption_pad", None) != pad:
                ax._caption_pad = pad
                ax.set_title(ax.title.get_text(), pad=pad, fontsize=ax.title.get_fontsize())
                changed = True
        labels = [tick.label1 for tick in ax.xaxis._update_ticks() if tick.label1.get_visible() and tick.label1.get_text()]
        if len(labels) > 1 and ax.name != "polar" and _overlapping(labels, renderer, gap_points=6):
            changed |= _uncrowd_ticks(ax, labels)
    for container in _containers(fig):
        title = getattr(container, "_suptitle", None)
        if title is not None and title.get_visible():
            changed |= _wrap_to(title, container.bbox.width * 0.95, renderer)
    return changed


def _uncrowd_ticks(ax: plt.Axes, labels: list[Text]) -> bool:
    """Make crowded x tick labels fit, gentlest step first.

    Numbers get fewer ticks. Group names are first broken onto two lines,
    then turned 45°; never 90°, which is hard to read on paper.
    """
    if not any(_is_label(label.get_text()) for label in labels):
        if ax.get_xscale() == "linear" and not isinstance(ax.xaxis.get_major_locator(), MaxNLocator):
            ax.xaxis.set_major_locator(MaxNLocator(nbins=3))
            return True
        return False
    if not getattr(ax, "_ticks_wrapped", False):
        ax._ticks_wrapped = True
        texts = [label.get_text() for label in labels]
        wrapped = [
            "\n".join(
                textwrap.fill(line, max(4, math.ceil(len(line) / 2)), break_long_words=False) if " " in line else line
                for line in text.split("\n")
            )
            for text in texts
        ]
        if wrapped != texts:
            ax.set_xticks([label.get_position()[0] for label in labels], wrapped)
            return True
    if labels[0].get_rotation() < 45:
        ax.tick_params(axis="x", labelrotation=45)
        for label in ax.get_xticklabels():
            label.set_horizontalalignment("right")
            label.set_rotation_mode("anchor")
        return True
    return False


def _print_size(size: float, base: float) -> float:
    """Journal-style sizes: on paper the screen hierarchy collapses to a few steps.

    Tick labels and annotations print at ``base``, axis labels and panel titles
    one point larger, block titles two, figure titles three and panel letters
    four (bold, as drawn).
    """
    if size <= 8:
        return base
    if size <= 11:
        return base + 1
    if size <= 15:
        return base + 2
    if size <= 18:
        return base + 3
    return base + 4


def _outer_tick_labels(fig: plt.Figure) -> None:
    """Keep tick numbers only on the outer panels of a shared-scale grid.

    Small multiples drawn on one scale (representative tracks, centered
    trajectories) repeat the same numbers in every cell; at print width those
    numbers take most of each cell. A panel keeps its y numbers only when no
    panel with the same limits sits to its left in its row, and its x numbers
    only when none sits below it in its column.
    """
    groups: dict[tuple, list] = {}
    for ax in fig.get_axes():
        spec = ax.get_subplotspec()
        if spec is None or not ax.get_visible() or ax.name != "rectilinear":
            continue
        limits = tuple(np.round((*ax.get_xlim(), *ax.get_ylim()), 9))
        groups.setdefault((id(spec.get_gridspec()), limits), []).append((ax, spec.rowspan, spec.colspan))
    for members in groups.values():
        if len(members) < 3:
            continue
        for ax, rows, columns in members:
            left = any(r == rows and c.stop <= columns.start for _a, r, c in members)
            below = any(c == columns and r.start >= rows.stop for _a, r, c in members)
            if left:
                ax.tick_params(axis="y", which="both", labelleft=False)
            if below:
                ax.tick_params(axis="x", which="both", labelbottom=False)
                ax.set_xlabel("")


def _fit_to_print(fig: plt.Figure, style: FigureStyle) -> tuple[float, tuple[str, ...]]:
    """Lay the figure out at its printed width with journal text sizes.

    Returns the DPI to save with, so the image holds ``style.dpi`` pixels per
    inch at the print width, and notes for the author about the layout.
    """
    base = style.print_text_pt
    # Print sizes come from print_text_pt alone; undo the screen multiplier
    # so a style used for both does not print at the wrong size.
    scale = style.font_scale
    printed = style.print_width_mm / 25.4
    width, height = fig.get_size_inches()
    for ax in fig.get_axes():
        for name, axis in (("x", ax.xaxis), ("y", ax.yaxis)):
            ticks = axis.get_major_ticks() + axis.get_minor_ticks()
            if ticks:
                ax.tick_params(axis=name, which="both", labelsize=_print_size(ticks[0].label1.get_fontsize() / scale, base))
    tick_labels = {
        id(label)
        for ax in fig.get_axes()
        for axis in (ax.xaxis, ax.yaxis)
        for tick in axis.get_major_ticks() + axis.get_minor_ticks()
        for label in (tick.label1, tick.label2)
    }
    for text in fig.findobj(Text):
        if id(text) not in tick_labels:
            text.set_fontsize(_print_size(text.get_fontsize() / scale, base))
    natural = height * printed / width
    limit = style.print_max_height_mm / 25.4
    _outer_tick_labels(fig)
    _lift_stat_captions(fig)
    collapsed = _lay_out(fig, printed, natural)
    notes: list[str] = []
    if natural > limit:
        # Squeezing a tall figure works for grids of plots, but text keeps its
        # size, so the data areas lose more than the page does and can end up
        # flat. Keep the shorter layout only while every panel stays at least
        # MIN_PANEL_MM tall, or loses little when it was already smaller.
        before = _panel_heights(fig)
        crowded = len(_text_overlaps(fig))
        squeezed = _lay_out(fig, printed, limit)
        after = _panel_heights(fig)
        squashed = any(
            after.get(key, 0) < MIN_PANEL_MM / 25.4 and after.get(key, 0) < 0.85 * value
            for key, value in before.items()
        )
        if squeezed or squashed or len(_text_overlaps(fig)) > crowded:
            _lay_out(fig, printed, natural)
            notes.append(
                f"Figure is {natural * 25.4:.0f} mm tall at {style.print_width_mm:g} mm wide, more than the "
                f"{style.print_max_height_mm:g} mm page limit; squeezing it would flatten panels. "
                "Move a panel to another figure, or lower the number of rows."
            )
        else:
            collapsed = False
    overlaps = _text_overlaps(fig)
    if collapsed or overlaps:
        notes.append(
            f"Some text does not fit at {style.print_width_mm:g} mm wide. Try a wider print width, "
            "fewer panels side by side, or shorter labels."
        )
    return float(style.dpi), tuple(notes) + overlaps


def _lay_out(fig: plt.Figure, width: float, height: float) -> bool:
    """Draw at this size, wrapping text until it settles; True if panels collapsed."""
    fig.set_size_inches(width, height)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for _attempt in range(4):
            fig.canvas.draw()
            if not _wrap_long_text(fig):
                break
        _fit_data_marks(fig)
        if _raise_for_marks(fig):
            fig.canvas.draw()
    return any("constrained_layout not applied" in str(item.message) for item in caught)


def _fit_data_marks(fig: plt.Figure) -> None:
    """Keep every data point and significance mark inside its axes.

    Points and stars keep their size in points while the axes are laid out
    in data units, so the room they need depends on the drawn panel height.
    Each call starts again from the range the data asked for
    (``ax.layout_free_ylim``), so laying a figure out at a new size never keeps
    room meant for the old one. Twice, because a new y range can change the
    tick labels and so the layout.
    """
    reset = False
    for ax in fig.get_axes():
        if not hasattr(ax, "layout_free_ylim"):
            ax.layout_free_ylim = ax.get_ylim()
        elif ax.get_ylim() != ax.layout_free_ylim:
            ax.set_ylim(*ax.layout_free_ylim)
            reset = True
    if reset:
        fig.canvas.draw()
    for _attempt in range(2):
        changed = False
        for ax in fig.get_axes():
            if ax.get_visible() and ax.name == "rectilinear" and ax.get_yscale() == "linear":
                changed |= _fit_markers(ax)
                changed |= _space_brackets(ax)
        if not changed:
            return
        fig.canvas.draw()


def _points_to_pixels(ax: plt.Axes, points: float) -> float:
    return points * ax.figure.dpi / 72


def _fit_markers(ax: plt.Axes) -> bool:
    """Widen the y range by the radius of the dataset points."""
    strips = [item for item in ax.collections if item.get_gid() == STRIP_POINTS and len(item.get_offsets())]
    height = ax.bbox.height
    if not strips or height <= 0:
        return False
    values = np.concatenate([np.asarray(item.get_offsets())[:, 1] for item in strips])
    values = values[np.isfinite(values)]
    if not len(values):
        return False
    radius = max(_points_to_pixels(ax, math.sqrt(float(np.max(item.get_sizes()))) / 2) for item in strips) + 1.5
    bottom, top = ax.get_ylim()
    original = (bottom, top)
    for _step in range(4):
        per_pixel = (top - bottom) / height
        bottom = min(bottom, float(values.min()) - radius * per_pixel)
        top = max(top, float(values.max()) + radius * per_pixel)
    if (bottom, top) == original:
        return False
    ax.set_ylim(bottom, top)
    return True


def _space_brackets(ax: plt.Axes) -> bool:
    """Lay significance brackets out in points above the data.

    Each level is one mark tall plus a small gap, so a star never touches the
    bracket above it whatever the panel height; the y range grows to hold them.
    """
    brackets = getattr(ax, "_brackets", None)
    height = ax.bbox.height
    if not brackets or height <= 0:
        return False
    renderer = ax.figure.canvas.get_renderer()
    data_top = brackets["data_maximum"]
    radius = max(
        (_points_to_pixels(ax, math.sqrt(float(np.max(item.get_sizes()))) / 2)
         for item in ax.collections if item.get_gid() == STRIP_POINTS and len(item.get_offsets())),
        default=0.0,
    )
    marks = brackets["items"]
    text_height = max((text.get_window_extent(renderer).height for _line, text, _level in marks), default=0.0)
    gap = _points_to_pixels(ax, 2.5)
    base = radius + gap + _points_to_pixels(ax, 2)
    step = text_height + gap + _points_to_pixels(ax, 1.5)
    levels = max((level for _line, _text, level in marks), default=-1) + 1
    stack = base + max(0, levels - 1) * step + text_height + gap
    caption = brackets.get("caption")
    if caption is not None and caption.get_visible() and caption.get_transform() == ax.transAxes:
        stack += caption.get_window_extent(renderer).height + gap
    bottom, top = ax.get_ylim()
    # Brackets never take more than 60 % of the panel.
    stack = min(stack, height * 0.6)
    needed = bottom + (data_top - bottom) * height / (height - stack)
    top = max(top, needed)
    per_pixel = (top - bottom) / height
    tick = 0.35 * text_height * per_pixel
    for line, text, level in marks:
        y = data_top + (base + level * step) * per_pixel
        line.set_ydata([y - tick, y, y, y - tick])
        text.set_y(y + 0.5 * per_pixel)
    changed = top != ax.get_ylim()[1]
    if changed:
        ax.set_ylim(bottom, top)
    return changed


def _raise_for_marks(fig: plt.Figure) -> bool:
    """Give significance marks the headroom they need at this panel height.

    Brackets are placed in data units but their stars keep a fixed size in
    points, so a shorter panel can push the top star out of the axes, where it
    is clipped. Raise the upper y limit just enough; the data stay as they are.
    """
    renderer = fig.canvas.get_renderer()
    changed = False
    for ax in fig.get_axes():
        marks = [text for text in ax.texts if text.get_gid() == STAT_MARK and text.get_visible()]
        height = ax.bbox.height
        if not marks or height <= 0:
            continue
        # Each mark sits on its bracket (va="bottom"), so its top is at
        # (y - bottom) / (top - bottom) of the axes plus its own height.
        bottom, top = ax.get_ylim()
        needed = top
        for text in marks:
            share = 1 - (text.get_window_extent(renderer).height + 2) / height
            if share > 0.2:
                needed = max(needed, bottom + (text.get_position()[1] - bottom) / share)
        if needed > top:
            ax.set_ylim(bottom, needed)
            changed = True
    return changed


def _panel_heights(fig: plt.Figure) -> dict[int, float]:
    # Drawn size in inches; get_position() is relative to a subfigure.
    return {id(ax): ax.bbox.height / fig.dpi for ax in fig.get_axes() if ax.get_visible()}


def _text_overlaps(fig: plt.Figure, limit: int = 8) -> tuple[str, ...]:
    renderer = fig.canvas.get_renderer()
    labels, boxes = [], []
    for text in _drawn_texts(fig):
        box = text.get_window_extent(renderer)
        if box.width > 1 and box.height > 1:
            labels.append(text.get_text().replace("\n", " "))
            boxes.append((box.x0, box.y0, box.x1, box.y1))
    if len(boxes) < 2:
        return ()
    b = np.asarray(boxes)
    # Overlaps of more than a pixel in both directions, each pair once.
    across = np.minimum(b[:, None, 2], b[None, :, 2]) - np.maximum(b[:, None, 0], b[None, :, 0])
    up = np.minimum(b[:, None, 3], b[None, :, 3]) - np.maximum(b[:, None, 1], b[None, :, 1])
    first, second = np.nonzero(np.triu((across > 1) & (up > 1), k=1))
    found = list(dict.fromkeys(f"'{labels[i]}' overlaps '{labels[j]}'" for i, j in zip(first, second)))
    if len(found) > limit:
        found = found[:limit] + [f"and {len(found) - limit} more"]
    return tuple(found)


def _png(fig: plt.Figure) -> bytes:
    style = _STYLE
    restyle = style.changes_rendering()
    printing = style.print_width_mm is not None
    if restyle or printing:
        # Draw once so tick labels exist, record the default wording, then
        # restyle. The save below lays the figure out again around the new text.
        fig.canvas.draw()
        texts = _figure_texts(fig)
        if restyle:
            _restyle(fig, style)
    dpi, notes = style.dpi, ()
    if printing:
        dpi, notes = _fit_to_print(fig, style)
    else:
        fig.canvas.draw()
        _fit_data_marks(fig)
    # Printed figures keep their canvas size: a tight crop would add padding
    # and move the width off the journal column.
    bbox = None if printing else "tight"
    output = io.BytesIO()
    fig.savefig(output, format="png", dpi=dpi, bbox_inches=bbox, facecolor=BACKGROUND)
    if not (restyle or printing):
        texts = _figure_texts(fig)
    # The background is opaque anyway; an alpha channel only trips up
    # journal upload checks. The TIFF is the same pixels.
    image = Image.open(io.BytesIO(output.getvalue())).convert("RGB")
    resolution = (style.dpi, style.dpi)
    png = io.BytesIO()
    image.save(png, format="PNG", dpi=resolution, optimize=False)
    extras: dict[str, bytes] = {}
    for file_format in style.formats:
        if file_format == "png":
            continue
        buffer = io.BytesIO()
        if file_format == "tiff":
            image.save(buffer, format="TIFF", compression="tiff_lzw", dpi=resolution)
        else:
            fig.savefig(buffer, format=file_format, dpi=dpi, bbox_inches=bbox, facecolor=BACKGROUND, **_SAVE_OPTIONS[file_format])
        extras[file_format] = buffer.getvalue()
    plt.close(fig)
    rendered = RenderedFigure(png.getvalue(), extras, texts)
    rendered.warnings = notes
    return rendered


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
    """Mean over tracks at each frame, with an SEM that treats samples as units.

    Tracks from one sample are not independent, so the band is the
    cluster-robust standard error of the mean with samples as clusters:
    SE² = k/(k-1) · Σ_s (S_s − n_s·mean)² / N², for k samples, where sample s
    has n_s values summing to S_s and N = Σ n_s. The line itself stays the
    plain mean over tracks; weighting every sample equally would let a sample
    with one track left at a frame move the line as much as one with fifty.
    Frames where fewer than half of the samples still have a track are left
    out (same rule as MSD).
    """
    sums: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
    samples = set()
    for track in tracks:
        values = getattr(track.raw, attribute)
        samples.add(track.raw.dataset)
        for frame, value in zip(track.raw.timeframe, values):
            if math.isfinite(value):
                cell = sums[int(frame)][track.raw.dataset]
                cell[0] += float(value)
                cell[1] += 1
    minimum = max(1, math.ceil(0.5 * len(samples)))
    frames, means, sem = [], [], []
    for frame in sorted(sums):
        per_sample = list(sums[frame].values())
        k = len(per_sample)
        if k < minimum:
            continue
        total = sum(n for _s, n in per_sample)
        mean = sum(s for s, _n in per_sample) / total
        spread = math.sqrt(k / (k - 1) * sum((s - n * mean) ** 2 for s, n in per_sample)) / total if k > 1 else 0.0
        frames.append(frame)
        means.append(mean)
        sem.append(spread)
    return np.asarray(frames, dtype=float), np.asarray(means, dtype=float), np.asarray(sem, dtype=float)


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
            (np.ones_like(arriving), "Cumulative tracks"),
            (np.ones_like(arriving), "Active tracks"),
            (arriving, "New tracks"),
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
        heights = _angle_histogram_heights(bundle, group, edges)
        if len(heights):
            maximum = max(maximum, float(np.max(heights)))
    return edges, maximum * 1.05


def _angle_histogram_heights(bundle, group: str, edges: np.ndarray) -> np.ndarray:
    """Tracks per angle bin, as a share of the group's tracks or as a count."""
    angles = [
        item.final_angle
        for item in bundle.summaries_for(group, True)
        if item.net_displacement > 0 and math.isfinite(item.final_angle)
    ]
    if not angles:
        return np.zeros(len(edges) - 1, dtype=float)
    counts, _ = np.histogram(angles, bins=edges)
    if _STYLE.angle_counts:
        return counts.astype(float)
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
    ax.scatter([0], [0], s=9, color="#17211F", zorder=3)
    ax.scatter([x[-1]], [y[-1]], s=12, color=TYPE_COLORS[trajectory_type], marker="D", edgecolors="#17211F", linewidths=0.4, zorder=3)
    _apply_trajectory_limits(ax, limit)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=3, symmetric=True))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=3, symmetric=True))
    dataset = _short_dataset(track.raw.dataset) if _STYLE.short_dataset_names else track.raw.dataset
    ax.set_title(f"{dataset}\nTrack {track.raw.track_id}", fontsize=title_size)
    _style(ax)


def _representative_key(container, axes) -> None:
    """What the colours and markers of a representative track mean."""
    markers = [
        Line2D([], [], color="#17211F", marker="o", linestyle="", markersize=4, label="Start"),
        Line2D([], [], color="#8B9895", marker="D", linestyle="", markersize=4, markeredgecolor="#17211F", markeredgewidth=0.4, label="End (colour = type)"),
    ]
    container.legend(handles=markers + _type_handles(), loc="outside upper center", ncol=5, frameon=False, fontsize=9)
    mappable = plt.cm.ScalarMappable(cmap="viridis", norm=matplotlib.colors.Normalize(0, 1))
    bar = container.colorbar(mappable, ax=axes, location="bottom", shrink=0.35, aspect=30, pad=0.02)
    bar.set_ticks([0, 1], labels=["start", "end"])
    bar.set_label("Track progress", fontsize=9)
    bar.ax.tick_params(labelsize=8)
    bar.outline.set_linewidth(0.5)


def _type_handles(linewidth: float = 1.6) -> list[Line2D]:
    return [Line2D([], [], color=TYPE_COLORS[name], linewidth=linewidth, label=name) for name in ("Random", "Mixed", "Directed")]


def _plot_group_trajectory_axes(bundle, group_index: int, axes, limit: float, titles: bool = True) -> None:
    group = bundle.groups[group_index]
    unit = _distance_unit(bundle)
    tracks = list(bundle.tracks_for(group, True))
    summaries = list(bundle.summaries_for(group, True))
    summary_by_key = {(item.dataset, item.track_id): item for item in summaries}
    for track in tracks:
        summary = summary_by_key.get((track.raw.dataset, track.raw.track_id))
        trajectory_type = summary.trajectory_type if summary else "Mixed"
        x, y = _trajectory_xy(track)
        axes[0].plot(x, y, color=TYPE_COLORS[trajectory_type], linewidth=0.8, alpha=0.35)
    _apply_trajectory_limits(axes[0], limit)
    axes[0].set_xlabel(f"Δx ({unit})")
    axes[0].set_ylabel(f"Δy ({unit})")
    angle_edges, radial_limit = _angle_histogram_scale(bundle)
    percentages = _angle_histogram_heights(bundle, group, angle_edges)
    centers = (angle_edges[:-1] + angle_edges[1:]) / 2
    axes[1].bar(
        centers,
        percentages,
        width=np.diff(angle_edges),
        color=_STYLE.angle_plot_color or COLORS[group_index % len(COLORS)],
        alpha=0.78,
    )
    axes[1].set_ylim(0, radial_limit)
    axes[1].yaxis.set_major_locator(MaxNLocator(nbins=3, min_n_ticks=2, integer=_STYLE.angle_counts))
    suffix = "" if _STYLE.angle_counts else "%"
    axes[1].yaxis.set_major_formatter(lambda value, _position: f"{value:g}{suffix}")
    # The radial numbers go along the quietest sector (a bar and its two
    # neighbours, since the text is wider than one bar), with a white halo
    # where they still cross a bar.
    if len(percentages):
        sector = percentages + np.roll(percentages, 1) + np.roll(percentages, -1)
        axes[1].set_rlabel_position(math.degrees(centers[int(np.argmin(sector))]) % 360)
    for tick in axes[1].yaxis.get_major_ticks(6):
        tick.label1.set_path_effects([patheffects.withStroke(linewidth=2.2, foreground=BACKGROUND)])
    axes[1].spines["polar"].set_linewidth(0.8)
    axes[1].spines["polar"].set_color("#8B9895")
    axes[1].tick_params(labelsize=7, colors="#53635F")
    # Four compass labels close to the circle: the 45° ones and the default
    # padding took a third of each row's height without adding information.
    axes[1].set_thetagrids([0, 90, 180, 270], labels=["0°", "90°", "180°", "270°"])
    axes[1].tick_params(axis="x", pad=1)
    axes[1].set_facecolor(BACKGROUND)
    representatives = _pick_representatives(bundle, group, 3)
    for trajectory_type, selected in representatives.items():
        for track in selected:
            x, y = _trajectory_xy(track)
            axes[2].plot(x, y, color=TYPE_COLORS[trajectory_type], linewidth=1.2, alpha=0.85)
    _apply_trajectory_limits(axes[2], limit)
    axes[2].set_xlabel(f"Δx ({unit})")
    if titles:
        axes[0].set_title("Centered trajectories")
        axes[1].set_title("Final displacement angle", fontsize=10)
        axes[2].set_title("Representative tracks")
    for ax in (axes[0], axes[2]):
        ax.xaxis.set_major_locator(MaxNLocator(nbins=3, symmetric=True))
        ax.yaxis.set_major_locator(MaxNLocator(nbins=3, symmetric=True))
        _style(ax)


def _row_label(ax: plt.Axes, label: str) -> None:
    """A group name left of a row, outside the y label, kept by hide_figure_titles."""
    ax.annotate(
        label, xy=(0, 0.5), xycoords=ax.yaxis.label, xytext=(-6, 0), textcoords="offset points",
        ha="right", va="center", rotation=90, fontsize=11, fontweight="bold",
    )


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
    # Kept so the brackets can be re-spaced in points once the panel size is
    # known (_space_brackets); these data-unit positions are a first guess.
    ax._brackets = {"data_maximum": data_maximum, "items": [], "caption": None}
    for level, (first, second, label) in enumerate(ordered):
        height = base + level * step
        (line,) = ax.plot(
            [first, first, second, second],
            [height - step * 0.2, height, height, height - step * 0.2],
            color="#53635F", linewidth=0.8, clip_on=True, gid=STAT_BRACKET,
        )
        text = ax.text((first + second) / 2, height + step * 0.04, label, ha="center", va="bottom", fontsize=8, clip_on=True, gid=STAT_MARK)
        ax._brackets["items"].append((line, text, level))
    if overall:
        ax._brackets["caption"] = ax.text(
            0.5, 0.985, overall, transform=ax.transAxes,
            ha="center", va="top", fontsize=7, color="#61716D", gid=STAT_CAPTION,
        )


def _metric_results(bundle, metric: str):
    return statistical_results(bundle, [metric])


def _p_text(p_value: float) -> str:
    return "p < 0.001" if p_value < 0.001 else f"p = {p_value:.3f}"


def stat_summary(result) -> str:
    """The omnibus or two-group test as a caption would write it."""
    if result.test == "Mann-Whitney U":
        if result.p_value is None:
            return "Mann–Whitney U: NA"
        return f"Mann–Whitney U, {_p_text(result.p_value)}, $r_{{rb}}$ = {result.effect_size:.2f}"
    if result.p_value is None:
        return "Kruskal–Wallis: NA"
    return f"Kruskal–Wallis, {_p_text(result.p_value)}, ε² = {result.effect_size:.2f}"


def _box_and_points(ax: plt.Axes, values: list[list[float]]) -> None:
    """Boxes in the group colour with a dark median, and one point per dataset."""
    positions = [index + 1 for index, group_values in enumerate(values) if group_values]
    nonempty = [group_values for group_values in values if group_values]
    if nonempty:
        lines = {"color": WHISKER_COLOR, "linewidth": 0.8}
        boxplot = ax.boxplot(
            nonempty, positions=positions, patch_artist=True, showfliers=False, widths=0.5,
            medianprops={"color": MEDIAN_COLOR, "linewidth": 1.4}, whiskerprops=lines, capprops=lines,
        )
        for position, box in zip(positions, boxplot["boxes"]):
            color = COLORS[(position - 1) % len(COLORS)]
            box.set_facecolor(to_rgba(color, 0.22))
            box.set_edgecolor(color)
            box.set_linewidth(1.0)
    for index, group_values in enumerate(values):
        if group_values:
            offsets = np.linspace(-0.1, 0.1, len(group_values)) if len(group_values) > 1 else np.asarray([0.0])
            ax.scatter(
                index + 1 + offsets, group_values, s=22, color=COLORS[index % len(COLORS)],
                alpha=0.85, linewidths=0, zorder=3, gid=STRIP_POINTS,
            )


def _plot_metric_distribution(ax: plt.Axes, bundle, metric: str, title: str, prefer_long: bool = True) -> None:
    """One point per dataset, the same unit the statistics are computed on.

    The statistics are computed on long tracks, so a panel of all tracks shows
    the points without tests.
    """
    values = [[value for _dataset, value in dataset_metric_values(bundle, group, metric, prefer_long)] for group in bundle.groups]
    _box_and_points(ax, values)
    ax.set_xticks(
        np.arange(1, len(bundle.groups) + 1),
        [f"{group}\nn={len(group_values)}" for group, group_values in zip(bundle.groups, values)],
    )
    ax.set_title(title, fontsize=9.5)
    _style(ax)
    if not prefer_long:
        return
    results = _metric_results(bundle, metric)
    comparisons: list[tuple[int, int, str]] = []
    overall_label = None
    if len(bundle.groups) == 2:
        result = results[0]
        comparisons.append((1, 2, _significance_label(result.p_adjusted)))
        overall_label = stat_summary(result)
    else:
        overall = next(result for result in results if result.test == "Kruskal-Wallis")
        overall_label = stat_summary(overall)
        group_positions = {group: index + 1 for index, group in enumerate(bundle.groups)}
        pairwise = [result for result in results if result.test == "Mann-Whitney U"]
        if len(bundle.groups) > 3:
            pairwise = [result for result in pairwise if result.p_adjusted is not None and result.p_adjusted < 0.05]
        comparisons = [
            (group_positions[result.group_1], group_positions[result.group_2], _significance_label(result.p_adjusted))
            for result in pairwise
        ]
    if not _STYLE.stat_captions:
        overall_label = None
    finite_values = [value for group_values in values for value in group_values if math.isfinite(value)]
    if finite_values:
        _add_brackets(ax, comparisons, max(finite_values), min(finite_values), overall_label)
    elif overall_label:
        ax.text(0.5, 0.5, overall_label, transform=ax.transAxes, ha="center", va="center", fontsize=8)


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
    axes[0].set_xticks(x, bundle.groups)
    axes[0].set_ylabel("Tracks (%)")
    axes[0].set_title("Composition", fontsize=9.5)
    axes[0].set_ylim(0, 100)
    axes[0].set_yticks(range(0, 101, 20))
    axes[0].set_axisbelow(True)
    # The bars fill 0-100 %; the legend sits beside them, top to bottom in
    # the order the segments are stacked from the top.
    handles, labels = axes[0].get_legend_handles_labels()
    axes[0].legend(
        handles[::-1], labels[::-1], loc="center left", bbox_to_anchor=(1.0, 0.5),
        frameon=False, fontsize=8, handlelength=1.0, borderaxespad=0.3,
    )
    _style(axes[0])
    _plot_metric_distribution(axes[1], bundle, "directionality", "Directionality")
    axes[1].set_ylabel("Directionality")
    _plot_metric_distribution(axes[2], bundle, "turning_angle_std", "Turning-angle variability")
    axes[2].set_ylabel(_unit_label(bundle, SUMMARY_METRICS["turning_angle_std"]))


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
    _group_legend(container, axes[0, 0])


def _group_legend(container, ax: plt.Axes) -> None:
    """One legend for the groups under a block, outside every plot."""
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        container.legend(handles, labels, loc="outside lower center", ncol=min(6, len(labels)), frameon=False, fontsize=9)


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
        ax.plot(angles, density, color=color, linestyle=LINE_STYLES[index % len(LINE_STYLES)], linewidth=2, label=f"{group} (n={points[0].n} datasets)")
        valid_ci = np.asarray([point.ci_low is not None and point.ci_high is not None for point in points])
        if any(valid_ci):
            low = np.asarray([max(0.0, point.ci_low) if point.ci_low is not None else point.mean_density for point in points])
            high = np.asarray([point.ci_high if point.ci_high is not None else point.mean_density for point in points])
            ax.fill_between(angles, low, high, where=valid_ci, color=color, alpha=0.14)
    ax.set_xlim(0, 180)
    ax.set_xticks(np.arange(0, 181, 30))
    ax.set_ylim(bottom=0)
    ax.set_xlabel("Step turning angle (°)")
    ax.set_ylabel("Probability density (°$^{-1}$)")
    ax.set_title("Step turning-angle distribution (dataset mean ± 95% CI)")
    ax.legend(frameon=False, fontsize=8)
    _style(ax)


def _draw_msd(container, bundle, title: str, prefer_long: bool) -> None:
    axes = container.subplots(1, 3, squeeze=False)
    container.suptitle(title, fontsize=14)
    for index, group in enumerate(bundle.groups):
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
    x_label, y_label = _msd_labels(bundle)
    for ax, subtitle in zip(axes[0, :2], ("Population mean MSD", "Log-log MSD")):
        ax.set_title(subtitle)
        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        _style(ax)
    _group_legend(container, axes[0, 0])
    # α = 1 is free diffusion; below it subdiffusive, above it superdiffusive.
    axes[0, 2].axhline(1, color="#8B9895", linewidth=1, linestyle="--", zorder=0)
    _plot_metric_distribution(axes[0, 2], bundle, "msd_alpha", SUMMARY_METRICS["msd_alpha"], prefer_long)
    axes[0, 2].set_ylabel("α")


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
    _group_legend(container, ax)
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
    subfigures[0].suptitle(_long_tracks_title(bundle, "Temporal trends"), fontsize=14)
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
    """One grid: a row per group, the panel names over the first row only."""
    rows = len(bundle.groups)
    limit = _bundle_trajectory_limit(bundle)
    grid = container.add_gridspec(rows, 3)
    for row in range(rows):
        row_axes = [
            container.add_subplot(grid[row, 0]),
            container.add_subplot(grid[row, 1], projection="polar"),
            container.add_subplot(grid[row, 2]),
        ]
        _plot_group_trajectory_axes(bundle, row, row_axes, limit, titles=row == 0)
        _row_label(row_axes[0], bundle.groups[row])
    container.suptitle(f"Group trajectory analysis (shared scale ±{limit:.0f} {_distance_unit(bundle)})", fontsize=15)
    container.legend(handles=_type_handles(), loc="outside upper center", ncol=3, frameon=False, fontsize=9)


def _all_representatives(bundle) -> list[dict[str, list]]:
    return [_pick_representatives(bundle, group) for group in bundle.groups]


def _representative_limit(bundle, selections: list[dict[str, list]] | None = None) -> float:
    """One scale for every representative panel, set by the tracks shown.

    The scale of all long tracks would let one far-travelling track that is
    not shown shrink every shown track to a dot.
    """
    selections = selections if selections is not None else _all_representatives(bundle)
    return _trajectory_limit([track for selected in selections for tracks in selected.values() for track in tracks])


def _representatives_grid(bundle) -> tuple[bool, list[tuple[int, str]], int]:
    """Layout, the (group, type) of each row that has tracks, and the columns."""
    count = bundle.parameters.representatives_per_type
    selections = _all_representatives(bundle)
    types = ("Random", "Mixed", "Directed")
    # Two groups sit side by side on screen; at print width eight columns are
    # too narrow, so they stack like three or more groups do.
    horizontal = len(bundle.groups) == 2 and _STYLE.print_width_mm is None
    if horizontal:
        rows = [(-1, name) for name in types if any(selected[name] for selected in selections)]
        return True, rows or [(-1, "Random")], count * len(bundle.groups)
    rows = [(index, name) for index, selected in enumerate(selections) for name in types if selected[name]]
    return False, rows or [(0, "Random")], count


def _draw_representatives_all(container, bundle) -> None:
    count = bundle.parameters.representatives_per_type
    selections = _all_representatives(bundle)
    limit = _representative_limit(bundle, selections)
    horizontal, rows, columns = _representatives_grid(bundle)
    axes = container.subplots(len(rows), columns, squeeze=False)
    container.suptitle(f"Representative trajectories (shared scale ±{limit:.0f} {_distance_unit(bundle)})", fontsize=15)
    unit = _distance_unit(bundle)
    for row, (row_group, trajectory_type) in enumerate(rows):
        for group_index, group in enumerate(bundle.groups):
            if not horizontal and group_index != row_group:
                continue
            tracks = selections[group_index][trajectory_type]
            start_column = group_index * count if horizontal else 0
            for column in range(count):
                ax = axes[row, start_column + column]
                if column >= len(tracks):
                    ax.set_visible(False)
                    continue
                _plot_representative_track(ax, tracks[column], trajectory_type, limit)
                if column == 0:
                    ax.set_ylabel(f"{group}\n{trajectory_type}", fontsize=8)
    _bottom_xlabels(axes, f"Δx ({unit})")
    container.supylabel(f"Δy ({unit})", fontsize=9)
    _representative_key(container, axes.ravel().tolist())


def _bottom_xlabels(axes, label: str) -> None:
    """The x label on the lowest shown panel of each column, above the colour bar."""
    for column in axes.T:
        shown = [ax for ax in column if ax.get_visible()]
        if shown:
            shown[-1].set_xlabel(label, fontsize=8)


@dataclass(frozen=True)
class Block:
    """One panel layout: the size it is drawn at alone, and how to draw it."""

    size: Callable[[object], tuple[float, float]]
    draw: Callable[[object, object], None]


def _turning_size(bundle) -> tuple[float, float]:
    return (1 * 8.2, 1 * 4.8) if any(_turning_distributions(bundle)) else (1 * 9, 1 * 4.5)


def _representatives_size(bundle) -> tuple[float, float]:
    _horizontal, rows, columns = _representatives_grid(bundle)
    return columns * 2.7, len(rows) * 2.6 + 1.2


# Sizes are written exactly as the standalone figures always computed them,
# so the default renders keep their exact pixels.
def _long_tracks_title(bundle, title: str) -> str:
    return f"{title} (tracks ≥ {bundle.parameters.long_track_min_observations} time points)"


BLOCKS: dict[str, Block] = {
    "cell_appearance": Block(lambda b: (3 * 4.6, 1 * 4), _draw_cell_appearance),
    "classification": Block(lambda b: (3 * 4.8, 1 * 4.2), _draw_classification),
    "msd_long": Block(
        lambda b: (3 * 4.8, 1 * 4.2),
        lambda c, b: _draw_msd(c, b, _long_tracks_title(b, "Mean squared displacement"), True),
    ),
    "msd_all": Block(
        lambda b: (3 * 4.8, 1 * 4.2),
        lambda c, b: _draw_msd(c, b, "Mean squared displacement (all tracks)", False),
    ),
    "msd_summary": Block(lambda b: (1 * 8.2, 1 * 4.8), _draw_msd_summary),
    "turning_angle_distribution": Block(_turning_size, _draw_turning_angle_distribution),
    "temporal_long": Block(
        lambda b: (GRID_COLUMNS * 4.3, _grid_rows(len(_temporal_metrics(b))) * 3.2),
        lambda c, b: _draw_temporal(c, b, _long_tracks_title(b, "Temporal trends"), True),
    ),
    "temporal_all": Block(
        lambda b: (GRID_COLUMNS * 4.3, _grid_rows(len(_temporal_metrics(b))) * 3.2),
        lambda c, b: _draw_temporal(c, b, "Temporal trends (all tracks)", False),
    ),
    "parameter_distributions": Block(
        lambda b: (GRID_COLUMNS * 4.2, _grid_rows(len(_parameter_metrics(b))) * 3.4),
        _draw_parameter_distributions,
    ),
    "group_trajectories": Block(lambda b: (15, 3.8 * len(b.groups) + 0.9), _draw_group_trajectories_all),
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
    limit = _representative_limit(bundle)
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
    unit = _distance_unit(bundle)
    _bottom_xlabels(axes, f"Δx ({unit})")
    fig.supylabel(f"Δy ({unit})", fontsize=9)
    _representative_key(fig, [ax for ax in axes.ravel() if ax.get_visible()])
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
    fig.legend(handles=_type_handles(), loc="outside upper center", ncol=3, frameon=False, fontsize=9)
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
        add("temporal_long", "temporal_long", _plot_temporal(bundle, _long_tracks_title(bundle, "Temporal trends"), True))
    if "classification" in selected:
        add("classification", "trajectory_classification", figure_classification(bundle))
    if "turning_angle_distribution" in selected:
        add("turning_angle_distribution", "turning_angle_distribution", figure_turning_angle_distribution(bundle))
    if "msd_long" in selected:
        add("msd_long", "msd_long", _plot_msd(bundle, _long_tracks_title(bundle, "Mean squared displacement"), True))
    if "msd_summary" in selected:
        add("msd_summary", "msd_summary", figure_msd_summary(bundle))
    if "representatives" in selected:
        for index, group in enumerate(bundle.groups):
            add("representatives", f"{_slug(group, index)}_representatives", figure_representatives(bundle, index))
    if "temporal_all" in selected:
        add("temporal_all", "temporal_all", _plot_temporal(bundle, "Temporal trends (all tracks)", False))
    if "msd_all" in selected:
        add("msd_all", "msd_all", _plot_msd(bundle, "Mean squared displacement (all tracks)", False))
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
