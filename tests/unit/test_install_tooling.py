#!/usr/bin/env python3
"""Unit tests for tooling installer and assistant memory handling."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.install_tooling import (
    clean_tooling,
    ensure_agents_file,
    install_antigravity,
    install_claude,
    install_git_hook,
)


class TestInstallTooling(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_dir = Path(self.temp_dir.name)
        # Create mock .git directory
        (self.root_dir / ".git" / "hooks").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_clean_workspace_no_claude_md_fallback_to_agents(self) -> None:
        """Scenario 1: Clean workspace with no CLAUDE.md or AGENTS.md.

        Verifies AGENTS.md is created with pointer, but CLAUDE.md is NOT created,
        allowing Claude Code to natively fall back to AGENTS.md.
        """
        git_ok = install_git_hook(self.root_dir)
        ag_ok = install_antigravity(self.root_dir)
        agents_ok = ensure_agents_file(self.root_dir, assume_yes=True)
        claude_ok = install_claude(self.root_dir, assume_yes=True)

        self.assertTrue(git_ok)
        self.assertTrue(ag_ok)
        self.assertTrue(agents_ok)
        self.assertTrue(claude_ok)

        agents_file = self.root_dir / "AGENTS.md"
        claude_file = self.root_dir / "CLAUDE.md"
        claude_lower = self.root_dir / "claude.md"

        # AGENTS.md should exist with pointer
        self.assertTrue(agents_file.exists())
        self.assertIn("BEGIN CANONICAL RULES POINTER", agents_file.read_text(encoding="utf-8"))

        # CLAUDE.md must NOT exist
        self.assertFalse(claude_file.exists())
        self.assertFalse(claude_lower.exists())

        # Slash commands, prepackaged skills, and Antigravity tooling should exist
        self.assertTrue((self.root_dir / ".claude" / "commands" / "audit.md").exists())
        self.assertTrue((self.root_dir / ".claude" / "commands" / "preferences.md").exists())
        self.assertTrue((self.root_dir / ".claude" / "skills" / "code-auditor" / "SKILL.md").exists())
        self.assertTrue((self.root_dir / ".claude" / "skills" / "personal-preferences" / "SKILL.md").exists())
        self.assertTrue((self.root_dir / ".agents" / "skills" / "code-auditor" / "SKILL.md").exists())
        self.assertTrue((self.root_dir / ".agents" / "skills" / "personal-preferences" / "SKILL.md").exists())

        # Verify tiered rule directory hierarchy
        self.assertTrue((self.root_dir / ".agents" / "rules" / "org").is_dir())
        self.assertTrue((self.root_dir / ".agents" / "rules" / "team").is_dir())
        self.assertTrue((self.root_dir / ".agents" / "rules" / "personal" / ".gitkeep").exists())

        # Clean should remove generated artifacts
        clean_tooling(self.root_dir)
        self.assertFalse(agents_file.exists())  # Was empty template, so unlinked
        self.assertFalse((self.root_dir / ".claude" / "commands" / "audit.md").exists())
        self.assertFalse((self.root_dir / ".claude" / "commands" / "preferences.md").exists())
        self.assertFalse((self.root_dir / ".claude" / "skills" / "code-auditor" / "SKILL.md").exists())
        self.assertFalse((self.root_dir / ".claude" / "skills" / "personal-preferences" / "SKILL.md").exists())
        self.assertFalse((self.root_dir / ".claude").exists())
        self.assertFalse((self.root_dir / ".agents" / "skills" / "code-auditor" / "SKILL.md").exists())

    def test_existing_agents_md_with_permission(self) -> None:
        """Scenario 2: Existing non-empty AGENTS.md.

        User content must be preserved, pointer inserted only, and CLAUDE.md not created.
        """
        agents_file = self.root_dir / "AGENTS.md"
        original_content = "# Team Protocol\n\nAll changes require a PR.\n"
        agents_file.write_text(original_content, encoding="utf-8")

        # Install with explicit permission
        ok = ensure_agents_file(self.root_dir, assume_yes=True)
        self.assertTrue(ok)

        # Check content
        updated_content = agents_file.read_text(encoding="utf-8")
        self.assertTrue(updated_content.startswith(original_content.rstrip()))
        self.assertIn("BEGIN CANONICAL RULES POINTER", updated_content)

        # CLAUDE.md must not be created
        install_claude(self.root_dir, assume_yes=True)
        self.assertFalse((self.root_dir / "CLAUDE.md").exists())

        # Clean should remove pointer but preserve user protocol
        clean_tooling(self.root_dir)
        self.assertTrue(agents_file.exists())
        cleaned_content = agents_file.read_text(encoding="utf-8")
        self.assertNotIn("BEGIN CANONICAL RULES POINTER", cleaned_content)
        self.assertEqual(cleaned_content.strip(), original_content.strip())

    @patch("builtins.input", return_value="n")
    def test_existing_agents_md_permission_denied(self, mock_input) -> None:
        """Permission denied for existing non-empty AGENTS.md skips insertion."""
        agents_file = self.root_dir / "AGENTS.md"
        original_content = "# Custom Team Protocol\n"
        agents_file.write_text(original_content, encoding="utf-8")

        # Answering 'n' -> permission denied
        ok = ensure_agents_file(self.root_dir, assume_yes=False)
        self.assertFalse(ok)

        # Content must remain completely unmodified
        self.assertEqual(agents_file.read_text(encoding="utf-8"), original_content)

    def test_existing_claude_md_with_permission(self) -> None:
        """Scenario 3: Existing non-empty CLAUDE.md.

        User memory must be preserved, pointer inserted with reference to AGENTS.md.
        """
        claude_file = self.root_dir / "CLAUDE.md"
        original_content = "# My Project Claude Notes\n\n- Use python3.11\n"
        claude_file.write_text(original_content, encoding="utf-8")

        install_claude(self.root_dir, assume_yes=True)

        updated_content = claude_file.read_text(encoding="utf-8")
        self.assertTrue(updated_content.startswith(original_content.rstrip()))
        self.assertIn("BEGIN CANONICAL RULES POINTER", updated_content)
        self.assertIn("AGENTS.md", updated_content)

        # Clean should remove pointer but preserve user memory
        clean_tooling(self.root_dir)
        self.assertTrue(claude_file.exists())
        cleaned_content = claude_file.read_text(encoding="utf-8")
        self.assertNotIn("BEGIN CANONICAL RULES POINTER", cleaned_content)
        self.assertEqual(cleaned_content.strip(), original_content.strip())

    @patch("builtins.input", return_value="n")
    def test_existing_claude_md_permission_denied(self, mock_input) -> None:
        """Permission denied for existing non-empty CLAUDE.md skips insertion."""
        claude_file = self.root_dir / "CLAUDE.md"
        original_content = "# Protected User Memory\n"
        claude_file.write_text(original_content, encoding="utf-8")

        install_claude(self.root_dir, assume_yes=False)

        self.assertEqual(claude_file.read_text(encoding="utf-8"), original_content)

    def test_case_insensitive_detection(self) -> None:
        """Detects and modifies lowercase agents.md and claude.md without creating duplicates."""
        agents_lower = self.root_dir / "agents.md"
        claude_lower = self.root_dir / "claude.md"
        agents_lower.write_text("# Lowercase Agents\n", encoding="utf-8")
        claude_lower.write_text("# Lowercase Claude\n", encoding="utf-8")

        ensure_agents_file(self.root_dir, assume_yes=True)
        install_claude(self.root_dir, assume_yes=True)

        # Should inject into existing lowercase files
        self.assertIn("BEGIN CANONICAL RULES POINTER", agents_lower.read_text(encoding="utf-8"))
        self.assertIn("BEGIN CANONICAL RULES POINTER", claude_lower.read_text(encoding="utf-8"))

        # Clean should restore both
        clean_tooling(self.root_dir)
        self.assertEqual(agents_lower.read_text(encoding="utf-8").strip(), "# Lowercase Agents")
        self.assertEqual(claude_lower.read_text(encoding="utf-8").strip(), "# Lowercase Claude")

    def test_idempotent_pointer_injection(self) -> None:
        """Multiple runs do not duplicate the pointer block."""
        agents_file = self.root_dir / "AGENTS.md"
        agents_file.write_text("# Base Rules\n", encoding="utf-8")

        ensure_agents_file(self.root_dir, assume_yes=True)
        first_content = agents_file.read_text(encoding="utf-8")

        # Second run
        ensure_agents_file(self.root_dir, assume_yes=True)
        second_content = agents_file.read_text(encoding="utf-8")

        self.assertEqual(first_content, second_content)
        self.assertEqual(second_content.count("BEGIN CANONICAL RULES POINTER"), 1)

    def test_install_claude_commands_and_skills(self) -> None:
        """Verifies that install_claude provisions both slash commands and prepackaged skills."""
        ok = install_claude(self.root_dir, assume_yes=True)
        self.assertTrue(ok)

        # Commands
        audit_cmd = self.root_dir / ".claude" / "commands" / "audit.md"
        pref_cmd = self.root_dir / ".claude" / "commands" / "preferences.md"
        self.assertTrue(audit_cmd.is_file())
        self.assertTrue(pref_cmd.is_file())

        # Skills
        audit_skill = self.root_dir / ".claude" / "skills" / "code-auditor" / "SKILL.md"
        pref_skill = self.root_dir / ".claude" / "skills" / "personal-preferences" / "SKILL.md"
        self.assertTrue(audit_skill.is_file())
        self.assertTrue(pref_skill.is_file())

        # Verify content
        audit_content = audit_skill.read_text(encoding="utf-8")
        self.assertIn("name: code-auditor", audit_content)
        self.assertIn("pre_commit_reviewer.py", audit_content)

        pref_content = pref_skill.read_text(encoding="utf-8")
        self.assertIn("name: personal-preferences", pref_content)
        self.assertIn("preference_manager.py", pref_content)

        # Clean
        clean_tooling(self.root_dir)
        self.assertFalse(audit_cmd.exists())
        self.assertFalse(pref_cmd.exists())
        self.assertFalse(audit_skill.exists())
        self.assertFalse(pref_skill.exists())
        self.assertFalse((self.root_dir / ".claude").exists())


if __name__ == "__main__":
    unittest.main()
