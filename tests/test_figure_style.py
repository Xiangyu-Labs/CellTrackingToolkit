"""Figure styles change presentation only.

The data fingerprint of every figure is pinned in
``tests/fixtures/figure_fingerprints.json``. A change that moves a line, a
point, a bar or an axis range in any figure fails here, which is what used to
break approved figures by accident. To accept an intended change, run:

    CELLTRACK_UPDATE_FINGERPRINTS=1 python -m unittest tests.test_figure_style
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from celltrack.analysis import compute as analysis
from celltrack.analysis import figures
from celltrack.analysis.jobs import AnalysisTaskManager
from celltrack.analysis.models import FIGURE_TYPES, AnalysisParameters, DEFAULT_ANALYSIS_PARAMETERS
from celltrack.analysis.service import rerender_groups
from celltrack.analysis.storage import AnalysisStore, analysis_payload, media_type
from celltrack.analysis.style import BLOCKS, COMPOSITE_PRESETS, CompositeFigure, FigureStyle, style_options

FIXTURE = ROOT / "tests" / "fixtures" / "figure_fingerprints.json"

HEAVY_STYLE = {
    "dpi": 90,
    "font_scale": 1.4,
    "group_colors": {"Alpha": "#000000", "Gamma": "#FF00FF"},
    "type_colors": {"Random": "#123456"},
    "text": {
        "Trajectory classification": "Classification",
        "Composition": "",
        "Beta": "B",
        "Frame": "Frame (5 min)",
    },
    "hide_figure_titles": True,
    "short_dataset_names": True,
}


def _write_tracks(path: Path, seed: int, drift: float) -> None:
    random = np.random.RandomState(seed)
    rows = ["track_id,timeframe,x,y,polygon"]
    for track_id in range(1, 9):
        start = int(random.randint(1, 6))
        length = int(random.randint(8, 26))
        x, y = float(random.uniform(0, 200)), float(random.uniform(0, 200))
        for frame in range(start, start + length):
            x += float(random.normal(drift, 2.0))
            y += float(random.normal(drift * 0.5, 2.0))
            size = 4 + float(random.uniform(0, 2))
            polygon = [(x, y), (x + size, y), (x + size, y + size * 0.7), (x, y + size * 0.7)]
            rows.append(f'{track_id},{frame},{x:.4f},{y:.4f},"{polygon}"')
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _bundle(root: Path, groups: int = 3):
    specs = [("Alpha", 0.2), ("Beta", 1.0), ("Gamma", 2.0)][:groups]
    prepared = []
    for group_index, (name, drift) in enumerate(specs):
        datasets = []
        for sample in range(3):
            path = root / f"{name}-{sample}.csv"
            _write_tracks(path, seed=group_index * 10 + sample, drift=drift)
            datasets.append((f"{name} folder/{name.lower()}_{sample}", path))
        prepared.append((name, datasets))
    parameters = AnalysisParameters(
        long_track_min_observations=10,
        appearance_min_observations=3,
        figure_types=list(FIGURE_TYPES),
    )
    return analysis.prepare_analysis(prepared, parameters)


def _only(bundle, *figure_types):
    """The same data with fewer standalone figures, to keep tests quick."""
    parameters = bundle.parameters.model_copy(update={"figure_types": list(figure_types)})
    return dataclasses.replace(bundle, parameters=parameters)


def _number(value) -> str:
    # Rounding residue such as 5.55e-17 in rotated marker shapes differs
    # between platforms' maths libraries; it is not data, so it counts as 0.
    value = float(value)
    return f"{0.0 if abs(value) < 1e-9 else value:.6g}"


def _ylim(ax):
    # Room for marker size and significance brackets depends on the drawn
    # panel height, so data are compared on the range before that room.
    return getattr(ax, "layout_free_ylim", ax.get_ylim())


def _data_lines(ax):
    # Brackets are spaced in points at layout time; they are not data.
    return [line for line in ax.get_lines() if line.get_gid() != figures.STAT_BRACKET]


def _fingerprint(fig) -> str:
    """Hash of what the figure shows: coordinates and axis ranges, not looks."""
    parts = []
    for ax in fig.get_axes():
        if not ax.get_visible():
            parts.append("hidden-axes")
            continue
        parts.append("xlim " + " ".join(map(_number, ax.get_xlim())))
        parts.append("ylim " + " ".join(map(_number, _ylim(ax))))
        for line in _data_lines(ax):
            data = np.asarray(line.get_xydata(), dtype=float).ravel()
            parts.append("line " + " ".join(map(_number, data)))
        for collection in ax.collections:
            offsets = np.asarray(collection.get_offsets(), dtype=float).ravel()
            vertices = [np.asarray(path.vertices, dtype=float).ravel() for path in collection.get_paths()]
            joined = np.concatenate(vertices) if vertices else np.zeros(0)
            parts.append("collection " + " ".join(map(_number, offsets)) + " | " + " ".join(map(_number, joined)))
        for item in ax.patches:
            if hasattr(item, "get_x") and hasattr(item, "get_width"):
                bounds = (item.get_x(), item.get_y(), item.get_width(), item.get_height())
                parts.append("patch " + " ".join(map(_number, bounds)))
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


class _Capture:
    """Keep each figure open after saving so its artists can be inspected."""

    def __init__(self):
        self.figures = []

    def __enter__(self):
        self._patch = patch.object(figures.plt, "close", side_effect=self.figures.append)
        self._patch.start()
        return self

    def __exit__(self, *_exc):
        self._patch.stop()
        for fig in self.figures:
            figures.plt.close(fig)


def _render(bundle, style=None):
    with _Capture() as capture:
        rendered = figures.render_all_figures(bundle, style)
        prints = {name: _fingerprint(fig) for name, fig in zip(rendered, capture.figures)}
        texts = {
            name: [text.get_text() for text in fig.findobj(figures.Text) if text.get_visible() and text.get_text()]
            for name, fig in zip(rendered, capture.figures)
        }
    return rendered, prints, texts


class PhysicalUnitTests(unittest.TestCase):
    """A scale converts the numbers; it never changes which differences are significant."""

    def test_units_rescale_values_and_keep_statistics(self):
        with tempfile.TemporaryDirectory() as directory:
            pixels = _bundle(Path(directory))
            groups = [
                (name, [(f"{name} folder/{name.lower()}_{sample}", Path(directory) / f"{name}-{sample}.csv") for sample in range(3)])
                for name in pixels.groups
            ]
            parameters = pixels.parameters.model_copy(update={"microns_per_pixel": 0.5, "frame_interval_minutes": 10})
            microns = analysis.prepare_analysis(groups, parameters)
        for before, after in zip(pixels.summaries, microns.summaries):
            self.assertEqual(after.trajectory_type, before.trajectory_type)
            self.assertAlmostEqual(after.total_path_length, before.total_path_length * 0.5)
            self.assertAlmostEqual(after.mean_speed, before.mean_speed * 0.5 / 10)
            self.assertAlmostEqual(after.mean_area, before.mean_area * 0.25)
            self.assertAlmostEqual(after.mean_perimeter, before.mean_perimeter * 0.5)
            self.assertEqual(after.mean_roundness, before.mean_roundness)
            self.assertEqual(after.directionality, before.directionality)
        stats_before = {(row.metric, row.group_1, row.group_2): row.p_value for row in analysis.statistical_results(pixels)}
        stats_after = {(row.metric, row.group_1, row.group_2): row.p_value for row in analysis.statistical_results(microns)}
        self.assertEqual(stats_after.keys(), stats_before.keys())
        for key, value in stats_before.items():
            if value is None:
                self.assertIsNone(stats_after[key])
            else:
                self.assertAlmostEqual(stats_after[key], value, places=12, msg=key)
        before_msd = analysis.msd_summary(pixels, "Alpha")
        after_msd = analysis.msd_summary(microns, "Alpha")
        self.assertAlmostEqual(after_msd[0].lag, before_msd[0].lag * 10)
        self.assertAlmostEqual(after_msd[0].mean, before_msd[0].mean * 0.25)

        bundle = _only(microns, "temporal_long", "group_trajectories", "cell_appearance", "msd_long")
        texts = {text for image in figures.render_all_figures(bundle).values() for text in image.texts}
        for expected in ("Instantaneous speed (µm/min)", "Cell area (µm²)", "Δx (µm)", "Time (h)", "MSD (µm²)", "Time lag (min)"):
            self.assertIn(expected, texts)
        self.assertFalse(any("px" in text or text == "Frame" for text in texts), sorted(t for t in texts if "px" in t))


class FigureDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temporary = tempfile.TemporaryDirectory()
        cls.bundle = _bundle(Path(cls._temporary.name))
        cls.rendered, cls.prints, cls.texts = _render(cls.bundle)

    @classmethod
    def tearDownClass(cls):
        cls._temporary.cleanup()

    def test_figure_data_matches_the_approved_fingerprints(self):
        if os.environ.get("CELLTRACK_UPDATE_FINGERPRINTS"):
            FIXTURE.parent.mkdir(parents=True, exist_ok=True)
            FIXTURE.write_text(json.dumps(self.prints, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(sorted(self.prints), sorted(expected))
        changed = [name for name in expected if self.prints[name] != expected[name]]
        self.assertEqual(changed, [], "these figures now show different data")

    def test_default_style_renders_the_same_bytes_as_no_style(self):
        styled = figures.render_all_figures(self.bundle, FigureStyle())
        self.assertEqual(list(styled), list(self.rendered))
        for name in self.rendered:
            self.assertEqual(bytes(styled[name]), bytes(self.rendered[name]), name)

    def test_style_changes_presentation_but_never_data(self):
        rendered, prints, texts = _render(self.bundle, FigureStyle(**HEAVY_STYLE))
        self.assertEqual(prints, self.prints)
        classification = texts["figure_03_trajectory_classification.png"]
        self.assertNotIn("Composition", classification)
        self.assertIn("B", classification)
        self.assertNotIn("Beta", classification)
        self.assertNotIn("Trajectory classification", classification)
        # Multi-line labels are renamed line by line.
        self.assertTrue(any(text.startswith("B\nn=") for text in texts["figure_11_parameter_distributions.png"]))
        # Titles hidden, short dataset names used.
        self.assertNotIn("Cell appearance and trajectory classification", texts["paper_01_cell_and_classification.png"])
        self.assertTrue(any(text.startswith("alpha_0\n") for text in texts["supplementary_01_representative_trajectories.png"]))
        # The editor still sees the default wording.
        self.assertIn("Trajectory classification", rendered["figure_03_trajectory_classification.png"].texts)

    def test_rendered_figures_list_their_default_wording(self):
        texts = self.rendered["figure_03_trajectory_classification.png"].texts
        for expected in ("Trajectory classification", "Composition", "Alpha", "Directionality"):
            self.assertIn(expected, texts)
        self.assertFalse(any(text.isdigit() for text in texts))


class CompositeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temporary = tempfile.TemporaryDirectory()
        cls.bundle = _bundle(Path(cls._temporary.name), groups=2)

    @classmethod
    def tearDownClass(cls):
        cls._temporary.cleanup()

    def test_every_block_and_preset_renders(self):
        composites = [CompositeFigure(name=key, columns=[[key]]) for key in BLOCKS]
        composites.append(CompositeFigure(**COMPOSITE_PRESETS["migration_overview"]["composite"]))
        style = FigureStyle(dpi=72, composites=composites[:12])
        rendered = figures.render_all_figures(_only(self.bundle, "msd_summary"), style)
        names = [name for name in rendered if name.startswith("composite_")]
        self.assertEqual(len(names), 12)
        self.assertEqual(names[0], "composite_01_cell-appearance.png")
        self.assertEqual(rendered[names[0]].label, "cell_appearance")
        self.assertTrue(all(rendered[name].startswith(b"\x89PNG") for name in names))

    def test_composite_panels_show_the_same_data_as_their_standalone_figure(self):
        style = FigureStyle(composites=[{"name": "Overview", "columns": [["classification", "msd_long"]]}])
        with _Capture() as capture:
            rendered = figures.render_all_figures(_only(self.bundle, "classification", "msd_long"), style)
            by_name = dict(zip(rendered, capture.figures))
            composite_axes = by_name["composite_01_overview.png"].get_axes()
            standalone = by_name["figure_03_trajectory_classification.png"].get_axes() + by_name["figure_05_msd_long.png"].get_axes()
            for mine, theirs in zip(composite_axes, standalone):
                self.assertEqual(
                    [line.get_xydata().tolist() for line in _data_lines(mine)],
                    [line.get_xydata().tolist() for line in _data_lines(theirs)],
                )
                self.assertEqual(_ylim(mine), _ylim(theirs))
            self.assertEqual(len(composite_axes), len(standalone))

    def test_layout_never_shrinks_a_panel_below_its_standalone_size(self):
        composite = CompositeFigure(**COMPOSITE_PRESETS["migration_overview"]["composite"])
        (width, height), widths, heights = figures.composite_layout(composite, self.bundle)
        self.assertAlmostEqual(width, sum(widths))
        for column, column_widths, column_heights in zip(composite.columns, widths, heights):
            self.assertAlmostEqual(sum(column_heights), height)
            for key, block_height in zip(column, column_heights):
                natural_width, natural_height = figures.BLOCKS[key].size(self.bundle)
                self.assertGreaterEqual(column_widths + 1e-9, natural_width)
                self.assertAlmostEqual(block_height / column_widths, natural_height / natural_width)

    def test_extra_formats_are_written_alongside_the_png(self):
        style = FigureStyle(dpi=100, formats=["svg", "pdf", "tiff"])
        with figures.figure_style(style, tuple(self.bundle.groups)):
            rendered = figures.figure_classification(self.bundle)
        self.assertTrue(rendered.startswith(b"\x89PNG"))
        self.assertEqual(list(rendered.extras), ["svg", "pdf", "tiff"])
        self.assertTrue(rendered.extras["pdf"].startswith(b"%PDF"))
        self.assertIn(b"<svg", rendered.extras["svg"][:400])
        self.assertTrue(rendered.extras["tiff"][:4] in (b"II*\x00", b"MM\x00*"))
        # Text stays text in vector output.
        self.assertIn(b"Composition", rendered.extras["svg"])

    def test_style_is_restored_after_rendering(self):
        before = (figures.COLORS, dict(figures.TYPE_COLORS), figures._STYLE)
        figures.render_all_figures(_only(self.bundle, "classification"), FigureStyle(**HEAVY_STYLE))
        self.assertEqual((figures.COLORS, dict(figures.TYPE_COLORS), figures._STYLE), before)


class PrintSizeTests(unittest.TestCase):
    """Print size changes the canvas and the text sizes, never the data."""

    @classmethod
    def setUpClass(cls):
        cls._temporary = tempfile.TemporaryDirectory()
        cls.bundle = _bundle(Path(cls._temporary.name), groups=2)

    @classmethod
    def tearDownClass(cls):
        cls._temporary.cleanup()

    def _one(self, style, draw):
        with _Capture() as capture, figures.figure_style(style, tuple(self.bundle.groups)):
            rendered = draw(self.bundle)
            return rendered, capture.figures[-1], _fingerprint(capture.figures[-1])

    def test_printed_width_and_dpi(self):
        from PIL import Image
        import io

        style = FigureStyle(dpi=300, print_width_mm=180, formats=["tiff"])
        rendered, fig, prints = self._one(style, figures.figure_classification)
        _default, _fig, default_prints = self._one(FigureStyle(), figures.figure_classification)
        self.assertEqual(prints, default_prints)
        for data in (bytes(rendered), rendered.extras["tiff"]):
            image = Image.open(io.BytesIO(data))
            self.assertAlmostEqual(image.size[0], 180 / 25.4 * 300, delta=2)
            self.assertAlmostEqual(image.info["dpi"][0], 300, delta=0.1)
        sizes = {text.get_fontsize() for text in fig.findobj(figures.Text) if text.get_visible() and text.get_text()}
        self.assertGreaterEqual(min(sizes), style.print_text_pt)
        self.assertEqual(rendered.warnings, ())

    def test_too_narrow_a_width_is_flagged(self):
        rendered, _fig, _prints = self._one(FigureStyle(dpi=100, print_width_mm=85), figures.figure_classification)
        self.assertTrue(rendered.warnings[0].startswith("Some text does not fit at 85 mm wide"), rendered.warnings)

    def test_screen_figures_have_no_warnings(self):
        rendered, _fig, _prints = self._one(FigureStyle(), figures.figure_classification)
        self.assertEqual(rendered.warnings, ())

    def test_tall_figures_are_squeezed_or_flagged(self):
        composite = {"name": "Tall", "columns": [["cell_appearance", "classification", "msd_long", "group_trajectories"]]}
        style = FigureStyle(dpi=100, print_width_mm=180, print_max_height_mm=120, composites=[composite])
        with _Capture() as capture:
            rendered = figures.render_all_figures(_only(self.bundle, "msd_summary"), style)
            fig = capture.figures[-1]
            height = fig.get_figheight() * 25.4
        image = rendered["composite_01_tall.png"]
        flagged = any("page limit" in note for note in image.warnings)
        self.assertTrue(flagged or abs(height - 120) < 0.5, (height, image.warnings))

    def test_two_group_representatives_stack_and_keep_outer_numbers(self):
        style = FigureStyle(dpi=100, print_width_mm=180)
        with figures.figure_style(style):
            self.assertFalse(figures._representatives_grid(self.bundle)[0])
        self.assertTrue(figures._representatives_grid(self.bundle)[0])
        _rendered, fig, _prints = self._one(style, figures.figure_supplementary_representatives)
        shown = [
            ax for ax in fig.get_axes()
            if ax.get_visible() and any(label.get_visible() and label.get_text() for label in ax.get_yticklabels())
        ]
        columns = {ax.get_subplotspec().colspan.start for ax in shown}
        self.assertEqual(columns, {0})

    def test_significance_marks_stay_inside_short_panels(self):
        style = FigureStyle(dpi=100, print_width_mm=120)
        _rendered, fig, _prints = self._one(style, figures.figure_parameter_distributions)
        from matplotlib.backends.backend_agg import FigureCanvasAgg

        canvas = FigureCanvasAgg(fig)
        canvas.draw()
        renderer = canvas.get_renderer()
        marks = [(ax, text) for ax in fig.get_axes() for text in ax.texts if text.get_gid() == figures.STAT_MARK]
        self.assertTrue(marks)
        for ax, text in marks:
            self.assertLessEqual(text.get_window_extent(renderer).y1, ax.bbox.y1 + 0.5, text.get_text())

    def test_angle_plot_colour(self):
        def bar_colours(style):
            _rendered, fig, _prints = self._one(style, figures.figure_paper_group_trajectories)
            return {
                tuple(round(value, 3) for value in patch.get_facecolor()[:3])
                for ax in fig.get_axes() if ax.name == "polar" for patch in ax.patches
            }

        grey = tuple(round(value, 3) for value in __import__("matplotlib.colors", fromlist=["to_rgb"]).to_rgb("#6B7280"))
        self.assertEqual(bar_colours(FigureStyle(angle_plot_color="#6B7280")), {grey})
        self.assertNotIn(grey, bar_colours(FigureStyle()))


class ManuscriptQualityTests(unittest.TestCase):
    """What a journal figure needs: nothing cut, nothing overlapping, no code text."""

    PRINT = {
        "dpi": 100, "print_width_mm": 165, "print_text_pt": 6, "hide_figure_titles": True,
        "stat_captions": False, "formats": ["tiff"],
        "composites": [
            {"name": "Overview", "columns": [["cell_appearance", "classification", "msd_long", "group_trajectories"]]},
            {"name": "Parameters", "columns": [["temporal_long", "parameter_distributions"]]},
            {"name": "Representatives", "columns": [["representatives"]]},
        ],
    }

    @classmethod
    def setUpClass(cls):
        cls._temporary = tempfile.TemporaryDirectory()
        cls.bundle = _bundle(Path(cls._temporary.name))
        with _Capture() as capture:
            cls.rendered = figures.render_all_figures(_only(cls.bundle, "msd_summary"), FigureStyle(**cls.PRINT))
            cls.figures = dict(zip(cls.rendered, capture.figures))
            cls.composites = {name: fig for name, fig in cls.figures.items() if name.startswith("composite_")}
            # Keep the figures open for the tests; _Capture closes them on exit.
            capture.figures = []

    @classmethod
    def tearDownClass(cls):
        for fig in cls.figures.values():
            figures.plt.close(fig)
        cls._temporary.cleanup()

    def test_every_dataset_point_is_inside_its_axes(self):
        checked = 0
        for name, fig in self.composites.items():
            fig.canvas.draw()
            for ax in fig.get_axes():
                for points in [item for item in ax.collections if item.get_gid() == figures.STRIP_POINTS]:
                    radius = np.sqrt(points.get_sizes().max()) / 2 * fig.dpi / 72
                    shown = ax.transData.transform(np.asarray(points.get_offsets()))
                    box = ax.bbox
                    self.assertTrue(np.all(shown[:, 1] - radius >= box.y0 - 0.5), (name, ax.get_title()))
                    self.assertTrue(np.all(shown[:, 1] + radius <= box.y1 + 0.5), (name, ax.get_title()))
                    checked += 1
        self.assertGreater(checked, 10)

    def test_no_text_overlaps_and_marks_clear_their_brackets(self):
        for name, fig in self.composites.items():
            self.assertEqual(figures._text_overlaps(fig), (), name)
            renderer = fig.canvas.get_renderer()
            for ax in fig.get_axes():
                items = getattr(ax, "_brackets", {"items": []})["items"]
                for line, text, level in items:
                    top = text.get_window_extent(renderer).y1
                    self.assertLessEqual(top, ax.bbox.y1 + 0.5, (name, ax.get_title()))
                    above = [other for other, _text, other_level in items if other_level == level + 1]
                    for other in above:
                        bracket = ax.transData.transform((0, max(other.get_ydata())))[1]
                        self.assertLess(top, bracket, (name, ax.get_title()))
        warned = {name: self.rendered[name].warnings for name in self.composites if self.rendered[name].warnings}
        self.assertTrue(all("page limit" in note for notes in warned.values() for note in notes), warned)

    def test_no_code_style_text(self):
        texts = {text for name in self.composites for text in figures._figure_texts(self.figures[name])}
        for fig in self.composites.values():
            texts |= {text.get_text() for text in fig.findobj(figures.Text) if text.get_visible()}
        for token in ("r_rb", "epsilon^2", "Delta ", "(deg)", " | ", "exponent alpha", "Cumulative appeared"):
            self.assertFalse(any(token in text for text in texts), (token, sorted(t for t in texts if token in t)))

    def test_trajectory_types_never_reuse_a_group_colour(self):
        groups = {colour.casefold() for colour in figures.DEFAULT_GROUP_COLORS}
        types = {colour.casefold() for colour in figures.DEFAULT_TYPE_COLORS.values()}
        self.assertEqual(groups & types, set())

    def test_print_exports_are_rgb_at_the_requested_dpi(self):
        from PIL import Image
        import io

        for name in self.composites:
            for data in (bytes(self.rendered[name]), self.rendered[name].extras["tiff"]):
                image = Image.open(io.BytesIO(data))
                self.assertEqual(image.mode, "RGB", name)
                self.assertAlmostEqual(image.info["dpi"][0], 100, delta=0.1)


class MsdTests(unittest.TestCase):
    def test_gapped_track_uses_whole_frame_lags(self):
        from types import SimpleNamespace

        frames = [frame for frame in range(31) if frame not in (10, 11, 12)]
        raw = SimpleNamespace(
            x=[2.0 * frame for frame in frames], y=[0.0] * len(frames), timeframe=frames,
            dataset="d", track_id=1,
        )
        series = analysis._compute_msd(SimpleNamespace(raw=raw, group="G"), AnalysisParameters())
        self.assertEqual(series.lag_frames, tuple(float(lag) for lag in range(1, 16)))
        # Straight-line motion at 2 px per frame: MSD = (2 k)^2 at every lag.
        self.assertEqual(series.values, tuple(4.0 * lag * lag for lag in range(1, 16)))
        self.assertAlmostEqual(series.alpha, 2.0, places=9)

    def test_alpha_is_tested_and_exported(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = _bundle(Path(directory))
        rows = [row for row in analysis.statistical_results(bundle, ["msd_alpha"])]
        self.assertEqual({row.metric for row in rows}, {"msd_alpha"})
        self.assertIsNotNone(next(row for row in rows if row.test == "Kruskal-Wallis").p_value)
        self.assertIn(b"msd_alpha", analysis.statistics_to_csv(bundle))


class TemporalTests(unittest.TestCase):
    def test_band_treats_samples_as_units(self):
        from types import SimpleNamespace

        def track(dataset, speed, frames=(0, 1, 2)):
            return SimpleNamespace(raw=SimpleNamespace(dataset=dataset, timeframe=list(frames), speed=[speed] * len(frames)))

        # Sample "a" has nine tracks at speed 1, sample "b" one track at 3.
        # The line is the mean over tracks (1.2); the band is the
        # sample-clustered SEM, sqrt(2 * (1.8² + 1.8²)) / 10 = 0.36, wider
        # than the 0.2 that treating the ten tracks as independent gives.
        tracks = [track("a", 1.0) for _ in range(9)] + [track("b", 3.0)]
        frames, mean, sem = figures._temporal_values(tracks, "speed")
        self.assertEqual(list(frames), [0.0, 1.0, 2.0])
        np.testing.assert_allclose(mean, [1.2, 1.2, 1.2])
        self.assertAlmostEqual(sem[0], 0.36)
        # With one track per sample it is the ordinary SEM.
        _frames, mean, sem = figures._temporal_values([track("a", 1.0), track("b", 2.0), track("c", 6.0)], "speed")
        self.assertAlmostEqual(mean[0], 3.0)
        self.assertAlmostEqual(sem[0], np.std([1.0, 2.0, 6.0], ddof=1) / np.sqrt(3))
        # A frame reached by fewer than half of the samples is left out.
        frames, _mean, _sem = figures._temporal_values(tracks + [track("c", 2.0, (0, 1, 2, 3)), track("d", 2.0)], "speed")
        self.assertEqual(list(frames), [0.0, 1.0, 2.0])


class StyleModelTests(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(FigureStyle(formats=["pdf", "png", "pdf"]).formats, ["png", "pdf"])
        self.assertEqual(FigureStyle(text={"Same": "Same"}).text, {})
        self.assertFalse(FigureStyle().changes_rendering())
        self.assertTrue(FigureStyle(text={"A": "B"}).changes_rendering())
        for bad in (
            {"group_colors": {"A": "red"}},
            {"type_colors": {"Wandering": "#000000"}},
            {"dpi": 5000},
            {"formats": ["jpg"]},
            {"composites": [{"name": "X", "columns": [["no_such_panel"]]}]},
            {"composites": [{"name": "X", "columns": [[]]}]},
            {"composites": [{"name": "Same", "columns": [["msd_long"]]}, {"name": "same", "columns": [["msd_all"]]}]},
        ):
            with self.assertRaises(ValidationError, msg=bad):
                FigureStyle(**bad)

    def test_options_describe_every_block_and_preset(self):
        options = style_options()
        self.assertEqual(set(options["blocks"]), set(figures.BLOCKS))
        for preset in options["presets"].values():
            CompositeFigure(**preset["composite"])


class _Figure(bytes):
    extras = {"pdf": b"%PDF-1.4 test"}
    texts = ("Title",)


class StorageAndRouteTests(unittest.TestCase):
    def test_store_writes_alternate_formats_and_style(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = AnalysisStore(Path(temporary))
            manifest = store.create(
                {"figure_03_trajectory_classification.png": _Figure(b"png-data")},
                b"csv",
                [{"name": "A", "datasets": ["x"], "tracks": 1}],
                DEFAULT_ANALYSIS_PARAMETERS.model_dump(),
                style=FigureStyle(dpi=300).model_dump(),
                derived_from="0123456789abcdef",
            )
            artifact_id = manifest["id"]
            image = manifest["images"][0]
            self.assertEqual(image["formats"], ["png", "pdf"])
            self.assertEqual(image["texts"], ["Title"])
            self.assertEqual(manifest["style"]["dpi"], 300)
            self.assertEqual(manifest["derived_from"], "0123456789abcdef")
            pdf = store.image_file(artifact_id, "figure_03_trajectory_classification.pdf")
            self.assertEqual(pdf.read_bytes(), b"%PDF-1.4 test")
            payload = analysis_payload(store.read(artifact_id))
            self.assertTrue(payload["images"][0]["downloads"]["pdf"].endswith("figure_03_trajectory_classification.pdf/download"))
            with zipfile.ZipFile(store.data_file(artifact_id, "archive_file")) as archive:
                self.assertIn("figure_03_trajectory_classification.pdf", archive.namelist())
            with self.assertRaises(Exception):
                store.image_file(artifact_id, "figure_03_trajectory_classification.svg")
        self.assertEqual(media_type("a.svg"), "image/svg+xml")
        self.assertEqual(media_type("a.tiff"), "image/tiff")

    def test_older_manifests_get_default_style_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_id = "b" * 16
            (root / artifact_id).mkdir()
            (root / artifact_id / "manifest.json").write_text(json.dumps({
                "schema_version": 4, "id": artifact_id, "created_at": "2026-01-01T00:00:00+00:00",
                "images": [{"name": "figure.png", "title": "Figure"}], "groups": [],
            }), encoding="utf-8")
            manifest = AnalysisStore(root).read(artifact_id)
            self.assertEqual(manifest["images"][0]["formats"], ["png"])
            self.assertEqual(manifest["images"][0]["texts"], [])
            self.assertEqual(manifest["style"], FigureStyle().model_dump())
            self.assertIsNone(manifest["derived_from"])

    def test_task_manager_passes_style_only_when_given(self):
        calls = []

        def create(groups, parameters, **extra):
            calls.append(extra)
            return {"id": "c" * 16, "images": []}

        manager = AnalysisTaskManager(creator=create)
        try:
            manager.submit([("A", ["one"]), ("B", ["two"])], AnalysisParameters())
            manager.submit([("A", ["one"]), ("B", ["two"])], AnalysisParameters(), style=FigureStyle(dpi=200), derived_from="d" * 16)
        finally:
            manager.shutdown()
        self.assertEqual(calls[0], {})
        self.assertEqual(calls[1]["style"].dpi, 200)
        self.assertEqual(calls[1]["derived_from"], "d" * 16)

    def test_rerender_keeps_every_group(self):
        manifest = {"groups": [{"name": "A", "datasets": ["a/1"]}, {"name": "B", "datasets": ["b/1", "b/2"]}]}
        datasets = {
            "id-a1": type("D", (), {"id": "id-a1", "relative_path": "a/1"})(),
            "id-b1": type("D", (), {"id": "id-b1", "relative_path": "b/1"})(),
            "id-b2": type("D", (), {"id": "id-b2", "relative_path": "b/2"})(),
        }
        with patch("celltrack.analysis.service.dataset_map", return_value=datasets):
            self.assertEqual(
                rerender_groups(manifest, [(1, "Beta"), (0, "Alpha")]),
                [("Beta", ["id-b1", "id-b2"]), ("Alpha", ["id-a1"])],
            )
            for order in ([(0, "A")], [(0, "A"), (0, "B")], [(0, "A"), (2, "B")]):
                with self.assertRaises(ValueError):
                    rerender_groups(manifest, order)
        with patch("celltrack.analysis.service.dataset_map", return_value={}):
            with self.assertRaisesRegex(ValueError, "no longer available"):
                rerender_groups(manifest, [(0, "A"), (1, "B")])

    def test_rerender_endpoint_submits_a_new_analysis(self):
        from fastapi.testclient import TestClient
        from celltrack.web.app import app
        from celltrack.web.routes import analysis as routes

        manifest = {
            "id": "e" * 16,
            "groups": [{"name": "A", "datasets": ["a/1"]}, {"name": "B", "datasets": ["b/1"]}],
            "parameters": DEFAULT_ANALYSIS_PARAMETERS.model_dump(),
        }
        submitted = {}

        def submit(groups, parameters, style=None, derived_from=None):
            submitted.update(groups=groups, parameters=parameters, style=style, derived_from=derived_from)
            return type("Task", (), {
                "id": "t1", "groups": [], "parameters": {}, "status": "queued", "message": "", "error": None,
                "result": None, "created_at": "", "completed_at": None, "derived_from": derived_from,
            })()

        client = TestClient(app)
        with (
            patch.object(routes.analysis_store, "read", return_value=manifest),
            patch.object(routes, "rerender_groups", side_effect=lambda m, order: [(name, [str(index)]) for index, name in order]),
            patch.object(routes.analysis_tasks, "submit", side_effect=submit),
        ):
            response = client.post(f"/api/analysis/{'e' * 16}/rerender", json={
                "groups": [{"source_index": 1, "name": "Second"}, {"source_index": 0, "name": "First"}],
                "style": {"dpi": 300, "formats": ["pdf"]},
            })
            self.assertEqual(response.status_code, 202, response.text)
            self.assertEqual(response.json()["derived_from"], "e" * 16)
            self.assertEqual(submitted["groups"], [("Second", ["1"]), ("First", ["0"])])
            self.assertEqual(submitted["style"].formats, ["png", "pdf"])
            self.assertEqual(submitted["parameters"], AnalysisParameters(**manifest["parameters"]))
            bad = client.post(f"/api/analysis/{'e' * 16}/rerender", json={"style": {"dpi": 1}})
            self.assertEqual(bad.status_code, 422)
            scaled = client.post(f"/api/analysis/{'e' * 16}/rerender", json={
                "units": {"microns_per_pixel": 0.5, "frame_interval_minutes": 10},
            })
            self.assertEqual(scaled.status_code, 202, scaled.text)
            self.assertEqual(submitted["parameters"].microns_per_pixel, 0.5)
            self.assertEqual(submitted["parameters"].frame_interval_minutes, 10)
            self.assertEqual(submitted["parameters"].long_track_min_observations, 50)
        options = client.get("/api/analysis/options").json()
        self.assertIn("presets", options["style"])


if __name__ == "__main__":
    unittest.main()
