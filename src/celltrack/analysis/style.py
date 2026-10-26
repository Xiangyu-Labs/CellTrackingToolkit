"""Presentation settings for analysis figures.

Everything here changes how a figure looks, never what it shows: colours,
wording, font size, resolution, file formats and how panels are arranged into
composite figures. The analysis numbers, the statistics and the data drawn in
each panel come from the analysis parameters alone, so a style can be edited
and re-rendered as often as needed without changing a result.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# Panels that can be placed into a composite figure. Each one is drawn by the
# same code as the standalone figure it is named after.
BLOCKS = {
    "cell_appearance": "Cell appearance over time",
    "classification": "Trajectory classification",
    "msd_long": "Mean square displacement (long tracks)",
    "msd_all": "Mean square displacement (all tracks)",
    "msd_summary": "MSD comparison (mean ± 95% CI)",
    "turning_angle_distribution": "Step turning-angle distribution",
    "temporal_long": "Temporal trends (long tracks)",
    "temporal_all": "Temporal trends (all tracks)",
    "parameter_distributions": "Parameter distributions",
    "group_trajectories": "Group trajectory analysis (all groups)",
    "representatives": "Representative trajectories (all groups)",
    "cell_and_classification": "Cell appearance and classification (paper 01)",
    "temporal_and_parameters": "Temporal trends and parameters (paper 02)",
}

OUTPUT_FORMATS = ("png", "tiff", "pdf", "svg")
DEFAULT_GROUP_COLORS = ("#156F62", "#D97706", "#2563EB", "#B42318", "#7C3AED", "#087EA4")
# Trajectory types are ordered (random < mixed < directed), so they use one
# hue from light to dark. No step shares a hue with the group colours, so
# green or orange always means a group.
DEFAULT_TYPE_COLORS = {"Random": "#9F8FDD", "Mixed": "#6650C4", "Directed": "#3A2683"}
NEUTRAL_ANGLE_COLOR = "#6B7280"
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


class CompositeFigure(BaseModel):
    """Panels arranged in columns; each column is read top to bottom.

    Panel letters run down the first column, then down the next, which is
    how the manuscript figures are laid out (A-C on the left, D on the right).
    """

    name: str = Field(min_length=1, max_length=60)
    title: str = Field(default="", max_length=200)
    panel_letters: bool = True
    columns: list[list[str]] = Field(min_length=1, max_length=4)

    @field_validator("columns")
    @classmethod
    def known_blocks(cls, columns: list[list[str]]) -> list[list[str]]:
        if not any(columns):
            raise ValueError("a composite figure needs at least one panel")
        if any(len(column) > 6 for column in columns):
            raise ValueError("a column holds at most 6 panels")
        unknown = sorted({block for column in columns for block in column} - set(BLOCKS))
        if unknown:
            raise ValueError(f"Unknown panel: {', '.join(unknown)}")
        return [column for column in columns if column]


class FigureStyle(BaseModel):
    """The defaults reproduce the standard figures exactly."""

    dpi: int = Field(default=140, ge=72, le=1200)
    formats: list[Literal["png", "tiff", "pdf", "svg"]] = Field(default_factory=lambda: ["png"])
    font_scale: float = Field(default=1.0, ge=0.5, le=3.0)
    group_colors: dict[str, str] = Field(default_factory=dict)
    type_colors: dict[str, str] = Field(default_factory=dict)
    # Colour of the final-displacement-angle (polar) plot. Empty keeps each
    # group's colour. Next to trajectories coloured by type, a neutral colour
    # avoids green/orange meaning a group in one panel and a type in the next.
    angle_plot_color: str = ""
    # Exact text replacements, matched on the whole label. An empty
    # replacement removes that text and the space it took.
    text: dict[str, str] = Field(default_factory=dict)
    hide_figure_titles: bool = False
    # The test name, p-value and effect size above each distribution panel.
    # Journals usually want them in the caption; the stars stay either way.
    stat_captions: bool = True
    # Print size. When set, every figure is laid out for this printed width:
    # the canvas shrinks until its smallest text prints at ``print_text_pt``,
    # long titles wrap, and PNG/TIFF carry ``dpi`` at that width. Lines and
    # markers shrink with the canvas, so the figure looks as it will on paper.
    print_width_mm: float | None = Field(default=None, ge=30, le=500)
    print_text_pt: float = Field(default=6.0, ge=4, le=14)
    # A figure taller than this at the print width is squeezed to it (panels
    # get shorter, not narrower) unless a plot would end up under 12 mm; then it
    # keeps its height and the figure carries a warning. 230 mm fits a
    # journal page with a caption.
    print_max_height_mm: float = Field(default=230, ge=30, le=1000)
    short_dataset_names: bool = False
    composites: list[CompositeFigure] = Field(default_factory=list, max_length=12)

    @field_validator("formats")
    @classmethod
    def png_always_present(cls, formats: list[str]) -> list[str]:
        # The page previews PNGs, so a PNG is always written.
        return ["png", *[item for item in dict.fromkeys(formats) if item != "png"]]

    @field_validator("group_colors", "type_colors")
    @classmethod
    def hex_colors(cls, colors: dict[str, str]) -> dict[str, str]:
        bad = [value for value in colors.values() if not HEX_COLOR.match(value)]
        if bad:
            raise ValueError(f"Colours must look like #1A2B3C: {', '.join(bad)}")
        return colors

    @field_validator("angle_plot_color")
    @classmethod
    def optional_hex_color(cls, color: str) -> str:
        if color and not HEX_COLOR.match(color):
            raise ValueError(f"Colours must look like #1A2B3C: {color}")
        return color

    @field_validator("type_colors")
    @classmethod
    def known_types(cls, colors: dict[str, str]) -> dict[str, str]:
        unknown = sorted(set(colors) - set(DEFAULT_TYPE_COLORS))
        if unknown:
            raise ValueError(f"Unknown trajectory type: {', '.join(unknown)}")
        return colors

    @field_validator("text")
    @classmethod
    def bounded_text(cls, text: dict[str, str]) -> dict[str, str]:
        if len(text) > 500:
            raise ValueError("at most 500 text replacements")
        if any(len(key) > 300 or len(value) > 300 for key, value in text.items()):
            raise ValueError("each text is limited to 300 characters")
        # A replacement that equals the original is a no-op; dropping it keeps
        # a default style equal to an untouched one.
        return {key: value for key, value in text.items() if key != value}

    @model_validator(mode="after")
    def unique_composite_names(self) -> "FigureStyle":
        names = [slug(item.name) for item in self.composites]
        if len(names) != len(set(names)):
            raise ValueError("composite figure names must be unique")
        return self

    def changes_rendering(self) -> bool:
        """True when text must be rewritten or resized before saving."""
        return bool(self.text) or self.font_scale != 1.0 or self.hide_figure_titles


DEFAULT_FIGURE_STYLE = FigureStyle()


def slug(value: str, fallback: str = "figure") -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or fallback


# One click in the editor adds these. They match the layouts of the draft
# manuscript: Figures 1 and 3 put appearance, classification and MSD down the
# left and the trajectory analysis on the right; Figures 2 and 4 are the
# paired temporal/parameter grids; the supplement holds the turning-angle
# distribution and the representative tracks.
COMPOSITE_PRESETS = {
    "migration_overview": {
        "label": "Overview: appearance, classification, MSD | trajectories",
        "composite": {
            "name": "Migration overview",
            "panel_letters": True,
            "columns": [["cell_appearance", "classification", "msd_long"], ["group_trajectories"]],
        },
    },
    "temporal_and_parameters": {
        "label": "Temporal trends and parameter distributions",
        "composite": {
            "name": "Temporal and parameters",
            "panel_letters": False,
            "columns": [["temporal_and_parameters"]],
        },
    },
    "supplement_turning_angle": {
        "label": "Supplement: step turning-angle distribution",
        "composite": {
            "name": "Supplement turning angle",
            "panel_letters": False,
            "columns": [["turning_angle_distribution"]],
        },
    },
    "supplement_representatives": {
        "label": "Supplement: representative trajectories",
        "composite": {
            "name": "Supplement representatives",
            "panel_letters": False,
            "columns": [["representatives"]],
        },
    },
}


def style_options() -> dict[str, object]:
    return {
        "defaults": DEFAULT_FIGURE_STYLE.model_dump(),
        "blocks": BLOCKS,
        "formats": list(OUTPUT_FORMATS),
        "group_colors": list(DEFAULT_GROUP_COLORS),
        "type_colors": DEFAULT_TYPE_COLORS,
        "neutral_angle_color": NEUTRAL_ANGLE_COLOR,
        "presets": COMPOSITE_PRESETS,
        "ranges": {"dpi": [72, 1200], "font_scale": [0.5, 3.0], "print_width_mm": [30, 500], "print_text_pt": [4, 14], "print_max_height_mm": [30, 1000]},
        # Common journal widths: single column, 1.5 column, full page width.
        "print_widths": [85, 120, 180],
    }
