from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import zipfile
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from celltrack.analysis import compute as analysis
from celltrack.analysis import figures
from celltrack.analysis.jobs import AnalysisTaskManager
from celltrack.analysis.models import AnalysisParameters, DEFAULT_ANALYSIS_PARAMETERS
from celltrack.analysis.storage import AnalysisNotFoundError, AnalysisStore, analysis_payload
from celltrack.datasets import discover_datasets, require_datasets
from celltrack.jobs.manager import Job, JobCancelled, JobManager, SEGMENT_PROGRESS_RE


class DatasetDiscoveryTests(unittest.TestCase):
    def test_any_leaf_folder_with_images_is_a_dataset(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "A" / "one").mkdir(parents=True)
            (root / "custom" / "deep" / "two").mkdir(parents=True)
            (root / "empty").mkdir()
            (root / "A" / "one" / "frame1.JPG").touch()
            (root / "custom" / "deep" / "two" / "frame2.tif").touch()
            datasets = discover_datasets(root)
            self.assertEqual([item.relative_path for item in datasets], ["A/one", "custom/deep/two"])
            self.assertEqual(len({item.id for item in datasets}), 2)

    def test_unknown_dataset_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "Unknown dataset"):
                require_datasets(["missing"], Path(temporary))


class JobProgressTests(unittest.TestCase):
    def test_segmentation_progress_line_is_parsed(self):
        match = SEGMENT_PROGRESS_RE.match("[23/144] frame-023.jpg: 4 instances")
        self.assertIsNotNone(match)
        self.assertEqual(match.groups(), ("23", "144"))

    def test_command_output_is_streamed_line_by_line(self):
        lines = []
        manager = JobManager()
        manager._run_command(
            [sys.executable, "-u", "-c", "print('[1/2] first'); print('[2/2] second')"],
            on_line=lines.append,
        )
        self.assertEqual(lines, ["[1/2] first", "[2/2] second"])

    def test_running_process_can_be_cancelled(self):
        import threading
        manager = JobManager()
        job = Job(id="cancel-test", kind="segmentation", dataset_ids=["sample"], status="running")
        manager._jobs[job.id] = job
        started = threading.Event()
        errors = []

        def run_command():
            try:
                manager._run_command(
                    [sys.executable, "-u", "-c", "import time; print('ready'); time.sleep(30)"],
                    on_line=lambda _line: started.set(),
                    job=job,
                )
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=run_command)
        worker.start()
        self.assertTrue(started.wait(3))
        manager.cancel(job.id)
        worker.join(3)
        manager._executor.shutdown(wait=True)
        self.assertFalse(worker.is_alive())
        self.assertTrue(job.cancel_requested)
        self.assertEqual(job.status, "cancelling")
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], JobCancelled)


class AnalysisTests(unittest.TestCase):
    def test_analysis_task_runs_in_background_and_exposes_result(self):
        started = threading.Event()
        release = threading.Event()

        def create(groups, parameters):
            started.set()
            release.wait(3)
            return {
                "id": "a" * 16,
                "created_at": "2026-01-01T00:00:00+00:00",
                "images": [],
                "groups": [{"name": name, "datasets": ids} for name, ids in groups],
                "parameters": parameters.model_dump(),
            }

        manager = AnalysisTaskManager(creator=create)
        try:
            task = manager.submit(
                [("A", ["one"]), ("B", ["two"])],
                AnalysisParameters(figure_types=["cell_appearance"]),
            )
            self.assertTrue(started.wait(1))
            self.assertEqual(manager.get(task.id).status, "running")
            release.set()
            for _attempt in range(100):
                if manager.get(task.id).status == "completed":
                    break
                threading.Event().wait(0.01)
            completed = manager.get(task.id)
            self.assertEqual(completed.status, "completed")
            self.assertEqual(completed.groups[0]["dataset_count"], 1)
            self.assertEqual(completed.parameters["figure_types"], ["cell_appearance"])
            self.assertEqual(completed.result["result_url"], "/analysis/aaaaaaaaaaaaaaaa")
        finally:
            release.set()
            manager.shutdown()

    def test_analysis_task_surfaces_generation_errors(self):
        def fail(_groups, _parameters):
            raise ValueError("invalid comparison")

        manager = AnalysisTaskManager(creator=fail)
        try:
            task = manager.submit(
                [("A", ["one"]), ("B", ["two"])],
                AnalysisParameters(figure_types=["cell_appearance"]),
            )
            for _attempt in range(100):
                if manager.get(task.id).status == "failed":
                    break
                threading.Event().wait(0.01)
            failed = manager.get(task.id)
            self.assertEqual(failed.status, "failed")
            self.assertEqual(failed.error, "invalid comparison")
        finally:
            manager.shutdown()

    def test_tracking_csv_produces_metrics(self):
        with tempfile.TemporaryDirectory() as temporary:
            csv_path = Path(temporary) / "tracking.csv"
            csv_path.write_text(
                "track_id,timeframe,x,y,polygon,bbox,detection_id,is_interpolated\n"
                '1,1,0,0,"[(0, 0), (2, 0), (2, 2), (0, 2)]",,1,False\n'
                '1,3,3,4,"[(3, 4), (5, 4), (5, 6), (3, 6)]",,2,False\n',
                encoding="utf-8",
            )
            bundle = analysis.prepare_analysis([("A", [("sample", csv_path)])])
            summaries = bundle.summaries_for("A", prefer_long=False)
            self.assertEqual(len(summaries), 1)
            self.assertAlmostEqual(summaries[0].mean_speed, 2.5)
            self.assertAlmostEqual(summaries[0].directionality, 1.0)
            self.assertAlmostEqual(summaries[0].mean_area, 4.0)

    def test_shared_bundle_renders_configured_figures_for_every_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            csv_path = Path(temporary) / "tracking.csv"
            rows = ["track_id,timeframe,x,y,polygon,bbox,detection_id,is_interpolated"]
            for track_id in (1, 2):
                for frame in range(1, 13):
                    x = frame * (track_id + 0.5)
                    y = frame * frame * 0.08 + track_id
                    polygon = [(x, y), (x + 3, y), (x + 3, y + 2), (x, y + 2)]
                    rows.append(f'{track_id},{frame},{x},{y},"{polygon}",,1,False')
            csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
            parameters = AnalysisParameters(figure_types=["cell_appearance", "representatives", "group_trajectories"])
            bundle = analysis.prepare_analysis([
                ("A", [("sample", csv_path)]),
                ("B", [("sample", csv_path)]),
            ], parameters)
            self.assertEqual(bundle.source_files_read, 1)
            rendered = figures.render_all_figures(bundle)
            self.assertEqual(len(rendered), 5)
            self.assertEqual(sum("representatives" in name for name in rendered), 2)
            self.assertEqual(sum("trajectories" in name for name in rendered), 2)
            self.assertTrue(all(content.startswith(b"\x89PNG\r\n\x1a\n") for content in rendered.values()))

    def test_new_distribution_and_msd_summary_figures_render(self):
        with tempfile.TemporaryDirectory() as temporary:
            csv_path = Path(temporary) / "tracking.csv"
            rows = ["track_id,timeframe,x,y,polygon"]
            for track_id in range(1, 5):
                for frame in range(1, 15):
                    x = frame * track_id
                    y = (frame % (track_id + 2)) * track_id
                    rows.append(f"{track_id},{frame},{x},{y},")
            csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
            parameters = AnalysisParameters(
                long_track_min_observations=3,
                figure_types=["turning_angle_distribution", "msd_summary"],
            )
            bundle = analysis.prepare_analysis([
                ("A", [("sample-a", csv_path)]),
                ("B", [("sample-b", csv_path)]),
            ], parameters)
            rendered = figures.render_all_figures(bundle)
            self.assertEqual(list(rendered), [
                "figure_01_turning_angle_distribution.png",
                "figure_02_msd_summary.png",
            ])
            self.assertTrue(all(content.startswith(b"\x89PNG\r\n\x1a\n") for content in rendered.values()))

    def test_msd_summary_applies_calibration_and_sparse_tail_filter(self):
        from celltrack.analysis.compute import AnalysisBundle, GroupedTrack, MsdSeries, RawTrack, msd_summary

        parameters = AnalysisParameters(
            frame_interval_minutes=5,
            microns_per_pixel=0.5,
            figure_types=["msd_summary"],
        )
        tracks = tuple(
            GroupedTrack("A", RawTrack(
                f"sample-{track_id}", track_id, tuple(range(1, 31)), tuple(range(30)), tuple(range(30)),
                (), (), (), (), (), (), (), (), (), (),
            ))
            for track_id in range(1, 6)
        )
        bundle = AnalysisBundle(
            groups=("A",),
            tracks=tracks,
            summaries=(),
            msd=tuple(
                MsdSeries(
                    "A", f"sample-{track_id}", track_id,
                    (10.0, 20.0) if track_id <= 2 else (10.0,),
                    (100.0, 400.0) if track_id <= 2 else (100.0,),
                    None,
                )
                for track_id in range(1, 6)
            ),
            source_files_read=0,
            parameters=parameters,
        )
        points = msd_summary(bundle, "A")
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0].lag, 50.0)
        self.assertEqual(points[0].mean, 25.0)
        self.assertEqual(points[0].n, 5)
        unfiltered = msd_summary(bundle, "A", filter_sparse_tail=False)
        self.assertEqual(unfiltered[1].lag, 100.0)
        self.assertEqual(unfiltered[1].mean, 100.0)

    def test_single_track_msd_summary_has_mean_without_confidence_interval(self):
        from celltrack.analysis.compute import AnalysisBundle, GroupedTrack, MsdSeries, RawTrack, msd_summary

        parameters = AnalysisParameters(figure_types=["msd_summary"])
        track = GroupedTrack("A", RawTrack(
            "sample", 1, tuple(range(1, 11)), tuple(range(10)), tuple(range(10)),
            (), (), (), (), (), (), (), (), (), (),
        ))
        bundle = AnalysisBundle(
            ("A",), (track,), (), (MsdSeries("A", "sample", 1, (1.0,), (12.0,), None),), 0, parameters,
        )
        points = msd_summary(bundle, "A")
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0].mean, 12.0)
        self.assertIsNone(points[0].ci_low)
        self.assertIsNone(points[0].ci_high)

    def test_msd_summary_averages_tracks_within_each_dataset_first(self):
        from celltrack.analysis.compute import GroupedTrack, MsdSeries, RawTrack, msd_summary

        def track(dataset, track_id):
            return GroupedTrack("A", RawTrack(
                dataset, track_id, tuple(range(1, 11)), tuple(range(10)), tuple(range(10)),
                (), (), (), (), (), (), (), (), (), (),
            ))

        tracks = (track("one", 1), track("one", 2), track("two", 3))
        series = (
            MsdSeries("A", "one", 1, (1.0,), (10.0,), None),
            MsdSeries("A", "one", 2, (1.0,), (30.0,), None),
            MsdSeries("A", "two", 3, (1.0,), (100.0,), None),
        )
        bundle = analysis.AnalysisBundle(
            ("A",), tracks, (), series, 0, AnalysisParameters(figure_types=["msd_summary"]),
        )
        points = msd_summary(bundle, "A")
        self.assertEqual(points[0].mean, 60.0)
        self.assertEqual(points[0].n, 2)

    def test_turning_angle_distribution_weights_datasets_equally(self):
        from celltrack.analysis.compute import AnalysisBundle, GroupedTrack, RawTrack, turning_angle_distribution

        def track(dataset, track_id, turning):
            return GroupedTrack("A", RawTrack(
                dataset, track_id, tuple(range(50)), tuple(range(50)), tuple(range(50)),
                (), (), (), (), tuple(turning), (), (), (), (), (),
            ))

        tracks = (
            track("short", 1, [5.0]),
            track("long", 2, [175.0] * 100),
        )
        bundle = AnalysisBundle(
            ("A",), tracks, (), (), 0,
            AnalysisParameters(figure_types=["turning_angle_distribution"]),
        )
        points = turning_angle_distribution(bundle, "A")
        self.assertEqual(len(points), 18)
        self.assertEqual(points[0].angle_degrees, 5.0)
        self.assertAlmostEqual(points[0].mean_density, 0.05)
        self.assertAlmostEqual(points[-1].mean_density, 0.05)
        self.assertEqual(points[0].n, 2)

    def test_bundle_trajectory_and_polar_scales_are_shared(self):
        from celltrack.analysis.compute import AnalysisBundle, GroupedTrack, RawTrack, TrackSummary

        parameters = AnalysisParameters(angle_bins=4, figure_types=["group_trajectories"])

        def raw(dataset, track_id, distance):
            return RawTrack(
                dataset, track_id, (1, 2), (0.0, distance), (0.0, 0.0),
                (), (), (), (), (), (), (), (), (), (),
            )

        def summary(group, dataset, track_id, angle):
            return analysis.TrackSummary(
                group, dataset, track_id, 50, 1, 50, 1, 1, 1, 1, 1, 1,
                1, 1, 1, 1, 1, "Directed", angle,
            )

        tracks = (
            GroupedTrack("A", raw("a", 1, 300.0)),
            GroupedTrack("B", raw("b", 2, 100.0)),
        )
        summaries = (
            summary("A", "a", 1, 0.0),
            summary("B", "b", 2, 0.0),
        )
        bundle = AnalysisBundle(("A", "B"), tracks, summaries, (), 0, parameters)
        self.assertAlmostEqual(figures._bundle_trajectory_limit(bundle), 324.0)
        _edges, radial_limit = figures._angle_histogram_scale(bundle)
        self.assertAlmostEqual(radial_limit, 1.05)

    def test_long_track_filter_uses_at_least_fifty_observations(self):
        with tempfile.TemporaryDirectory() as temporary:
            csv_path = Path(temporary) / "tracking.csv"
            rows = ["track_id,timeframe,x,y,polygon"]
            for track_id, count in ((1, 49), (2, 50)):
                rows.extend(f"{track_id},{frame},{frame},{track_id}," for frame in range(1, count + 1))
            csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
            bundle = analysis.prepare_analysis([("A", [("sample", csv_path)])])
            selected = bundle.tracks_for("A", prefer_long=True)
            self.assertEqual([(track.raw.track_id, len(track.raw.timeframe)) for track in selected], [(2, 50)])

    def test_analysis_artifact_is_persisted_discoverable_and_deleted(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = AnalysisStore(Path(temporary))
            manifest = store.create(
                {"figure_01_cell_appearance.png": b"png-data"},
                b"csv-data",
                [{"name": "A", "datasets": ["sample"], "tracks": 1}],
                DEFAULT_ANALYSIS_PARAMETERS.model_dump(),
            )
            artifact_id = manifest["id"]
            payload = analysis_payload(store.read(artifact_id))
            self.assertEqual(payload["result_url"], f"/analysis/{artifact_id}")
            self.assertEqual(manifest["schema_version"], 4)
            self.assertIn("statistics_url", payload)
            self.assertEqual(store.list()[0]["id"], artifact_id)
            self.assertTrue(store.data_file(artifact_id, "archive_file").is_file())
            with zipfile.ZipFile(store.data_file(artifact_id, "archive_file")) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {"figure_01_cell_appearance.png", "cell-tracking-metrics.csv", "cell-tracking-statistics.csv"},
                )
            store.delete(artifact_id)
            self.assertFalse((Path(temporary) / artifact_id).exists())
            with self.assertRaises(AnalysisNotFoundError):
                store.read(artifact_id)

    def test_legacy_manifest_receives_default_parameters(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_id = "a" * 16
            directory = root / artifact_id
            directory.mkdir()
            (directory / "manifest.json").write_text(json.dumps({
                "id": artifact_id, "created_at": "2026-01-01T00:00:00+00:00",
                "images": ["figure.png"], "groups": [],
            }), encoding="utf-8")
            manifest = AnalysisStore(root).read(artifact_id)
            self.assertEqual(manifest["parameters"]["long_track_min_observations"], 50)
            self.assertEqual(manifest["images"][0]["name"], "figure.png")
            self.assertNotIn("statistics_file", manifest)
            self.assertNotIn("statistics_url", analysis_payload(manifest))

    def test_analysis_parameter_constraints(self):
        with self.assertRaises(ValidationError):
            AnalysisParameters(random_directionality_max=0.7, directed_directionality_min=0.5)
        with self.assertRaises(ValidationError):
            AnalysisParameters(figure_types=[])
        with self.assertRaises(ValidationError):
            AnalysisParameters(msd_fit_points=3, msd_min_fit_points=4)
        self.assertIsNone(AnalysisParameters().frame_interval_minutes)
        self.assertEqual(AnalysisParameters(frame_interval_minutes=5).frame_interval_minutes, 5)
        self.assertEqual(AnalysisParameters(microns_per_pixel=0.5).microns_per_pixel, 0.5)
        for field in ("frame_interval_minutes", "microns_per_pixel"):
            with self.assertRaises(ValidationError):
                AnalysisParameters(**{field: 0})
            with self.assertRaises(ValidationError):
                AnalysisParameters(**{field: -1})

    def test_statistical_results_and_csv_include_pairwise_adjustment_and_effects(self):
        from celltrack.analysis.compute import AnalysisBundle, TrackSummary, statistical_results, statistics_to_csv

        def summary(group, dataset, track_id, value):
            return TrackSummary(
                group, dataset, track_id, 50, 1, 50, value, value, value, value,
                value, value, value, value, value, value, value, "Mixed", 0.0,
            )

        parameters = AnalysisParameters(
            summary_metrics=["mean_speed"],
            figure_types=["parameter_distributions"],
        )
        summaries = tuple(
            summary(group, f"{group}-{index}", index, offset + index)
            for group, offset in (("A", 0), ("B", 20), ("C", 40))
            for index in range(1, 7)
        )
        bundle = AnalysisBundle(("A", "B", "C"), (), summaries, (), 0, parameters)
        results = statistical_results(bundle)
        self.assertEqual(results[0].test, "Kruskal-Wallis")
        self.assertEqual(results[0].unit, "dataset_mean")
        self.assertEqual(results[0].group_1, "A|B|C")
        self.assertEqual(results[0].effect_size_type, "epsilon-squared")
        pairwise = [result for result in results if result.test == "Mann-Whitney U"]
        self.assertEqual(len(pairwise), 3)
        self.assertTrue(all(result.p_adjusted is not None for result in pairwise))
        self.assertTrue(all(result.effect_size_type == "rank-biserial correlation" for result in pairwise))
        parsed = list(csv.DictReader(io.StringIO(statistics_to_csv(bundle).decode("utf-8-sig"))))
        self.assertEqual(list(parsed[0]), [
            "metric", "test", "unit", "group_1", "group_2", "n_1", "n_2", "statistic",
            "p_value", "p_adjusted", "effect_size", "effect_size_type",
        ])

    def test_statistics_do_not_treat_tracks_as_independent_replicates(self):
        from celltrack.analysis.compute import AnalysisBundle, TrackSummary, statistical_results

        def summary(group, track_id, value):
            return TrackSummary(
                group, f"{group}-dataset", track_id, 50, 1, 50, value, value, value, value,
                value, value, value, value, value, value, value, "Mixed", 0.0,
            )

        summaries = tuple(
            summary(group, track_id, offset + track_id)
            for group, offset in (("A", 0), ("B", 100))
            for track_id in range(1, 21)
        )
        parameters = AnalysisParameters(summary_metrics=["mean_speed"], figure_types=["parameter_distributions"])
        result = statistical_results(AnalysisBundle(("A", "B"), (), summaries, (), 0, parameters))[0]
        self.assertEqual((result.n_1, result.n_2), (1, 1))
        self.assertIsNone(result.p_value)

    def test_analysis_rejects_a_dataset_in_multiple_groups(self):
        from celltrack.analysis.service import create_analysis

        with self.assertRaisesRegex(ValueError, "Each dataset can belong to only one comparison group"):
            create_analysis(
                [("A", ["duplicate"]), ("B", ["duplicate"])],
                AnalysisParameters(figure_types=["cell_appearance"]),
            )


class TrackingIntegrationTests(unittest.TestCase):
    def test_separate_segmentation_labels_feed_tracking(self):
        from PIL import Image

        from celltrack.pipelines import tracking as module

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            images = base / "images"
            labels = base / "labels"
            images.mkdir()
            labels.mkdir()
            for frame in range(1, 4):
                stem = f"frame-{frame}"
                Image.new("RGB", (100, 100), "white").save(images / f"{stem}.png")
                x = 0.20 + frame * 0.01
                labels.joinpath(f"{stem}.txt").write_text(
                    f"0 {x:.2f} 0.20 {x + .10:.2f} 0.20 {x + .10:.2f} 0.30 {x:.2f} 0.30\n",
                    encoding="utf-8",
                )
            detections = module.load_dataset(images, labels)
            tracks = module.run_tracking_from_detections(
                detections,
                module.TrackingConfig([5, 8, 12], {5, 8, 12}, max_lookback=2, min_track_length=3),
            )
            self.assertEqual(len(tracks), 3)
            self.assertEqual(len({row.track_id for row in tracks}), 1)


class ResultVisualizationTests(unittest.TestCase):
    def test_segmentation_and_tracking_overlays_render_as_jpeg(self):
        from types import SimpleNamespace
        from PIL import Image
        from celltrack.web.visualization import render_segmentation, render_tracking

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            labels = base / "labels"
            labels.mkdir()
            images = []
            for frame in range(1, 3):
                image_path = base / f"frame-{frame}.png"
                Image.new("RGB", (100, 80), "white").save(image_path)
                images.append(image_path)
                labels.joinpath(f"frame-{frame}.txt").write_text(
                    "0 0.2 0.2 0.4 0.2 0.4 0.4 0.2 0.4\n",
                    encoding="utf-8",
                )
            tracking_csv = base / "tracking.csv"
            tracking_csv.write_text(
                "track_id,timeframe,x,y,polygon\n"
                '1,1,30,24,"[(20, 16), (40, 16), (40, 32), (20, 32)]"\n'
                '1,2,34,26,"[(24, 18), (44, 18), (44, 34), (24, 34)]"\n',
                encoding="utf-8",
            )
            dataset = SimpleNamespace(images=tuple(images), labels_dir=labels, tracking_csv=tracking_csv)
            segmentation = render_segmentation(dataset, 1)
            tracking = render_tracking(dataset, 2)
            self.assertEqual(segmentation[:2], b"\xff\xd8")
            self.assertEqual(tracking[:2], b"\xff\xd8")
            self.assertGreater(len(segmentation), 1000)
            self.assertGreater(len(tracking), 1000)


if __name__ == "__main__":
    unittest.main()
