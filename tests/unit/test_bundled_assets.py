#!/usr/bin/env python3
"""Unit tests for scripts/bundled_assets.py."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts.bundled_assets import (
    ENV_BLOCK_END_TAG,
    ENV_BLOCK_START_TAG,
    ENV_EXAMPLE_FILENAME,
    bootstrap_workspace_templates,
    load_env_example_template,
    merge_env_file,
    strip_env_block,
)


class TestBundledAssets(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_dir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_bootstrap_creates_directories_and_env_example_without_static_rules(self) -> None:
        written = bootstrap_workspace_templates(self.root_dir)
        self.assertEqual(written, [ENV_EXAMPLE_FILENAME])
        self.assertTrue((self.root_dir / ENV_EXAMPLE_FILENAME).is_file())
        content = (self.root_dir / ENV_EXAMPLE_FILENAME).read_text(encoding="utf-8")
        self.assertIn(ENV_BLOCK_START_TAG, content)
        self.assertIn(ENV_BLOCK_END_TAG, content)

        # Tier directories exist, but no hardcoded rule markdown files are written
        self.assertTrue((self.root_dir / ".agents" / "rules" / "org").is_dir())
        self.assertTrue((self.root_dir / ".agents" / "rules" / "team").is_dir())
        self.assertTrue((self.root_dir / ".agents" / "rules" / "personal" / ".gitkeep").is_file())
        self.assertEqual(list((self.root_dir / ".agents" / "rules" / "org").glob("*.md")), [])
        self.assertEqual(list((self.root_dir / ".agents" / "rules" / "team").glob("*.md")), [])

    def test_bootstrap_merges_into_existing_env_example_preserving_user_keys(self) -> None:
        env_ex = self.root_dir / ENV_EXAMPLE_FILENAME
        user_header = "# App Config\nDATABASE_URL=postgres://localhost/db\nGEMINI_MODEL=gemini-2.5-pro\n"
        env_ex.write_text(user_header, encoding="utf-8")

        written = bootstrap_workspace_templates(self.root_dir, overwrite=False)
        self.assertEqual(written, [ENV_EXAMPLE_FILENAME])
        merged = env_ex.read_text(encoding="utf-8")

        # User content preserved at top
        self.assertTrue(merged.startswith(user_header.rstrip()))
        self.assertIn(ENV_BLOCK_START_TAG, merged)
        # Colliding GEMINI_MODEL is commented out in the managed block so user's key wins
        self.assertIn("# GEMINI_MODEL=gemini-3.8-flash  # (preserved existing value above)", merged)

        # Second run is idempotent (no duplicate block)
        written_second = bootstrap_workspace_templates(self.root_dir, overwrite=False)
        self.assertEqual(written_second, [])
        self.assertEqual(env_ex.read_text(encoding="utf-8").count(ENV_BLOCK_START_TAG), 1)

        # strip_env_block restores original user content
        cleaned = strip_env_block(env_ex.read_text(encoding="utf-8"))
        self.assertEqual(cleaned, user_header)

    def test_merge_env_file_collision_and_overrides(self) -> None:
        env_file = self.root_dir / ".env"
        env_file.write_text(
            "PORT=8080\nGEMINI_MODEL=gemini-2.5-pro\n",
            encoding="utf-8",
        )

        modified, colliding = merge_env_file(
            env_file,
            overrides={"ORG_RULES_SYNC_URL": '"https://example.com/rules.zip"'},
        )
        self.assertTrue(modified)
        self.assertEqual(colliding, ["GEMINI_MODEL"])

        text = env_file.read_text(encoding="utf-8")
        self.assertIn("PORT=8080", text)
        self.assertIn("GEMINI_MODEL=gemini-2.5-pro", text)
        self.assertIn('ORG_RULES_SYNC_URL="https://example.com/rules.zip"', text)
        self.assertIn("# GEMINI_MODEL=gemini-3.8-flash  # (preserved existing value above)", text)

    def test_load_env_example_from_meipass_and_zipapp(self) -> None:
        meipass_dir = self.root_dir / "meipass"
        meipass_env = meipass_dir / ENV_EXAMPLE_FILENAME
        meipass_env.parent.mkdir(parents=True, exist_ok=True)
        meipass_env.write_text("# MEIPASS ENV EXAMPLE\n", encoding="utf-8")

        with patch("sys._MEIPASS", str(meipass_dir), create=True):
            self.assertEqual(load_env_example_template(), "# MEIPASS ENV EXAMPLE\n")

        pyz_path = self.root_dir / "agentic-dev.pyz"
        with zipfile.ZipFile(pyz_path, "w") as zf:
            zf.writestr(ENV_EXAMPLE_FILENAME, "# PYZ ENV EXAMPLE\n")

        with patch("scripts.bundled_assets._find_zipapp_archive", return_value=pyz_path):
            self.assertEqual(load_env_example_template(), "# PYZ ENV EXAMPLE\n")


if __name__ == "__main__":
    unittest.main()
