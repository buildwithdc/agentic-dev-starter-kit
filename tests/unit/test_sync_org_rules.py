#!/usr/bin/env python3
"""Unit tests for organizational rule synchronizer."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.sync_org_rules import (
    RuleChanges,
    compare_rule_files,
    format_duration,
    format_file_size,
    format_verbose_report,
    get_last_sync_info,
    get_local_rules,
    get_timestamp_file,
    is_sync_needed,
    load_dotenv,
    normalize_sync_url,
    record_sync_timestamp,
    sync_org_rules,
    trigger_background_sync,
)


class TestSyncOrgRules(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_dir = Path(self.temp_dir.name)
        self.org_dir = self.root_dir / ".agents" / "rules" / "org"
        self.org_dir.mkdir(parents=True, exist_ok=True)
        import os

        self._orig_sync_url = os.environ.get("ORG_RULES_SYNC_URL")
        os.environ.pop("ORG_RULES_SYNC_URL", None)

    def tearDown(self) -> None:
        import os

        if self._orig_sync_url is not None:
            os.environ["ORG_RULES_SYNC_URL"] = self._orig_sync_url
        else:
            os.environ.pop("ORG_RULES_SYNC_URL", None)
        self.temp_dir.cleanup()

    def test_is_sync_needed_missing_timestamp(self) -> None:
        self.assertTrue(is_sync_needed(self.root_dir))

    def test_is_sync_needed_fresh_timestamp(self) -> None:
        record_sync_timestamp(self.root_dir)
        self.assertFalse(is_sync_needed(self.root_dir, interval_seconds=3600))

    def test_is_sync_needed_stale_timestamp(self) -> None:
        ts_file = get_timestamp_file(self.root_dir)
        ts_file.parent.mkdir(parents=True, exist_ok=True)
        # Set mtime to 25 hours ago
        stale_time = time.time() - 90000
        ts_file.write_text("old timestamp\n", encoding="utf-8")
        import os
        os.utime(ts_file, (stale_time, stale_time))

        self.assertTrue(is_sync_needed(self.root_dir, interval_seconds=86400))

    def test_is_sync_needed_stale_iso_content_with_fresh_mtime(self) -> None:
        ts_file = get_timestamp_file(self.root_dir)
        ts_file.parent.mkdir(parents=True, exist_ok=True)
        # Content has an ISO timestamp from 8 days ago, but file was written just now (fresh mtime)
        ts_file.write_text("2026-09-20T06:08:52.491300+00:00 | SUCCESS\n", encoding="utf-8")
        self.assertTrue(is_sync_needed(self.root_dir, interval_seconds=86400))
        _, _, age = get_last_sync_info(self.root_dir)
        self.assertIsNotNone(age)
        self.assertGreater(age, 86400 * 7)

    def test_sync_org_rules_local_source(self) -> None:
        source_dir = self.root_dir / "central_rules"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "00_org_rule.md").write_text("# Org Rule\n", encoding="utf-8")

        success = sync_org_rules(
            root_dir=self.root_dir,
            source_dir=source_dir,
            force=True,
        )
        self.assertTrue(success)

        target_file = self.org_dir / "00_org_rule.md"
        self.assertTrue(target_file.exists())
        self.assertEqual(target_file.read_text(encoding="utf-8"), "# Org Rule\n")

    def test_sync_org_rules_cached_fallback(self) -> None:
        # Pre-seed cached rule
        (self.org_dir / "cached_rule.md").write_text("# Cached\n", encoding="utf-8")

        success = sync_org_rules(
            root_dir=self.root_dir,
            force=True,
        )
        self.assertTrue(success)
        self.assertTrue(get_timestamp_file(self.root_dir).exists())

    @patch("subprocess.Popen")
    def test_trigger_background_sync(self, mock_popen) -> None:
        # 1. When sync is needed (no timestamp), should spawn background process
        res = trigger_background_sync(self.root_dir)
        self.assertTrue(res)
        mock_popen.assert_called_once()

        # 2. When sync is fresh, should return False without spawning
        mock_popen.reset_mock()
        record_sync_timestamp(self.root_dir)
        res_fresh = trigger_background_sync(self.root_dir)
        self.assertFalse(res_fresh)
        mock_popen.assert_not_called()

    def test_normalize_sync_url(self) -> None:
        tag_url = "https://github.com/myorg/rules-repo/releases/tag/v1.2.0"
        normalized = normalize_sync_url(tag_url)
        self.assertEqual(
            normalized,
            "https://github.com/myorg/rules-repo/releases/download/v1.2.0/rules-org.zip",
        )

        latest_url = "https://github.com/myorg/rules-repo/releases/latest"
        self.assertEqual(
            normalize_sync_url(latest_url),
            "https://github.com/myorg/rules-repo/releases/latest/download/rules-org.zip",
        )

        repo_url = "https://github.com/myorg/rules-repo"
        self.assertEqual(
            normalize_sync_url(repo_url),
            "https://github.com/myorg/rules-repo/archive/refs/heads/main.zip",
        )

    def test_format_duration(self) -> None:
        self.assertEqual(format_duration(30), "30s ago")
        self.assertEqual(format_duration(120), "2m ago")
        self.assertEqual(format_duration(7200), "2h ago")
        self.assertEqual(format_duration(7500), "2h 5m ago")
        self.assertEqual(format_duration(90000), "1d 1h ago")
        self.assertEqual(format_duration(-5), "in the future")

    def test_format_file_size(self) -> None:
        self.assertEqual(format_file_size(500), "500 B")
        self.assertEqual(format_file_size(2048), "2.0 KB")
        self.assertEqual(format_file_size(2 * 1024 * 1024), "2.0 MB")

    def test_get_last_sync_info_missing(self) -> None:
        iso_ts, status, age = get_last_sync_info(self.root_dir)
        self.assertIsNone(iso_ts)
        self.assertIsNone(status)
        self.assertIsNone(age)

    def test_get_last_sync_info_present(self) -> None:
        record_sync_timestamp(self.root_dir, backoff=False)
        iso_ts, status, age = get_last_sync_info(self.root_dir)
        self.assertIsNotNone(iso_ts)
        self.assertEqual(status, "SUCCESS")
        self.assertIsNotNone(age)
        self.assertGreaterEqual(age, 0)

    def test_get_local_rules(self) -> None:
        # Initially empty
        self.assertEqual(get_local_rules(self.root_dir), [])

        # Add rules and README.md
        (self.org_dir / "README.md").write_text("# Org Rules README\n", encoding="utf-8")
        (self.org_dir / "org-01-first.md").write_text("# First\n", encoding="utf-8")
        (self.org_dir / "org-02-second.md").write_text("# Second\n", encoding="utf-8")

        rules = get_local_rules(self.root_dir)
        rule_names = [r.name for r in rules]
        self.assertEqual(rule_names, ["org-01-first.md", "org-02-second.md"])

    def test_compare_rule_files(self) -> None:
        # Pre-seed local files
        (self.org_dir / "rule-unchanged.md").write_text("same content", encoding="utf-8")
        (self.org_dir / "rule-modified.md").write_text("old content", encoding="utf-8")
        (self.org_dir / "rule-local-only.md").write_text("local content", encoding="utf-8")

        # Candidate files from remote/source
        cand_dir = self.root_dir / "candidates"
        cand_dir.mkdir(parents=True, exist_ok=True)
        (cand_dir / "rule-unchanged.md").write_text("same content", encoding="utf-8")
        (cand_dir / "rule-modified.md").write_text("new content\nwith extra line", encoding="utf-8")
        (cand_dir / "rule-added.md").write_text("brand new rule", encoding="utf-8")

        candidates = [
            cand_dir / "rule-unchanged.md",
            cand_dir / "rule-modified.md",
            cand_dir / "rule-added.md",
        ]

        changes = compare_rule_files(candidates, self.org_dir)
        self.assertTrue(changes.has_changes)
        self.assertEqual(changes.added, ["rule-added.md"])
        self.assertEqual(changes.modified, ["rule-modified.md"])
        self.assertEqual(changes.unchanged, ["rule-unchanged.md"])
        self.assertEqual(changes.local_only, ["rule-local-only.md"])
        self.assertIn("rule-modified.md", changes.diffs)
        self.assertIn("-old content", changes.diffs["rule-modified.md"])
        self.assertIn("+new content", changes.diffs["rule-modified.md"])

    def test_format_verbose_report(self) -> None:
        changes = RuleChanges(
            added=["org-03.md"],
            modified=["org-01.md"],
            unchanged=["org-02.md"],
            diffs={"org-01.md": "-line 1\n+line 2"},
        )
        report = format_verbose_report(
            remote_origin="https://example.com/rules.zip",
            last_sync_info=("2026-09-28T05:00:00+00:00", "SUCCESS", 60.0),
            local_rules=[self.org_dir / "org-01.md"],
            changes=changes,
        )
        self.assertIn("Remote Origin:       https://example.com/rules.zip", report)
        self.assertIn("Last Sync Timestamp: 2026-09-28T05:00:00+00:00 [SUCCESS] (1m ago)", report)
        self.assertIn("Local Rules (1 in .agents/rules/org):", report)
        self.assertIn("+ org-03.md (added from remote)", report)
        self.assertIn("~ org-01.md (modified from remote", report)
        self.assertIn("= org-02.md (unchanged)", report)

    @patch("builtins.print")
    def test_sync_org_rules_verbose_mode(self, mock_print) -> None:
        source_dir = self.root_dir / "central_rules"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "org-01-new.md").write_text("# New Rule\n", encoding="utf-8")

        success = sync_org_rules(
            root_dir=self.root_dir,
            source_dir=source_dir,
            force=True,
            verbose=True,
        )
        self.assertTrue(success)
        mock_print.assert_called()
        printed_content = " ".join(str(call.args[0]) for call in mock_print.call_args_list if call.args)
        self.assertIn("Remote Origin:", printed_content)
        self.assertIn("Last Sync Timestamp:", printed_content)
        self.assertIn("Local Rules", printed_content)
        self.assertIn("Changes from Remote:", printed_content)
        self.assertIn("org-01-new.md", printed_content)

    @patch("builtins.print")
    def test_sync_org_rules_verbose_skipped_fresh(self, mock_print) -> None:
        record_sync_timestamp(self.root_dir)
        (self.org_dir / "org-01-existing.md").write_text("# Existing\n", encoding="utf-8")

        success = sync_org_rules(
            root_dir=self.root_dir,
            force=False,
            verbose=True,
        )
        self.assertTrue(success)
        mock_print.assert_called()
        printed_content = " ".join(str(call.args[0]) for call in mock_print.call_args_list if call.args)
        self.assertIn("Remote Origin:", printed_content)
        self.assertIn("Last Sync Timestamp:", printed_content)
        self.assertIn("Skipped (cache is fresh", printed_content)


    def test_load_dotenv(self) -> None:
        env_file = self.root_dir / ".env"
        env_file.write_text('ORG_RULES_SYNC_URL="https://example.com/rules.zip"\n', encoding="utf-8")
        import os
        os.environ.pop("ORG_RULES_SYNC_URL", None)
        load_dotenv(self.root_dir)
        self.assertEqual(os.environ.get("ORG_RULES_SYNC_URL"), "https://example.com/rules.zip")

    @patch("builtins.print")
    def test_sync_org_rules_verbose_empty_cache(self, mock_print) -> None:
        # No remote URL and empty org dir
        success = sync_org_rules(
            root_dir=self.root_dir,
            force=True,
            verbose=True,
        )
        self.assertFalse(success)
        mock_print.assert_called()
        printed_content = " ".join(str(call.args[0]) for call in mock_print.call_args_list if call.args)
        self.assertIn("Remote Origin:", printed_content)
        self.assertIn("Local Rules (0): None found", printed_content)
        self.assertIn("No remote URL configured", printed_content)


if __name__ == "__main__":
    unittest.main()
