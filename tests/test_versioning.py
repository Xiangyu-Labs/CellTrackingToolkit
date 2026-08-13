from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
BUMP_PATH = ROOT / "scripts" / "bump_version.py"
SPEC = importlib.util.spec_from_file_location("celltrack_bump_version", BUMP_PATH)
assert SPEC is not None and SPEC.loader is not None
bump_version = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bump_version)


class VersionBumpTests(unittest.TestCase):
    def test_repository_version_files_are_synchronized(self):
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertIn(
            f'version = "{version}"',
            (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
        )
        self.assertIn(
            f'__version__ = "{version}"',
            (ROOT / "src" / "celltrack" / "__init__.py").read_text(
                encoding="utf-8"
            ),
        )

    def test_patch_version_is_updated_in_all_source_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "src" / "celltrack"
            package.mkdir(parents=True)
            (root / "VERSION").write_text("2.0.2\n", encoding="utf-8")
            (root / "pyproject.toml").write_text(
                '[project]\nversion = "2.0.2"\n',
                encoding="utf-8",
            )
            (package / "__init__.py").write_text(
                '__version__ = "2.0.2"\n',
                encoding="utf-8",
            )

            self.assertEqual(bump_version.bump_patch(root), "2.0.3")
            self.assertEqual(
                (root / "VERSION").read_text(encoding="utf-8"),
                "2.0.3\n",
            )
            self.assertIn(
                'version = "2.0.3"',
                (root / "pyproject.toml").read_text(encoding="utf-8"),
            )
            self.assertIn(
                '__version__ = "2.0.3"',
                (package / "__init__.py").read_text(encoding="utf-8"),
            )

    def test_invalid_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "VERSION").write_text("development\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Invalid VERSION"):
                bump_version.bump_patch(root)


if __name__ == "__main__":
    unittest.main()
