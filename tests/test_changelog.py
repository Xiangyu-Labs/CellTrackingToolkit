from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from celltrack import __version__
from celltrack.changelog import CHANGELOG_PATH, parse_changelog
from celltrack.web.app import app


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


class ChangelogTests(unittest.TestCase):
    def test_parser_joins_wrapped_lines_and_skips_empty_sections(self):
        releases = parse_changelog(
            "# Changelog\n\nIntro text.\n\n## Unreleased\n\n"
            "## 1.0.1 — 2026-01-02 10:00 (UTC+8)\n\n- First line\n  continues here.\n- Second.\n"
        )

        self.assertEqual(
            releases,
            [{
                "version": "1.0.1",
                "released": "2026-01-02 10:00 (UTC+8)",
                "changes": ["First line continues here.", "Second."],
            }],
        )

    def test_changelog_is_newest_first_and_matches_the_current_version(self):
        releases = parse_changelog(CHANGELOG_PATH.read_text(encoding="utf-8"))
        versions = [release["version"] for release in releases if release["version"] != "Unreleased"]

        self.assertEqual(versions, sorted(versions, key=version_key, reverse=True))
        self.assertEqual(versions[0], __version__)
        for release in releases:
            if release["version"] != "Unreleased":
                self.assertRegex(release["released"], r"^\d{4}-\d\d-\d\d \d\d:\d\d \(UTC\+8\)$")

    def test_changelog_endpoint_returns_releases(self):
        response = TestClient(app).get("/api/changelog")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(any(release["version"] == __version__ for release in response.json()))


if __name__ == "__main__":
    unittest.main()
