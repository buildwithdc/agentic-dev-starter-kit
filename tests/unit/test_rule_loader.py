#!/usr/bin/env python3
"""Unit tests for rule loader."""

import tempfile
import unittest
from pathlib import Path

from scripts.rule_loader import (
    load_rules,
    match_rules_for_files,
)


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
            "tier: org\n"
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
            "tier: team\n"
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

    def test_tier_detection_and_subdirectories(self) -> None:
        # Create tiered folders
        org_dir = self.rules_path / "org"
        team_dir = self.rules_path / "team"
        personal_dir = self.rules_path / "personal"
        org_dir.mkdir(parents=True, exist_ok=True)
        team_dir.mkdir(parents=True, exist_ok=True)
        personal_dir.mkdir(parents=True, exist_ok=True)

        (org_dir / "sec.md").write_text(
            "---\nid: rule-sec\ntitle: Security\nseverity_default: CRITICAL\n---\nBody\n",
            encoding="utf-8",
        )
        (team_dir / "arch.md").write_text(
            "---\nid: rule-arch\ntitle: Architecture\nseverity_default: WARN\n---\nBody\n",
            encoding="utf-8",
        )
        (personal_dir / "pref.md").write_text(
            "---\nid: rule-pref\ntitle: Preferences\nseverity_default: INFO\n---\nBody\n",
            encoding="utf-8",
        )

        rules = load_rules(self.rules_path)
        rule_map = {r.id: r for r in rules}

        self.assertIn("rule-sec", rule_map)
        self.assertEqual(rule_map["rule-sec"].tier, "org")
        self.assertEqual(rule_map["rule-sec"].severity_default, "CRITICAL")

        self.assertIn("rule-arch", rule_map)
        self.assertEqual(rule_map["rule-arch"].tier, "team")

        self.assertIn("rule-pref", rule_map)
        self.assertEqual(rule_map["rule-pref"].tier, "personal")

        # Check sorting: Org first, then Team, then Personal
        tiers = [r.tier for r in rules]
        org_indices = [i for i, t in enumerate(tiers) if t == "org"]
        team_indices = [i for i, t in enumerate(tiers) if t == "team"]
        personal_indices = [i for i, t in enumerate(tiers) if t == "personal"]

        self.assertTrue(max(org_indices) < min(team_indices))
        self.assertTrue(max(team_indices) < min(personal_indices))

    def test_non_weakening_precedence(self) -> None:
        # Create an org rule
        org_dir = self.rules_path / "org"
        personal_dir = self.rules_path / "personal"
        org_dir.mkdir(parents=True, exist_ok=True)
        personal_dir.mkdir(parents=True, exist_ok=True)

        (org_dir / "rule_conflict.md").write_text(
            "---\nid: rule-conflict\ntitle: Strict Org Rule\nseverity_default: CRITICAL\n---\nOrg content\n",
            encoding="utf-8",
        )
        # Attempt to weaken in personal
        (personal_dir / "rule_conflict.md").write_text(
            "---\nid: rule-conflict\ntitle: Relaxed Personal Rule\nseverity_default: INFO\n---\nPersonal content\n",
            encoding="utf-8",
        )

        rules = load_rules(self.rules_path)
        conflict_rules = [r for r in rules if r.id == "rule-conflict"]
        self.assertEqual(len(conflict_rules), 1)
        # Higher tier (org) must prevail with CRITICAL severity
        self.assertEqual(conflict_rules[0].tier, "org")
        self.assertEqual(conflict_rules[0].severity_default, "CRITICAL")


if __name__ == "__main__":
    unittest.main()
