#!/usr/bin/env python3
"""Safe application updater and model downloader for Cell Tracking Studio."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION_PATH = PROJECT_ROOT / "VERSION"
LOCAL_MODEL_MANIFEST = PROJECT_ROOT / "model-manifest.json"
DEFAULT_MANIFEST_URL = (
    "https://github.com/Xiangyu-Labs/CellTrackingToolkit/"
    "releases/latest/download/update-manifest.json"
)
PUBLIC_GIT_URL = "https://github.com/Xiangyu-Labs/CellTrackingToolkit.git"
DEFAULT_MODEL_PATH = (
    PROJECT_ROOT / "models" / "segmentation" / "yolo11x-seg.pt"
)
UPDATE_EXIT_CODE = 10
SCHEMA_VERSION = 1
PROTECTED_TOP_LEVEL = {
    ".git",
    ".tools",
    ".venv",
    "Datasets",
    "docs",
    "models",
    "workspace",
}


class UpdateError(RuntimeError):
    """A recoverable update or download failure."""


def _request(url: str, timeout: float = 15.0):
    request = Request(url, headers={"User-Agent": "CellTrackingStudio-Updater/1"})
    return urlopen(request, timeout=timeout)


def load_json_url(url: str) -> dict[str, Any]:
    with _request(url) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise UpdateError("The update manifest is not a JSON object.")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise UpdateError("The update manifest schema is not supported.")
    return payload


def load_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UpdateError(f"Could not read {path.name}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise UpdateError(f"{path.name} has an unsupported schema.")
    return payload


def current_version() -> str:
    try:
        return VERSION_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"


def version_key(value: str) -> tuple[int, ...]:
    core = value.strip().removeprefix("v").split("-", 1)[0]
    try:
        return tuple(int(part) for part in core.split("."))
    except ValueError as exc:
        raise UpdateError(f"Invalid version: {value}") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _request(url, timeout=30.0) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)


def validate_download(path: Path, *, size: int, sha256: str) -> None:
    if path.stat().st_size != size:
        raise UpdateError(
            f"Downloaded file size is {path.stat().st_size}, expected {size}."
        )
    actual_hash = sha256_file(path)
    if actual_hash.lower() != sha256.lower():
        raise UpdateError(
            f"Downloaded file SHA-256 is {actual_hash}, expected {sha256}."
        )


def resolve_model_path() -> tuple[Path, bool]:
    custom = os.environ.get("CELLTRACK_WEIGHTS_PATH")
    if custom:
        return Path(custom).expanduser().resolve(), True
    return DEFAULT_MODEL_PATH, False


def ensure_model(model: dict[str, Any]) -> None:
    model_path, is_custom = resolve_model_path()
    if is_custom:
        if not model_path.is_file():
            raise UpdateError(
                f"Custom segmentation model does not exist: {model_path}"
            )
        print(f"Using custom segmentation model: {model_path}")
        return

    try:
        expected_size = int(model["size"])
        expected_hash = str(model["sha256"])
        download_url = str(model["url"])
    except (KeyError, TypeError, ValueError) as exc:
        raise UpdateError("The model manifest is incomplete.") from exc

    if model_path.is_file():
        try:
            validate_download(
                model_path,
                size=expected_size,
                sha256=expected_hash,
            )
            print("Segmentation model is ready.")
            return
        except (OSError, UpdateError):
            print("The existing segmentation model failed verification; downloading it again.")

    temporary = model_path.with_name(f".{model_path.name}.download")
    try:
        temporary.unlink(missing_ok=True)
        print("Downloading the segmentation model (about 137 MiB)...")
        download_file(download_url, temporary)
        validate_download(temporary, size=expected_size, sha256=expected_hash)
        model_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, model_path)
        print("Segmentation model download completed.")
    except (HTTPError, URLError, TimeoutError, OSError, UpdateError) as exc:
        temporary.unlink(missing_ok=True)
        raise UpdateError(
            "The segmentation model could not be downloaded. Check the internet "
            "connection and start the application again."
        ) from exc


def run_git(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise UpdateError(f"Git command failed: {message}")
    return completed


def update_git_checkout() -> bool:
    if shutil.which("git") is None:
        print("Automatic code update skipped: Git is not installed.")
        return False
    branch = run_git("branch", "--show-current").stdout.strip()
    if branch != "main":
        print("Automatic code update skipped: the current branch is not main.")
        return False
    if run_git("status", "--porcelain", "--untracked-files=no").stdout.strip():
        print("Automatic code update skipped: tracked files have local changes.")
        return False

    run_git("fetch", PUBLIC_GIT_URL, "main")
    ancestor = run_git(
        "merge-base",
        "--is-ancestor",
        "HEAD",
        "FETCH_HEAD",
        check=False,
    )
    if ancestor.returncode != 0:
        print("Automatic code update skipped: the local branch has diverged.")
        return False
    if run_git("rev-parse", "HEAD").stdout.strip() == run_git(
        "rev-parse", "FETCH_HEAD"
    ).stdout.strip():
        print("Application code is up to date.")
        return False
    run_git("merge", "--ff-only", "FETCH_HEAD")
    print("Application code was updated.")
    return True


def _safe_zip_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members: list[zipfile.ZipInfo] = []
    for info in archive.infolist():
        if "\\" in info.filename:
            raise UpdateError(f"Unsafe path in update archive: {info.filename}")
        path = PurePosixPath(info.filename)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or path.parts[0] in PROTECTED_TOP_LEVEL
        ):
            if path.parts and path.parts[0] in PROTECTED_TOP_LEVEL:
                continue
            raise UpdateError(f"Unsafe path in update archive: {info.filename}")
        file_type = (info.external_attr >> 16) & 0o170000
        if file_type == stat.S_IFLNK:
            raise UpdateError(f"Symbolic links are not allowed: {info.filename}")
        members.append(info)
    return members


def install_zip_update(archive_path: Path) -> None:
    update_root = PROJECT_ROOT / ".tools" / "update"
    update_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=update_root) as temporary_dir:
        temporary_root = Path(temporary_dir)
        staging = temporary_root / "staging"
        backup = temporary_root / "backup"
        staging.mkdir()
        backup.mkdir()

        with zipfile.ZipFile(archive_path) as archive:
            members = _safe_zip_members(archive)
            archive.extractall(staging, members=members)
            for info in members:
                extracted = staging / PurePosixPath(info.filename)
                permissions = (info.external_attr >> 16) & 0o777
                if extracted.is_file() and permissions:
                    extracted.chmod(permissions)

        files = [
            path for path in staging.rglob("*")
            if path.is_file() and path.relative_to(staging).parts[0]
            not in PROTECTED_TOP_LEVEL
        ]
        replaced: list[Path] = []
        created: list[Path] = []
        pending: list[Path] = []
        try:
            for source in files:
                relative = source.relative_to(staging)
                destination = PROJECT_ROOT / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    backup_path = backup / relative
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(destination, backup_path)
                    replaced.append(relative)
                else:
                    created.append(relative)
                temporary_destination = destination.with_name(
                    f".{destination.name}.update"
                )
                pending.append(temporary_destination)
                shutil.copy2(source, temporary_destination)
                os.replace(temporary_destination, destination)
                pending.remove(temporary_destination)
        except Exception:
            for temporary_destination in pending:
                temporary_destination.unlink(missing_ok=True)
            for relative in created:
                (PROJECT_ROOT / relative).unlink(missing_ok=True)
            for relative in replaced:
                destination = PROJECT_ROOT / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup / relative, destination)
            raise


def update_zip_install(app: dict[str, Any]) -> bool:
    try:
        remote_version = str(app["version"])
        url = str(app["url"])
        expected_hash = str(app["sha256"])
    except KeyError as exc:
        raise UpdateError("The application update manifest is incomplete.") from exc
    if version_key(remote_version) <= version_key(current_version()):
        print("Application code is up to date.")
        return False

    update_dir = PROJECT_ROOT / ".tools" / "update"
    archive_path = update_dir / "download.tmp"
    try:
        archive_path.unlink(missing_ok=True)
        print(f"Downloading Cell Tracking Studio {remote_version}...")
        download_file(url, archive_path)
        actual_hash = sha256_file(archive_path)
        if actual_hash.lower() != expected_hash.lower():
            raise UpdateError(
                f"Update archive SHA-256 is {actual_hash}, expected {expected_hash}."
            )
        install_zip_update(archive_path)
    finally:
        archive_path.unlink(missing_ok=True)
    print(f"Cell Tracking Studio was updated to {remote_version}.")
    return True


def fetch_remote_manifest() -> dict[str, Any] | None:
    url = os.environ.get("CELLTRACK_UPDATE_MANIFEST_URL", DEFAULT_MANIFEST_URL)
    try:
        return load_json_url(url)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, UpdateError) as exc:
        print(f"Automatic update check unavailable; continuing: {exc}")
        return None


def check_application_update(manifest: dict[str, Any] | None) -> bool:
    if os.environ.get("CELLTRACK_SKIP_UPDATE") == "1":
        print("Automatic update skipped by CELLTRACK_SKIP_UPDATE.")
        return False
    try:
        if (PROJECT_ROOT / ".git").is_dir():
            return update_git_checkout()
        if manifest is None or not isinstance(manifest.get("app"), dict):
            return False
        return update_zip_install(manifest["app"])
    except (OSError, subprocess.SubprocessError, UpdateError) as exc:
        print(f"Automatic code update failed; continuing with this version: {exc}")
        return False


def model_manifest(remote: dict[str, Any] | None) -> dict[str, Any]:
    if remote is not None and isinstance(remote.get("model"), dict):
        return remote["model"]
    local = load_json_file(LOCAL_MODEL_MANIFEST)
    model = local.get("model")
    if not isinstance(model, dict):
        raise UpdateError("model-manifest.json does not define a model.")
    return model


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--app-only", action="store_true")
    group.add_argument("--model-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    skip_update = os.environ.get("CELLTRACK_SKIP_UPDATE") == "1"
    remote = None if args.model_only or skip_update else fetch_remote_manifest()
    if args.app_only:
        return UPDATE_EXIT_CODE if check_application_update(remote) else 0
    if not args.model_only and check_application_update(remote):
        return UPDATE_EXIT_CODE
    try:
        ensure_model(model_manifest(remote))
    except UpdateError as exc:
        print(exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
