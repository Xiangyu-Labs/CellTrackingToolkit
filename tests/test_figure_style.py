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
    return f"{float(value):.6g}"


def _fingerprint(fig) -> str:
    """Hash of what the figure shows: coordinates and axis ranges, not looks."""
    parts = []
    for ax in fig.get_axes():
        if not ax.get_visible():
            parts.append("hidden-axes")
            continue
        parts.append("xlim " + " ".join(map(_number, ax.get_xlim())))
        parts.append("ylim " + " ".join(map(_number, ax.get_ylim())))
        for line in ax.get_lines():
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
        for expected in ("Instantaneous speed (µm/min)", "Cell area (µm²)", "Delta x (µm)", "Time (h)", "MSD (µm²)", "Time lag (min)"):
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
        for expected in ("Trajectory classification", "Composition", "Alpha", "Directionality distribution"):
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
                    [line.get_xydata().tolist() for line in mine.get_lines()],
                    [line.get_xydata().tolist() for line in theirs.get_lines()],
                )
                self.assertEqual(mine.get_ylim(), theirs.get_ylim())
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
