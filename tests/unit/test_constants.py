#!/usr/bin/env python3
"""Unit tests for centralized constants module."""

import unittest
from pathlib import Path

from scripts.constants import (
    DEFAULT_PERSONAL_PREFERENCES_PATH,
    DEFAULT_SYNC_TTL_SECONDS,
    ORG_RULE_FILES,
    ORG_RULES_REL_PATH,
    PERSONAL_RULE_FILES,
    PERSONAL_RULES_REL_PATH,
    RULE_ORG_META_GUIDELINES,
    RULE_ORG_SECURITY_SECRETS,
    RULE_PERSONAL_PREFERENCES,
    RULE_TEAM_ARCHITECTURE,
    RULE_TEAM_CODE_QUALITY,
    RULE_TEAM_TESTING_STANDARDS,
    RULES_DIR_NAME,
    SEVERITY_PRIORITY,
    SYNC_TIMESTAMP_FILENAME,
    TEAM_RULE_FILES,
    TEAM_RULES_REL_PATH,
    TIER_ORDER,
    TIER_ORG,
    TIER_PERSONAL,
    TIER_PREFIX_MAP,
    TIER_PRIORITY,
    TIER_TEAM,
    get_rule_path,
)


class TestConstants(unittest.TestCase):
    def test_tier_definitions(self) -> None:
        self.assertEqual(TIER_ORG, "org")
        self.assertEqual(TIER_TEAM, "team")
        self.assertEqual(TIER_PERSONAL, "personal")

        # Org > Team > Personal priority
        self.assertGreater(TIER_PRIORITY[TIER_ORG], TIER_PRIORITY[TIER_TEAM])
        self.assertGreater(TIER_PRIORITY[TIER_TEAM], TIER_PRIORITY[TIER_PERSONAL])

        self.assertEqual(TIER_ORDER, ["org", "team", "personal"])
        self.assertEqual(TIER_PREFIX_MAP["org"], "org-")
        self.assertEqual(TIER_PREFIX_MAP["team"], "team-")
        self.assertEqual(TIER_PREFIX_MAP["personal"], "personal-")

    def test_rule_naming_prefixes_and_numbers(self) -> None:
        # Org rules start with org-01, org-02...
        self.assertTrue(RULE_ORG_META_GUIDELINES.startswith("org-01-"))
        self.assertTrue(RULE_ORG_SECURITY_SECRETS.startswith("org-02-"))
        for rule in ORG_RULE_FILES:
            self.assertTrue(rule.startswith("org-"))
            self.assertTrue(rule.endswith(".md"))

        # Team rules start with team-01, team-02, team-03...
        self.assertTrue(RULE_TEAM_ARCHITECTURE.startswith("team-01-"))
        self.assertTrue(RULE_TEAM_CODE_QUALITY.startswith("team-02-"))
        self.assertTrue(RULE_TEAM_TESTING_STANDARDS.startswith("team-03-"))
        for rule in TEAM_RULE_FILES:
            self.assertTrue(rule.startswith("team-"))
            self.assertTrue(rule.endswith(".md"))

        # Personal rules start with personal-01...
        self.assertTrue(RULE_PERSONAL_PREFERENCES.startswith("personal-01-"))
        for rule in PERSONAL_RULE_FILES:
            self.assertTrue(rule.startswith("personal-"))
            self.assertTrue(rule.endswith(".md"))

    def test_paths_and_helpers(self) -> None:
        self.assertEqual(RULES_DIR_NAME, ".agents/rules")
        self.assertEqual(ORG_RULES_REL_PATH, ".agents/rules/org")
        self.assertEqual(TEAM_RULES_REL_PATH, ".agents/rules/team")
        self.assertEqual(PERSONAL_RULES_REL_PATH, ".agents/rules/personal")

        self.assertEqual(
            DEFAULT_PERSONAL_PREFERENCES_PATH,
            ".agents/rules/personal/personal-01-preferences.md",
        )

        self.assertEqual(DEFAULT_SYNC_TTL_SECONDS, 86400)
        self.assertEqual(SYNC_TIMESTAMP_FILENAME, ".sync_timestamp")

        # Test path resolver helper
        resolved = get_rule_path("org", RULE_ORG_META_GUIDELINES, root_dir=Path("/workspace"))
        self.assertEqual(
            str(resolved),
            "/workspace/.agents/rules/org/org-01-meta-guidelines.md",
        )

    def test_severity_levels(self) -> None:
        self.assertGreater(SEVERITY_PRIORITY["CRITICAL"], SEVERITY_PRIORITY["ERROR"])
        self.assertGreater(SEVERITY_PRIORITY["ERROR"], SEVERITY_PRIORITY["WARN"])
        self.assertGreater(SEVERITY_PRIORITY["WARN"], SEVERITY_PRIORITY["INFO"])


if __name__ == "__main__":
    unittest.main()
