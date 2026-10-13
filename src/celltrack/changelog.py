"""Read CHANGELOG.md for the Updates tab and stamp it at release time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

from celltrack.settings import PROJECT_ROOT


CHANGELOG_PATH = PROJECT_ROOT / "CHANGELOG.md"
UNRELEASED = "Unreleased"
RELEASE_TIMEZONE = timezone(timedelta(hours=8), "UTC+8")
HEADING = re.compile(r"^## (?P<version>Unreleased|\d+\.\d+\.\d+)(?: — (?P<released>.+))?$")


def parse_changelog(text: str) -> list[dict]:
    """Return releases in file order (newest first), skipping empty sections."""
    releases: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        heading = HEADING.match(line.strip())
        if heading:
            current = {
                "version": heading["version"],
                "released": heading["released"],
                "changes": [],
            }
            releases.append(current)
        elif line.startswith("## "):
            current = None
        elif current is not None and line.startswith("- "):
            current["changes"].append(line[2:].strip())
        elif current is not None and current["changes"] and line.startswith("  ") and line.strip():
            current["changes"][-1] += " " + line.strip()
    return [release for release in releases if release["changes"]]


def load_changelog(path: Path = CHANGELOG_PATH) -> list[dict]:
    try:
        return parse_changelog(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


def stamp_release(path: Path, version: str, now: datetime | None = None) -> bool:
    """Turn the Unreleased section into ``version`` and open a new empty one.

    Returns False and leaves the file alone when there is nothing unreleased.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    if not any(release["version"] == UNRELEASED for release in parse_changelog(text)):
        return False
    released = (now or datetime.now(RELEASE_TIMEZONE)).astimezone(RELEASE_TIMEZONE)
    heading = f"## {version} — {released:%Y-%m-%d %H:%M} (UTC+8)"
    text = text.replace(f"## {UNRELEASED}\n", f"## {UNRELEASED}\n\n{heading}\n", 1)
    path.write_text(text, encoding="utf-8")
    return True
