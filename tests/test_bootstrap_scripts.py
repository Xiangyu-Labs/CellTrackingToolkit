from __future__ import annotations

from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
START_SH = ROOT / "start.sh"
BOOTSTRAP_PS1 = ROOT / "scripts" / "bootstrap.ps1"

# A uv that answers every subcommand start.sh uses, and records the calls so a
# test can assert on them. `venv` and `sync` materialise the interpreter shim
# the script expects to find afterwards.
FAKE_UV = """#!/bin/sh
printf '%s\\n' "$*" >>"$FAKE_UV_CALLS"

make_venv_python() {
  mkdir -p "$1/bin"
  cat >"$1/bin/python" <<'SHIM'
#!/bin/sh
printf '%s\\n' "$*" >>"$FAKE_PYTHON_CALLS"
case "$*" in
  *launcher.py*) printf 'launcher started\\n' ;;
esac
exit 0
SHIM
  chmod +x "$1/bin/python"
}

case "$1" in
  --version) printf 'uv 0.11.16\\n' ;;
  python) ;;
  venv)
    for argument in "$@"; do last="$argument"; done
    make_venv_python "$last"
    ;;
  sync) make_venv_python "$PWD/.venv" ;;
  run) ;;
  *) ;;
esac
exit 0
"""

# Stands in for curl/wget: writes an installer that drops a working fake uv.
FAKE_DOWNLOADER = """#!/bin/sh
for argument in "$@"; do
  case "$previous" in
    -o|-O) destination="$argument" ;;
  esac
  previous="$argument"
done
printf 'downloaded\\n' >>"$FAKE_DOWNLOAD_CALLS"
cat >"$destination" <<INSTALLER
#!/bin/sh
cp "$FAKE_UV_SOURCE" "\\$UV_UNMANAGED_INSTALL/uv"
chmod +x "\\$UV_UNMANAGED_INSTALL/uv"
INSTALLER
exit 0
"""


def write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@unittest.skipIf(
    sys.platform == "win32" or shutil.which("sh") is None,
    "start.sh needs a POSIX shell; bootstrap.ps1 covers Windows",
)
class StartScriptPlatformGuardTest(unittest.TestCase):
    """A project folder copied between platforms must repair itself.

    The bundled uv and .venv hold native binaries. When the folder moves from
    macOS to Linux, or between CPU architectures, they are still present and
    still marked executable, so a test for the executable bit alone reports a
    healthy toolchain and the next command dies with "Exec format error".
    """

    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)

        self.project = self.directory / "project"
        self.project.mkdir()
        shutil.copy2(START_SH, self.project / "start.sh")

        stubs = self.directory / "stubs"
        self.uv_source = self.directory / "uv-source"
        write_executable(self.uv_source, FAKE_UV)
        write_executable(stubs / "curl", FAKE_DOWNLOADER)
        write_executable(stubs / "wget", FAKE_DOWNLOADER)

        self.uv_calls = self.directory / "uv-calls.txt"
        self.python_calls = self.directory / "python-calls.txt"
        self.download_calls = self.directory / "download-calls.txt"
        self.environment = {
            "PATH": f"{stubs}:/usr/bin:/bin",
            "HOME": str(self.directory),
            "FAKE_UV_SOURCE": str(self.uv_source),
            "FAKE_UV_CALLS": str(self.uv_calls),
            "FAKE_PYTHON_CALLS": str(self.python_calls),
            "FAKE_DOWNLOAD_CALLS": str(self.download_calls),
            # Skip the update check, which would shell out to git.
            "CELLTRACK_UPDATE_RESTARTED": "1",
        }

    def install_uv(self, content: str) -> Path:
        destination = self.project / ".tools" / "uv" / "uv"
        write_executable(destination, content)
        return destination

    def install_venv(self, *, working_python: bool) -> Path:
        venv = self.project / ".venv"
        (venv / "bin").mkdir(parents=True)
        (venv / "lib").mkdir()
        if working_python:
            write_executable(
                venv / "bin" / "python",
                "#!/bin/sh\n"
                f'printf \'%s\\n\' "$*" >>"{self.python_calls}"\n'
                "exit 0\n",
            )
        return venv

    def run_start(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(self.project / "start.sh"), "--no-browser"],
            capture_output=True,
            text=True,
            env=self.environment,
            timeout=60,
        )

    def read(self, path: Path) -> str:
        return path.read_text() if path.exists() else ""

    def test_unrunnable_bundled_uv_is_replaced(self) -> None:
        # An executable file that cannot run here, the way a Mach-O binary
        # behaves on Linux.
        self.install_uv("\x7fELF-not-really\nthis is not a valid program\n")

        result = self.run_start()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("cannot run on this computer", result.stdout)
        self.assertIn("downloaded", self.read(self.download_calls))
        self.assertIn("[6/6]", result.stdout)
        self.assertNotIn("Startup preparation failed", result.stdout)

    def test_unrunnable_uv_also_rebuilds_the_transferred_venv(self) -> None:
        self.install_uv("not a program\n")
        venv = self.install_venv(working_python=True)

        result = self.run_start()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # The interpreter answered, so only knowing the toolchain came from
        # another platform can condemn the environment beside it.
        self.assertIn("transferred application environment", result.stdout)
        self.assertIn(f"venv --clear --python 3.11 --managed-python {venv}",
                      self.read(self.uv_calls))

    def test_working_uv_is_left_alone(self) -> None:
        self.install_uv(FAKE_UV)

        result = self.run_start()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.read(self.download_calls), "")
        self.assertNotIn("cannot run on this computer", result.stdout)
        self.assertIn("[6/6]", result.stdout)


class PowerShellBootstrapGuardTest(unittest.TestCase):
    """The Windows script carries the same guard, checked by parsing it."""

    def test_uv_is_probed_rather_than_merely_located(self) -> None:
        source = BOOTSTRAP_PS1.read_text()
        self.assertIn("function Test-UvRuns", source)
        self.assertIn("& $UvExe --version", source)
        self.assertIn("if (-not (Test-UvRuns))", source)
        self.assertIn("$ForeignToolchain", source)

    @unittest.skipIf(shutil.which("pwsh") is None, "pwsh is not installed")
    def test_script_parses(self) -> None:
        script = (
            "$tokens = $null; $errors = $null;"
            "$null = [System.Management.Automation.Language.Parser]::ParseFile("
            f"'{BOOTSTRAP_PS1}', [ref]$tokens, [ref]$errors);"
            "if ($errors.Count -gt 0) { $errors | ForEach-Object "
            "{ Write-Output $_.Message }; exit 1 }"
        )
        result = subprocess.run(
            ["pwsh", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    sys.exit(unittest.main())
