#!/usr/bin/env python3
"""Unit tests for scripts/cli.py unified CLI dispatcher."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.cli import main as cli_main


class TestUnifiedCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_dir = Path(self.temp_dir.name)
        (self.root_dir / ".git" / "hooks").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_version_and_help(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli_main(["version"])
        self.assertEqual(rc, 0)
        self.assertIn("agentic-dev 0.2.0", buf.getvalue())

        buf_help = io.StringIO()
        with redirect_stdout(buf_help):
            rc_help = cli_main(["--help"])
        self.assertEqual(rc_help, 0)
        self.assertIn("subcommands:", buf_help.getvalue())

    def test_unknown_subcommand_returns_2(self) -> None:
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = cli_main(["nonexistent-cmd"])
        self.assertEqual(rc, 2)
        self.assertIn("Unknown subcommand", err_buf.getvalue())

    def test_init_and_install_subcommands(self) -> None:
        rc = cli_main(["init", "--all", "-y", "--no-gitignore", "--root-dir", str(self.root_dir)])
        self.assertEqual(rc, 0)
        self.assertTrue((self.root_dir / ".git" / "hooks" / "pre-commit").is_file())
        self.assertTrue((self.root_dir / ".env.example").is_file())
        self.assertTrue((self.root_dir / "AGENTS.md").is_file())
        self.assertTrue((self.root_dir / ".agents" / "rules" / "org").is_dir())
        self.assertTrue((self.root_dir / ".agents" / "rules" / "team").is_dir())

        # Clean via `install --clean`
        rc_clean = cli_main(["install", "--clean", "--root-dir", str(self.root_dir)])
        self.assertEqual(rc_clean, 0)
        self.assertFalse((self.root_dir / ".git" / "hooks" / "pre-commit").exists())

    def test_prefs_subcommand(self) -> None:
        rc_add = cli_main(
            [
                "prefs",
                "--add",
                "Prefer dataclasses over raw dicts",
                "--root-dir",
                str(self.root_dir),
            ]
        )
        self.assertEqual(rc_add, 0)

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc_list = cli_main(["prefs", "--list", "--root-dir", str(self.root_dir)])
        self.assertEqual(rc_list, 0)
        self.assertIn("Prefer dataclasses over raw dicts", buf.getvalue())

    def test_review_subcommand_skip_llm(self) -> None:
        rc = cli_main(
            [
                "review",
                "--skip-llm",
                "--root-dir",
                str(self.root_dir),
                "--audit-file",
                str(self.root_dir / ".agents" / "audit.log"),
            ]
        )
        self.assertEqual(rc, 0)

    def test_sync_subcommand_local_source(self) -> None:
        src_rules = self.root_dir / "remote_org_rules"
        src_rules.mkdir(parents=True, exist_ok=True)
        (src_rules / "org-01-test.md").write_text("# Org Rule\n", encoding="utf-8")

        rc = cli_main(
            [
                "sync",
                "--root-dir",
                str(self.root_dir),
                "--source-dir",
                str(src_rules),
                "--force",
            ]
        )
        self.assertEqual(rc, 0)
        self.assertTrue((self.root_dir / ".agents" / "rules" / "org" / "org-01-test.md").is_file())

    def test_doctor_subcommand(self) -> None:
        cli_main(["init", "--git-hook", "--no-gitignore", "--root-dir", str(self.root_dir)])
        buf = io.StringIO()
        with patch("scripts.cli.get_adc_access_token", return_value=None), redirect_stdout(buf):
            rc = cli_main(["doctor", "--root-dir", str(self.root_dir)])
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertIn("agentic-dev doctor", output)
        self.assertIn("Pre-Commit:    ✅ Installed", output)
        self.assertIn("Config (.env): ⚠️  Missing .env", output)
        self.assertIn("Active Rules:", output)

    def test_binary_init_gitignore_excludes_scripts(self) -> None:
        rc = cli_main(["init", "--all", "-y", "--gitignore", "--root-dir", str(self.root_dir)])
        self.assertEqual(rc, 0)
        gitignore_content = (self.root_dir / ".gitignore").read_text(encoding="utf-8")
        non_comment_lines = [
            line.strip()
            for line in gitignore_content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        self.assertIn(".agents/hooks.json", non_comment_lines)
        self.assertIn(".agents/audit.log", non_comment_lines)
        self.assertNotIn("scripts/pre_commit_reviewer.py", non_comment_lines)
        self.assertNotIn("scripts/install_tooling.py", non_comment_lines)
        self.assertFalse(any(line.startswith("scripts/") for line in non_comment_lines))

    def test_sync_without_dotenv_and_zero_rules_fails_loudly(self) -> None:
        import os

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ORG_RULES_SYNC_URL", None)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_main(["sync", "--root-dir", str(self.root_dir)])
            self.assertEqual(rc, 1)
            output = buf.getvalue()
            self.assertIn("No .env file found", output)
            self.assertIn("No organizational rules loaded (0 rules", output)
            self.assertNotIn("synchronized successfully", output)

    def test_init_warns_when_not_git_repository(self) -> None:
        non_git_dir = self.root_dir / "not_a_git_repo"
        non_git_dir.mkdir(parents=True, exist_ok=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli_main(["init", "--all", "-y", "--no-gitignore", "--root-dir", str(non_git_dir)])
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertIn("not a Git-initialized repository (.git directory not found)", output)

    def test_init_interactive_wizard(self) -> None:
        import os

        # Step 1: '2' (Antigravity only)
        # Step 2: 'https://example.com/org-rules.zip' (configure .env)
        # Step 3: 'y' (add generated files to .gitignore without scripts/)
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("builtins.input", side_effect=["2", "https://example.com/org-rules.zip", "y"]),
            patch("scripts.install_tooling.sync_org_rules", return_value=True),
        ):
            os.environ.pop("ORG_RULES_SYNC_URL", None)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_main(["init", "--root-dir", str(self.root_dir)])
            self.assertEqual(rc, 0)
            output = buf.getvalue()
            self.assertIn("Agentic Dev Interactive Setup Wizard", output)

        # Verify Antigravity installed, Claude NOT installed
        self.assertTrue((self.root_dir / ".agents" / "hooks.json").is_file())
        self.assertFalse((self.root_dir / ".claude").exists())

        # Verify .env created with ORG_RULES_SYNC_URL
        env_file = self.root_dir / ".env"
        self.assertTrue(env_file.is_file())
        self.assertIn('ORG_RULES_SYNC_URL="https://example.com/org-rules.zip"', env_file.read_text(encoding="utf-8"))

        # Verify .gitignore created without scripts/
        gi_text = (self.root_dir / ".gitignore").read_text(encoding="utf-8")
        gi_entries = [
            line.strip()
            for line in gi_text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        self.assertIn(".agents/hooks.json", gi_entries)
        self.assertFalse(any(line.startswith("scripts/") for line in gi_entries))


if __name__ == "__main__":
    unittest.main()
