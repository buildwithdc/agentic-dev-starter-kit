#!/usr/bin/env python3
"""Unit tests for tooling installer."""

import tempfile
import unittest
from pathlib import Path

from scripts.install_tooling import clean_tooling, install_antigravity, install_claude, install_git_hook


class TestInstallTooling(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_dir = Path(self.temp_dir.name)
        # Create mock .git directory
        (self.root_dir / ".git" / "hooks").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_install_and_clean_all(self) -> None:
        # Install
        git_ok = install_git_hook(self.root_dir)
        ag_ok = install_antigravity(self.root_dir)
        claude_ok = install_claude(self.root_dir)

        self.assertTrue(git_ok)
        self.assertTrue(ag_ok)
        self.assertTrue(claude_ok)

        # Check installed files
        git_hook = self.root_dir / ".git" / "hooks" / "pre-commit"
        ag_skill = self.root_dir / ".agents" / "skills" / "code-auditor" / "SKILL.md"
        ag_hooks = self.root_dir / ".agents" / "hooks.json"
        claude_cmd = self.root_dir / ".claude" / "commands" / "audit.md"
        claude_md = self.root_dir / "CLAUDE.md"

        self.assertTrue(git_hook.exists())
        self.assertTrue(ag_skill.exists())
        self.assertTrue(ag_hooks.exists())
        self.assertTrue(claude_cmd.exists())
        self.assertTrue(claude_md.exists())
        self.assertIn("BEGIN CANONICAL RULES POINTER", claude_md.read_text(encoding="utf-8"))

        # Clean
        clean_tooling(self.root_dir)
        self.assertFalse(git_hook.exists())
        self.assertFalse(ag_skill.exists())
        self.assertFalse(ag_hooks.exists())
        self.assertFalse(claude_cmd.exists())


if __name__ == "__main__":
    unittest.main()
