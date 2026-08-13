#!/usr/bin/env python3
"""Increment the Cell Tracking Studio patch version."""

from __future__ import annotations

import argparse
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def replace_once(path: Path, old: str, new: str) -> None:
    content = path.read_text(encoding="utf-8")
    if content.count(old) != 1:
        raise RuntimeError(f"Expected one version entry in {path}.")
    path.write_text(content.replace(old, new), encoding="utf-8")


def bump_patch(root: Path = ROOT) -> str:
    version_path = root / "VERSION"
    current = version_path.read_text(encoding="utf-8").strip()
    match = VERSION_PATTERN.fullmatch(current)
    if match is None:
        raise RuntimeError(f"Invalid VERSION value: {current}")

    major, minor, patch = (int(part) for part in match.groups())
    version = f"{major}.{minor}.{patch + 1}"
    version_path.write_text(f"{version}\n", encoding="utf-8")
    replace_once(
        root / "pyproject.toml",
        f'version = "{current}"',
        f'version = "{version}"',
    )
    replace_once(
        root / "src" / "celltrack" / "__init__.py",
        f'__version__ = "{current}"',
        f'__version__ = "{version}"',
    )
    return version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(bump_patch(args.root.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
