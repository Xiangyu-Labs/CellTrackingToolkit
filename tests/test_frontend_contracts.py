from __future__ import annotations

from pathlib import Path
import re
import unittest

from fastapi.testclient import TestClient

from celltrack.web.app import app


ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "celltrack" / "web" / "static"


class FrontendContractTests(unittest.TestCase):
    def read_static(self, relative_path: str) -> str:
        return (STATIC / relative_path).read_text(encoding="utf-8")

    def test_main_page_modules_share_one_state_url(self):
        state_urls = []
        for module in ("js/app.js", "js/workflow.js", "js/compare.js"):
            source = self.read_static(module)
            match = re.search(r'from "(\./state\.js\?v=\d+)"', source)
            self.assertIsNotNone(match, f"{module} must import the shared state module")
            state_urls.append(match.group(1))

        self.assertEqual(state_urls, ["./state.js?v=5"] * 3)

    def test_navigation_uses_process_and_analysis_tabs(self):
        index = self.read_static("index.html")
        state = self.read_static("js/state.js")
        result = self.read_static("analysis-result.html")

        self.assertIn('data-tab="process"', index)
        self.assertIn('data-tab="analysis"', index)
        self.assertNotIn('data-tab="compare"', index)
        self.assertNotIn('class="sidebar"', index)
        self.assertIn('new Set(["process", "analysis"])', state)
        self.assertNotIn('"compare"', state)
        self.assertIn('href="/?tab=analysis"', result)

    def test_main_page_asset_versions_are_coordinated(self):
        index = self.read_static("index.html")
        app = self.read_static("js/app.js")

        for asset in (
            "/static/css/components.css?v=7",
            "/static/css/layout.css?v=7",
            "/static/js/app.js?v=16",
        ):
            self.assertIn(asset, index)
        self.assertIn('"./workflow.js?v=10"', app)
        self.assertIn('"./compare.js?v=10"', app)
        self.assertIn('"./strings.js?v=5"', app)

    def test_result_viewer_has_download_links_and_updates_frame_url(self):
        index = self.read_static("index.html")
        app = self.read_static("js/app.js")

        self.assertIn('id="downloadCurrentFrame"', index)
        self.assertIn('id="downloadAllResults"', index)
        self.assertIn('aria-label="Download current frame"', index)
        self.assertIn('aria-label="Download all results"', index)
        self.assertIn("results/${viewer.kind}/frames/${viewer.index}/download", app)
        self.assertIn("results/${kind}/download", app)
        self.assertIn('$("#downloadCurrentFrame").removeAttribute("href")', app)

    def test_parameter_distributions_require_a_main_grid_metric(self):
        compare = self.read_static("js/compare.js")
        self.assertIn(
            'parameters.summary_metrics.some(metric => metric !== "turning_angle_std")',
            compare,
        )


class WebCacheTests(unittest.TestCase):
    def test_pages_and_static_assets_are_not_cached(self):
        client = TestClient(app)

        for path in (
            "/",
            "/analysis/tasks/pending",
            "/static/js/app.js?v=16",
            "/static/css/layout.css?v=7",
        ):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"], "no-store")

    def test_api_responses_keep_their_own_cache_policy(self):
        response = TestClient(app).get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("cache-control", response.headers)


if __name__ == "__main__":
    unittest.main()
