from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
LAUNCHER_PATH = ROOT / "scripts" / "launcher.py"
SPEC = importlib.util.spec_from_file_location("celltrack_launcher", LAUNCHER_PATH)
assert SPEC is not None and SPEC.loader is not None
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)

from celltrack.web.app import health


class HealthEndpointTests(unittest.TestCase):
    def test_health_endpoint_has_stable_identity(self):
        self.assertEqual(
            health(),
            {
                "status": "ok",
                "app": "cell-tracking-studio",
                "version": launcher.__version__,
            },
        )


class LauncherPortTests(unittest.TestCase):
    def _server(self, payload: dict[str, str]):
        encoded = json.dumps(payload).encode("utf-8")

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/api/health":
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, _format, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def test_existing_celltrack_service_is_reused(self):
        server = self._server(launcher.HEALTH_RESPONSE)
        port = server.server_address[1]
        with mock.patch.object(launcher, "PORTS", range(port, port + 1)):
            self.assertEqual(launcher.select_port(), (port, True))

    def test_foreign_service_is_skipped_for_next_free_port(self):
        server = self._server({"status": "ok", "app": "another-program"})
        occupied_port = server.server_address[1]
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            free_port = reservation.getsockname()[1]
        with mock.patch.object(launcher, "PORTS", [occupied_port, free_port]):
            self.assertEqual(launcher.select_port(), (free_port, False))

    def test_wait_timeout_returns_false(self):
        process = mock.Mock()
        process.poll.return_value = None
        with (
            mock.patch.object(launcher, "is_celltrack_service", return_value=False),
            mock.patch.object(launcher.time, "sleep"),
            mock.patch.object(
                launcher.time,
                "monotonic",
                side_effect=[0.0, 0.0, 0.6],
            ),
        ):
            self.assertFalse(
                launcher.wait_until_healthy(
                    8000,
                    process,
                    timeout=0.5,
                    interval=0.1,
                )
            )


class LauncherValidationTests(unittest.TestCase):
    def test_run_creates_datasets_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            datasets = Path(temporary) / "Datasets"
            with (
                mock.patch.object(launcher, "DATASETS_DIR", datasets),
                mock.patch.object(launcher, "validate_model"),
                mock.patch.object(
                    launcher,
                    "select_port",
                    side_effect=RuntimeError("stop after setup"),
                ),
                self.assertRaisesRegex(RuntimeError, "stop after setup"),
            ):
                launcher.run_application(no_browser=True)
            self.assertTrue(datasets.is_dir())

    def test_missing_model_message_is_actionable(self):
        with mock.patch.object(
            launcher,
            "DEFAULT_MODEL_PATH",
            Path("/definitely/missing/yolo11x-seg.pt"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Missing cell segmentation model: "
                "models/segmentation/yolo11x-seg.pt",
            ):
                launcher.validate_model()

    def test_custom_model_path_is_used(self):
        with tempfile.TemporaryDirectory() as temporary:
            model = Path(temporary) / "custom.pt"
            model.write_bytes(b"custom")
            with mock.patch.dict(
                os.environ,
                {"CELLTRACK_WEIGHTS_PATH": str(model)},
            ):
                launcher.validate_model()

    def test_self_check_imports_application_dependencies(self):
        self.assertEqual(launcher.self_check(), 0)


class ShellBootstrapTests(unittest.TestCase):
    def test_broken_environment_triggers_uv_clear_rebuild(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".tools" / "uv").mkdir(parents=True)
            (root / ".venv" / "bin").mkdir(parents=True)
            (root / "scripts").mkdir()
            (root / "workspace" / "logs").mkdir(parents=True)
            (root / "start.sh").write_bytes((ROOT / "start.sh").read_bytes())
            (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
            (root / "scripts" / "launcher.py").write_text("", encoding="utf-8")
            (root / ".venv" / "bin" / "python").symlink_to(
                "/missing/python3"
            )

            uv_log = root / "uv-calls.log"
            fake_uv = root / ".tools" / "uv" / "uv"
            fake_uv.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' \"$*\" >>'{uv_log}'\n"
                "if [ \"$1\" = venv ]; then\n"
                "  mkdir -p \"$6/bin\"\n"
                "  rm -f \"$6/bin/python\"\n"
                "  printf '#!/bin/sh\\nexit 0\\n' >\"$6/bin/python\"\n"
                "  chmod +x \"$6/bin/python\"\n"
                "fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake_uv.chmod(0o755)
            (root / "start.sh").chmod(0o755)

            completed = subprocess.run(
                ["sh", str(root / "start.sh")],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            calls = uv_log.read_text(encoding="utf-8")
            self.assertIn("venv --clear --python 3.11 --managed-python", calls)
            self.assertIn("sync --locked --python 3.11 --managed-python", calls)
            self.assertIn(
                "An incomplete or transferred application environment was "
                "detected. Repairing it automatically...",
                completed.stdout,
            )


if __name__ == "__main__":
    unittest.main()
