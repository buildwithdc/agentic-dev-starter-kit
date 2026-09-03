#!/usr/bin/env python3
"""Unit tests for rule loader."""

import tempfile
import unittest
from pathlib import Path

from scripts.rule_loader import Rule, load_rules, match_rules_for_files


class TestRuleLoader(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.rules_path = Path(self.temp_dir.name)

        # Write sample rules
        rule_meta = (
            "---\n"
            "id: rule-meta\n"
            "title: Meta Rule\n"
            "severity_default: CRITICAL\n"
            "applies_to:\n"
            "  - '**/*'\n"
            "tags:\n"
            "  - meta\n"
            "---\n"
            "# Meta Rule Body\n"
            "Always follow standards.\n"
        )
        (self.rules_path / "00_meta.md").write_text(rule_meta, encoding="utf-8")

        rule_py = (
            "---\n"
            "id: rule-python\n"
            "title: Python Quality\n"
            "severity_default: WARN\n"
            "applies_to:\n"
            "  - '**/*.py'\n"
            "tags:\n"
            "  - python\n"
            "---\n"
            "# Python Rules\n"
            "Type everything.\n"
        )
        (self.rules_path / "01_python.md").write_text(rule_py, encoding="utf-8")

        # Write README.md (should be skipped)
        (self.rules_path / "README.md").write_text("# Catalog Index\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_load_rules(self) -> None:
        rules = load_rules(self.rules_path)
        self.assertEqual(len(rules), 2)
        rule_ids = {r.id for r in rules}
        self.assertIn("rule-meta", rule_ids)
        self.assertIn("rule-python", rule_ids)

    def test_match_rules_for_files(self) -> None:
        rules = load_rules(self.rules_path)

        # Match for a python file -> should match both meta and python
        matched_py = match_rules_for_files(rules, ["src/main.py"])
        self.assertEqual(len(matched_py), 2)

        # Match for a markdown file -> should only match meta
        matched_md = match_rules_for_files(rules, ["docs/readme.md"])
        self.assertEqual(len(matched_md), 1)
        self.assertEqual(matched_md[0].id, "rule-meta")

        # Match for empty list
        self.assertEqual(match_rules_for_files(rules, []), [])


if __name__ == "__main__":
    unittest.main()
