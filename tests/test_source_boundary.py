import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_source_tree import inspect_index, path_error


class SourceBoundaryTests(unittest.TestCase):
    def test_private_paths_are_rejected_at_any_depth(self):
        for name in ("data/user.json", "nested/demo-data/user.json", "review-runs/result.json", ".local-private/file", "analysis.json", "nested/timeline.json", "auth.json", ".env", ".env.local", "archive.sqlite3-wal", "database.db", "chat.jsonl", "trace.log", "private.key", "source.zip", "盘点报告.md"):
            with self.subTest(name=name):
                self.assertIsNotNone(path_error(name))

    def test_source_documentation_and_synthetic_tests_are_allowed(self):
        for name in ("app.py", "demo.py", "review_schema.json", "configuration.py", "README.md", "docs/maintenance.md", ".github/workflows/tests.yml", "tests/test_source_boundary.py", "docs/preview.png", ".githooks/pre-commit"):
            self.assertIsNone(path_error(name), name)
        self.assertIsNotNone(path_error("data-link", "120000"))
        self.assertIsNotNone(path_error("embedded-repo", "160000"))

    def test_check_uses_index_not_unstaged_files_and_never_prints_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "safe.py").write_text("print('source')\n")
            subprocess.run(["git", "add", "safe.py"], cwd=root, check=True)
            (root / "safe.py").write_text("gh" + "p_" + "x" * 36)
            (root / "data").mkdir()
            (root / "data" / "private.json").write_text("{}")
            self.assertEqual(inspect_index(root), (1, []))
            subprocess.run(["git", "add", "safe.py", "data/private.json"], cwd=root, check=True)
            total, errors = inspect_index(root)
            self.assertEqual(total, 2)
            self.assertEqual({name for name, _ in errors}, {"safe.py", "data/private.json"})
            self.assertNotIn("x" * 36, str(errors))

    def test_personal_paths_and_private_keys_are_flagged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "notes.md").write_text("/Users/" + "example-person/private/\n")
            (root / "secret.txt").write_text("-----BEGIN " + "OPENSSH PRIVATE KEY-----")
            subprocess.run(["git", "add", "notes.md", "secret.txt"], cwd=root, check=True)
            _, errors = inspect_index(root)
            self.assertEqual({name for name, _ in errors}, {"notes.md", "secret.txt"})


if __name__ == "__main__":
    unittest.main()
