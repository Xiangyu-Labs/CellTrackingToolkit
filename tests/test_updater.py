from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parent.parent
UPDATER_PATH = ROOT / "scripts" / "updater.py"
SPEC = importlib.util.spec_from_file_location("celltrack_updater", UPDATER_PATH)
assert SPEC is not None and SPEC.loader is not None
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class ModelDownloadTests(unittest.TestCase):
    def test_valid_model_is_not_downloaded_again(self):
        payload = b"verified model"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "models" / "model.pt"
            destination.parent.mkdir(parents=True)
            destination.write_bytes(payload)
            manifest = {
                "url": "https://example.invalid/model.pt",
                "size": len(payload),
                "sha256": digest(payload),
            }
            with (
                mock.patch.object(
                    updater,
                    "resolve_model_path",
                    return_value=(destination, False),
                ),
                mock.patch.object(updater, "download_file") as download,
            ):
                updater.ensure_model(manifest)
            download.assert_not_called()

    def test_missing_model_is_downloaded_and_atomically_installed(self):
        payload = b"new model"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "models" / "model.pt"
            manifest = {
                "url": "https://example.invalid/model.pt",
                "size": len(payload),
                "sha256": digest(payload),
            }

            def fake_download(_url: str, path: Path) -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)

            with (
                mock.patch.object(
                    updater,
                    "resolve_model_path",
                    return_value=(destination, False),
                ),
                mock.patch.object(
                    updater,
                    "download_file",
                    side_effect=fake_download,
                ),
            ):
                updater.ensure_model(manifest)
            self.assertEqual(destination.read_bytes(), payload)
            self.assertFalse(
                destination.with_name(f".{destination.name}.download").exists()
            )

    def test_bad_download_does_not_replace_existing_model(self):
        old_payload = b"old invalid model"
        expected_payload = b"expected model"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "models" / "model.pt"
            destination.parent.mkdir(parents=True)
            destination.write_bytes(old_payload)
            manifest = {
                "url": "https://example.invalid/model.pt",
                "size": len(expected_payload),
                "sha256": digest(expected_payload),
            }

            def fake_download(_url: str, path: Path) -> None:
                path.write_bytes(b"corrupt")

            with (
                mock.patch.object(
                    updater,
                    "resolve_model_path",
                    return_value=(destination, False),
                ),
                mock.patch.object(
                    updater,
                    "download_file",
                    side_effect=fake_download,
                ),
                self.assertRaisesRegex(
                    updater.UpdateError,
                    "could not be downloaded",
                ),
            ):
                updater.ensure_model(manifest)
            self.assertEqual(destination.read_bytes(), old_payload)

    def test_custom_model_is_never_downloaded(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "custom.pt"
            destination.write_bytes(b"custom")
            with (
                mock.patch.object(
                    updater,
                    "resolve_model_path",
                    return_value=(destination, True),
                ),
                mock.patch.object(updater, "download_file") as download,
            ):
                updater.ensure_model({})
            download.assert_not_called()


class ZipUpdateTests(unittest.TestCase):
    def test_zip_update_replaces_code_and_preserves_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".tools" / "update").mkdir(parents=True)
            (root / "Datasets").mkdir()
            (root / "models").mkdir()
            (root / "workspace").mkdir()
            (root / "app.txt").write_text("old", encoding="utf-8")
            (root / "Datasets" / "image.tif").write_bytes(b"image")
            (root / "models" / "model.pt").write_bytes(b"model")
            (root / "workspace" / "result.json").write_text(
                "{}",
                encoding="utf-8",
            )
            archive = root / "update.zip"
            with zipfile.ZipFile(archive, "w") as target:
                target.writestr("app.txt", "new")
                target.writestr("new.txt", "added")
                executable = zipfile.ZipInfo("start.sh")
                executable.external_attr = (stat.S_IFREG | 0o755) << 16
                target.writestr(executable, "#!/bin/sh\n")
                target.writestr("Datasets/image.tif", "replacement")
                target.writestr("models/model.pt", "replacement")
                target.writestr("workspace/result.json", "replacement")

            with mock.patch.object(updater, "PROJECT_ROOT", root):
                updater.install_zip_update(archive)

            self.assertEqual((root / "app.txt").read_text(), "new")
            self.assertEqual((root / "new.txt").read_text(), "added")
            if os.name != "nt":
                self.assertTrue(
                    (root / "start.sh").stat().st_mode & stat.S_IXUSR
                )
            self.assertEqual((root / "Datasets" / "image.tif").read_bytes(), b"image")
            self.assertEqual((root / "models" / "model.pt").read_bytes(), b"model")
            self.assertEqual(
                (root / "workspace" / "result.json").read_text(),
                "{}",
            )

    def test_zip_hash_failure_keeps_current_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".tools" / "update").mkdir(parents=True)
            (root / "VERSION").write_text("2.0.0\n", encoding="utf-8")
            (root / "app.txt").write_text("old", encoding="utf-8")
            payload = b"not a valid expected archive"

            def fake_download(_url: str, path: Path) -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)

            with (
                mock.patch.object(updater, "PROJECT_ROOT", root),
                mock.patch.object(updater, "VERSION_PATH", root / "VERSION"),
                mock.patch.object(
                    updater,
                    "download_file",
                    side_effect=fake_download,
                ),
                self.assertRaisesRegex(updater.UpdateError, "SHA-256"),
            ):
                updater.update_zip_install(
                    {
                        "version": "2.0.1",
                        "url": "https://example.invalid/app.zip",
                        "sha256": digest(b"different"),
                    }
                )
            self.assertEqual((root / "app.txt").read_text(), "old")
            self.assertFalse((root / ".tools" / "update" / "download.tmp").exists())

    def test_zip_rejects_backslash_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".tools" / "update").mkdir(parents=True)
            archive = root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as target:
                target.writestr("..\\outside.txt", "unsafe")
            with (
                mock.patch.object(updater, "PROJECT_ROOT", root),
                self.assertRaisesRegex(updater.UpdateError, "Unsafe path"),
            ):
                updater.install_zip_update(archive)


class UpdatePolicyTests(unittest.TestCase):
    def test_skip_update_environment_variable(self):
        with (
            mock.patch.dict(os.environ, {"CELLTRACK_SKIP_UPDATE": "1"}),
            mock.patch.object(updater, "fetch_remote_manifest") as fetch,
        ):
            self.assertEqual(updater.main(["--app-only"]), 0)
        fetch.assert_not_called()

    def test_git_checkout_with_tracked_changes_is_not_updated(self):
        responses = [
            mock.Mock(stdout="main\n"),
            mock.Mock(stdout=" M README.md\n"),
        ]
        with (
            mock.patch.object(updater.shutil, "which", return_value="/usr/bin/git"),
            mock.patch.object(updater, "run_git", side_effect=responses) as run,
        ):
            self.assertFalse(updater.update_git_checkout())
        self.assertEqual(run.call_count, 2)

    def test_fast_forward_git_checkout_is_updated(self):
        responses = [
            mock.Mock(stdout="main\n"),
            mock.Mock(stdout=""),
            mock.Mock(stdout=""),
            mock.Mock(returncode=0),
            mock.Mock(stdout="local\n"),
            mock.Mock(stdout="remote\n"),
            mock.Mock(stdout=""),
        ]
        with (
            mock.patch.object(updater.shutil, "which", return_value="/usr/bin/git"),
            mock.patch.object(updater, "run_git", side_effect=responses) as run,
        ):
            self.assertTrue(updater.update_git_checkout())
        self.assertEqual(run.call_args_list[-1], mock.call("merge", "--ff-only", "FETCH_HEAD"))

    def test_git_checkout_updates_without_release_manifest(self):
        with (
            mock.patch.object(
                updater,
                "PROJECT_ROOT",
                Path("/temporary/git-checkout"),
            ),
            mock.patch.object(Path, "is_dir", return_value=True),
            mock.patch.object(
                updater,
                "update_git_checkout",
                return_value=True,
            ) as update,
        ):
            self.assertTrue(updater.check_application_update(None))
        update.assert_called_once_with()


class ManifestTests(unittest.TestCase):
    def test_local_model_manifest_matches_published_weight(self):
        manifest = json.loads(
            (ROOT / "model-manifest.json").read_text(encoding="utf-8")
        )
        model = manifest["model"]
        self.assertEqual(model["size"], 143999293)
        self.assertEqual(
            model["sha256"],
            "629e9a4196c654af6294f2cd748f637044a131d6754b699fc02f13ebe9dbabd4",
        )


if __name__ == "__main__":
    unittest.main()
