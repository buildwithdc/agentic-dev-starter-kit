#!/usr/bin/env python3
"""Integration tests for git hook lifecycle."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.install_tooling import install_git_hook


class TestGitHookLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name)

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
        )

        # Copy scripts directory to temp repo
        scripts_src = Path(__file__).resolve().parent.parent.parent / "scripts"
        scripts_dest = self.repo_dir / "scripts"
        scripts_dest.mkdir(parents=True, exist_ok=True)
        for script_file in scripts_src.glob("*.py"):
            scripts_dest.joinpath(script_file.name).write_text(
                script_file.read_text(encoding="utf-8"), encoding="utf-8"
            )

        # Copy rules directory to temp repo
        rules_src = Path(__file__).resolve().parent.parent.parent / ".agents" / "rules"
        rules_dest = self.repo_dir / ".agents" / "rules"
        rules_dest.mkdir(parents=True, exist_ok=True)
        for rule_file in rules_src.glob("*.md"):
            rules_dest.joinpath(rule_file.name).write_text(
                rule_file.read_text(encoding="utf-8"), encoding="utf-8"
            )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_git_hook_execution(self) -> None:
        # Install hook
        installed = install_git_hook(self.repo_dir)
        self.assertTrue(installed)
        hook_path = self.repo_dir / ".git" / "hooks" / "pre-commit"
        self.assertTrue(hook_path.exists())

        # Stage a clean file
        test_file = self.repo_dir / "sample.py"
        test_file.write_text("def test_func() -> None:\n    pass\n", encoding="utf-8")
        subprocess.run(["git", "add", "sample.py"], cwd=self.repo_dir, check=True)

        # Attempt commit without GEMINI_API_KEY (graceful offline degradation -> exit 0)
        env = {"PATH": subprocess.os.environ.get("PATH", "")}
        res = subprocess.run(
            ["git", "commit", "-m", "chore: add sample"],
            cwd=self.repo_dir,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("add sample", res.stdout + res.stderr)


if __name__ == "__main__":
    unittest.main()
