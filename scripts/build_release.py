#!/usr/bin/env python3
"""Build a GitHub Release archive and update manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_TOP_LEVEL = {
    ".git",
    ".tools",
    ".venv",
    "Datasets",
    "docs",
    "models",
    "workspace",
}
INCLUDED_EXCLUDED_PATHS = {Path("Datasets/README.md")}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tracked_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    files = []
    for raw_path in completed.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative = Path(raw_path.decode("utf-8"))
        if (
            relative.parts[0] not in EXCLUDED_TOP_LEVEL
            or relative in INCLUDED_EXCLUDED_PATHS
        ):
            files.append(relative)
    return files


def build(tag: str, repository: str, output_dir: Path) -> tuple[Path, Path]:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if tag != f"v{version}":
        raise RuntimeError(f"Tag {tag} does not match VERSION {version}.")

    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"cell-tracking-studio-{tag}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for relative in tracked_files():
            source = ROOT / relative
            if source.is_file():
                target.write(source, relative.as_posix())

    model_manifest = json.loads(
        (ROOT / "model-manifest.json").read_text(encoding="utf-8")
    )
    manifest = {
        "schema_version": 1,
        "app": {
            "version": version,
            "url": (
                f"https://github.com/{repository}/releases/download/{tag}/"
                f"{archive.name}"
            ),
            "sha256": sha256_file(archive),
        },
        "model": model_manifest["model"],
    }
    manifest_path = output_dir / "update-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return archive, manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    archive, manifest = build(args.tag, args.repository, args.output_dir)
    print(archive)
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
