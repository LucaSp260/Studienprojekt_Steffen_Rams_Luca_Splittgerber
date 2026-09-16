"""Kostenfreie Prüfungen der Windows-Helfer und Datenschutzdateien."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SetupTests(unittest.TestCase):
    def test_setup_contains_required_steps(self):
        text = (ROOT / "setup.bat").read_text(encoding="utf-8").casefold()
        for required in ["sys.version_info < (3, 10)", "-m venv .venv",
                         "pip install --upgrade pip", "pip install -r requirements.txt",
                         'mkdir "data"', 'mkdir "user_data"', 'mkdir "knowledge_base"',
                         'mkdir "chroma_db"', "initialize_database"]:
            self.assertIn(required.casefold(), text)

    def test_start_without_virtual_environment_is_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "start.bat"
            shutil.copy2(ROOT / "start.bat", target)
            environment = os.environ | {"AI_LEARNING_COMPANION_NO_PAUSE": "1"}
            result = subprocess.run(["cmd.exe", "/d", "/c", str(target)], cwd=directory,
                                    capture_output=True, text=True, env=environment)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Bitte zuerst setup.bat ausfuehren", result.stdout)

    def test_gitignore_and_example_contain_no_secret(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        for entry in [".env", ".venv/", "__pycache__/", "user_data/", "chroma_db/",
                      "data/application.db*", "tmp/"]:
            self.assertIn(entry, ignored)
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        for line in example.splitlines():
            if "API_KEY=" in line:
                self.assertEqual(line.split("=", 1)[1], "")


if __name__ == "__main__":
    unittest.main()
