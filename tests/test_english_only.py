from __future__ import annotations

from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parent.parent
HAN_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
ROOT_FILES = {
    "README.md",
    "start.sh",
    "start.command",
    "start.bat",
    "start-linux.desktop",
    "pyproject.toml",
    "requirements.txt",
}
SOURCE_PREFIXES = ("scripts/", "src/", "tests/")
TEXT_SUFFIXES = {
    ".bat",
    ".command",
    ".css",
    ".desktop",
    ".html",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    ".tools",
    "__pycache__",
    "workspace",
    "models",
    "Datasets",
    "datasets",
    "data",
    "outputs",
    "results",
    ".cache",
}


class EnglishOnlyRepositoryTests(unittest.TestCase):
    def test_repository_owned_text_contains_no_han_characters(self):
        completed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        tracked = completed.stdout.decode("utf-8").split("\0")
        matches: list[str] = []

        for relative_name in tracked:
            if not relative_name:
                continue
            path = Path(relative_name)
            full_path = ROOT / path
            if not full_path.is_file():
                continue
            if any(part in EXCLUDED_PARTS for part in path.parts):
                continue
            in_scope = (
                relative_name in ROOT_FILES
                or relative_name.startswith(SOURCE_PREFIXES)
                or path.suffix.lower() == ".md"
            )
            if not in_scope or path.suffix.lower() not in TEXT_SUFFIXES:
                continue

            for line_number, line in enumerate(
                full_path.read_text(encoding="utf-8").splitlines(),
                start=1,
            ):
                if HAN_PATTERN.search(line):
                    matches.append(f"{relative_name}:{line_number}: {line}")

        self.assertEqual(
            matches,
            [],
            "Han characters found in repository-owned text:\n"
            + "\n".join(matches),
        )


if __name__ == "__main__":
    unittest.main()
