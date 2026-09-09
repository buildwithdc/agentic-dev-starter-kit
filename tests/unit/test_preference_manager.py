#!/usr/bin/env python3
"""Unit tests for preference manager."""

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from scripts.preference_manager import (
    add_preference,
    format_preferences_document,
    load_preferences,
    main,
    parse_preferences_document,
    remove_preference,
)


class TestPreferenceManager(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_dir = Path(self.temp_dir.name)
        self.rule_file = self.root_dir / ".agents" / "rules" / "05_personal_preferences.md"
        self.rule_file.parent.mkdir(parents=True, exist_ok=True)

        initial_content = (
            "---\n"
            "id: rule-personal-preferences\n"
            "title: Personal & Team Engineering Preferences\n"
            "severity_default: WARN\n"
            "applies_to:\n"
            "  - '**/*'\n"
            "tags:\n"
            "  - preferences\n"
            "  - workflow\n"
            "---\n\n"
            "# Personal & Team Engineering Preferences\n\n"
            "Guidelines.\n\n"
            "## 1. General Workflow Preferences\n"
            "- Explicitly confirm before modifying repository rule definitions.\n\n"
            "## 2. Code Style & Engineering Conventions\n"
            "<!-- empty -->\n\n"
            "## 3. Tooling & Environment Preferences\n"
            "<!-- empty -->\n"
        )
        self.rule_file.write_text(initial_content, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_parse_and_format_document(self) -> None:
        content = self.rule_file.read_text(encoding="utf-8")
        meta, preamble, sections = parse_preferences_document(content)

        self.assertEqual(meta.get("id"), "rule-personal-preferences")
        self.assertIn("1. General Workflow Preferences", sections)
        self.assertEqual(len(sections["1. General Workflow Preferences"]), 1)

        reformatted = format_preferences_document(meta, preamble, sections)
        self.assertIn("id: rule-personal-preferences", reformatted)
        self.assertIn("Explicitly confirm before modifying", reformatted)

    def test_add_preference_success(self) -> None:
        success, msg = add_preference(
            rule_text="Always use strict typing for public interfaces",
            section="2. Code Style & Engineering Conventions",
            rule_file=self.rule_file,
            root_dir=self.root_dir,
        )
        self.assertTrue(success)
        self.assertIn("Successfully added rule", msg)

        prefs = load_preferences(rule_file=self.rule_file, root_dir=self.root_dir)
        self.assertIn("Always use strict typing for public interfaces", prefs["2. Code Style & Engineering Conventions"])

    def test_add_preference_duplicate(self) -> None:
        # Try adding existing rule
        success, msg = add_preference(
            rule_text="Explicitly confirm before modifying repository rule definitions.",
            section="1. General Workflow Preferences",
            rule_file=self.rule_file,
            root_dir=self.root_dir,
        )
        self.assertFalse(success)
        self.assertIn("already exists", msg)

    def test_add_preference_new_section(self) -> None:
        success, msg = add_preference(
            rule_text="Run benchmarks on release tags",
            section="4. Release & Performance",
            rule_file=self.rule_file,
            root_dir=self.root_dir,
        )
        self.assertTrue(success)
        prefs = load_preferences(rule_file=self.rule_file, root_dir=self.root_dir)
        self.assertIn("4. Release & Performance", prefs)
        self.assertIn("Run benchmarks on release tags", prefs["4. Release & Performance"])

    def test_remove_preference(self) -> None:
        # First add a rule
        add_preference(
            rule_text="Temporary debug rule to delete",
            section="1. General Workflow Preferences",
            rule_file=self.rule_file,
            root_dir=self.root_dir,
        )

        count, removed = remove_preference(
            pattern="Temporary debug rule",
            rule_file=self.rule_file,
            root_dir=self.root_dir,
        )
        self.assertEqual(count, 1)
        self.assertIn("Temporary debug rule to delete", removed[0])

        prefs = load_preferences(rule_file=self.rule_file, root_dir=self.root_dir)
        self.assertNotIn("Temporary debug rule to delete", prefs["1. General Workflow Preferences"])

    def test_cli_execution(self) -> None:
        # Test CLI add
        ret_add = main([
            "--root-dir", str(self.root_dir),
            "--rule-file", str(self.rule_file),
            "--add", "CLI tested preference rule",
            "--section", "3. Tooling & Environment Preferences",
        ])
        self.assertEqual(ret_add, 0)

        # Test CLI list
        f = io.StringIO()
        with redirect_stdout(f):
            ret_list = main([
                "--root-dir", str(self.root_dir),
                "--rule-file", str(self.rule_file),
                "--list",
            ])
        self.assertEqual(ret_list, 0)
        self.assertIn("CLI tested preference rule", f.getvalue())

        # Test CLI remove
        ret_rem = main([
            "--root-dir", str(self.root_dir),
            "--rule-file", str(self.rule_file),
            "--remove", "CLI tested",
        ])
        self.assertEqual(ret_rem, 0)


if __name__ == "__main__":
    unittest.main()
