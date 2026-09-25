#!/usr/bin/env python3
"""Unit tests for organizational rule synchronizer."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.sync_org_rules import (
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

    def test_load_dotenv(self) -> None:
        env_file = self.root_dir / ".env"
        env_file.write_text('ORG_RULES_SYNC_URL="https://example.com/rules.zip"\n', encoding="utf-8")
        import os
        os.environ.pop("ORG_RULES_SYNC_URL", None)
        load_dotenv(self.root_dir)
        self.assertEqual(os.environ.get("ORG_RULES_SYNC_URL"), "https://example.com/rules.zip")


if __name__ == "__main__":
    unittest.main()
