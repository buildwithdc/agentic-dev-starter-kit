#!/usr/bin/env python3
"""Unit tests for scripts/auth_manager.py and `agentic-dev auth` CLI."""

from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.auth_manager import (
    clear_credential,
    inspect_auth_status,
    mask_secret,
    set_credential,
)
from scripts.cli import main as cli_main
from scripts.constants import ENV_CONFIG_DIR, get_global_credentials_file
from scripts.sync_org_rules import load_dotenv


class TestAuthManager(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_dir = Path(self.temp_dir.name).resolve() / "repo"
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.global_cfg_dir = Path(self.temp_dir.name).resolve() / "global_cfg"

        self._env_patch = patch.dict(
            os.environ,
            {ENV_CONFIG_DIR: str(self.global_cfg_dir)},
            clear=False,
        )
        self._env_patch.start()
        os.environ.pop("GOOGLE_AI_STUDIO_KEY", None)

    def tearDown(self) -> None:
        os.environ.pop("GOOGLE_AI_STUDIO_KEY", None)
        self._env_patch.stop()
        self.temp_dir.cleanup()

    def test_mask_secret(self) -> None:
        self.assertEqual(mask_secret("abcd1234"), "********")
        self.assertEqual(mask_secret("AIzaSyTest123456WXYZ"), "AIza...WXYZ")

    def test_set_and_clear_global_credential_with_secure_permissions(self) -> None:
        target, masked, warnings = set_credential(
            raw_key="AIzaSyFakeKeyForUnitTest9876",
            root_dir=self.root_dir,
            local=False,
        )
        self.assertEqual(target, get_global_credentials_file())
        self.assertEqual(masked, "AIza...9876")
        self.assertEqual(warnings, [])
        self.assertTrue(target.is_file())

        content = target.read_text(encoding="utf-8")
        self.assertIn("GOOGLE_AI_STUDIO_KEY=AIzaSyFakeKeyForUnitTest9876", content)

        if os.name != "nt":
            mode = stat.S_IMODE(target.stat().st_mode)
            self.assertEqual(mode, 0o600)

        # Updating again replaces in-place without duplicating key
        set_credential(
            raw_key="AIzaSyUpdatedKeyForTest4321",
            root_dir=self.root_dir,
            local=False,
        )
        updated_lines = [
            line
            for line in target.read_text(encoding="utf-8").splitlines()
            if line.startswith("GOOGLE_AI_STUDIO_KEY=")
        ]
        self.assertEqual(updated_lines, ["GOOGLE_AI_STUDIO_KEY=AIzaSyUpdatedKeyForTest4321"])

        removed_path, removed = clear_credential(root_dir=self.root_dir, local=False)
        self.assertEqual(removed_path, target)
        self.assertTrue(removed)
        self.assertNotIn("GOOGLE_AI_STUDIO_KEY=", target.read_text(encoding="utf-8"))

    def test_set_credential_rejects_placeholders_and_newlines(self) -> None:
        for bad_value in ("", "   ", "your_google_ai_studio_key_here", "abc\ndef"):
            with self.assertRaises(ValueError):
                set_credential(bad_value, root_dir=self.root_dir)

    def test_set_local_credential_warns_when_env_not_gitignored(self) -> None:
        subprocess.run(["git", "init"], cwd=self.root_dir, capture_output=True, check=True)
        target, _, warnings = set_credential(
            raw_key="AIzaSyLocalKeyForRepo1111",
            root_dir=self.root_dir,
            local=True,
        )
        self.assertEqual(target, self.root_dir / ".env")
        self.assertTrue(any("not ignored by Git" in w for w in warnings))

        # Adding .env to .gitignore suppresses the warning
        (self.root_dir / ".gitignore").write_text(".env\n", encoding="utf-8")
        _, _, warnings_after = set_credential(
            raw_key="AIzaSyLocalKeyForRepo2222",
            root_dir=self.root_dir,
            local=True,
        )
        self.assertFalse(any("not ignored by Git" in w for w in warnings_after))

    def test_inspect_auth_status_precedence_and_layered_dotenv(self) -> None:
        set_credential("AIzaSyGlobalKey1111", root_dir=self.root_dir, local=False)
        status_global = inspect_auth_status(self.root_dir, include_adc=False)
        self.assertTrue(status_global.configured)
        self.assertEqual(status_global.source, "global_config")
        self.assertEqual(status_global.masked_key, "AIza...1111")

        # Repo .env overrides global config
        (self.root_dir / ".env").write_text(
            "GOOGLE_AI_STUDIO_KEY=AIzaSyRepoOverride2222\n",
            encoding="utf-8",
        )
        status_repo = inspect_auth_status(self.root_dir, include_adc=False)
        self.assertEqual(status_repo.source, "repo_env")
        self.assertEqual(status_repo.masked_key, "AIza...2222")

        # Load dotenv verifies repo wins over global in os.environ
        load_dotenv(self.root_dir)
        self.assertEqual(os.environ.get("GOOGLE_AI_STUDIO_KEY"), "AIzaSyRepoOverride2222")

        # Shell env overrides both
        with patch.dict(os.environ, {"GOOGLE_AI_STUDIO_KEY": "AIzaSyShellEnv3333"}, clear=False):
            status_env = inspect_auth_status(self.root_dir, include_adc=False)
            self.assertEqual(status_env.source, "environment")
            self.assertEqual(status_env.masked_key, "AIza...3333")

    def test_cli_auth_set_status_clear_and_doctor_integration(self) -> None:
        # 1. Interactive `auth set` using mocked getpass
        out_set = io.StringIO()
        with (
            patch("scripts.auth_manager.getpass.getpass", return_value="AIzaSyInteractive9999"),
            redirect_stdout(out_set),
        ):
            rc_set = cli_main(["auth", "set", "--root-dir", str(self.root_dir)])
        self.assertEqual(rc_set, 0)
        set_text = out_set.getvalue()
        self.assertIn("AIza...9999", set_text)
        self.assertNotIn("AIzaSyInteractive9999", set_text)

        # 2. `auth status --json`
        out_status = io.StringIO()
        with patch("scripts.auth_manager.get_adc_access_token", return_value=None), redirect_stdout(out_status):
            rc_status = cli_main(["auth", "status", "--root-dir", str(self.root_dir), "--json"])
        self.assertEqual(rc_status, 0)
        payload = json.loads(out_status.getvalue())
        self.assertTrue(payload["configured"])
        self.assertEqual(payload["source"], "global_config")
        self.assertEqual(payload["masked_key"], "AIza...9999")

        # 3. `doctor` reports user-global source without needing .env
        out_doc = io.StringIO()
        with patch("scripts.cli.get_adc_access_token", return_value=None), redirect_stdout(out_doc):
            rc_doc = cli_main(["doctor", "--root-dir", str(self.root_dir)])
        self.assertEqual(rc_doc, 0)
        doc_text = out_doc.getvalue()
        self.assertIn("Auth Status:   ✅ GOOGLE_AI_STUDIO_KEY configured (source: user-global", doc_text)
        os.environ.pop("GOOGLE_AI_STUDIO_KEY", None)

        # 4. `auth set --stdin` rejects invalid placeholder safely
        err_bad = io.StringIO()
        with (
            patch.object(os, "environ", os.environ),
            patch("sys.stdin", io.StringIO("your_google_ai_studio_key_here\n")),
            redirect_stderr(err_bad),
        ):
            rc_bad = cli_main(["auth", "set", "--stdin", "--root-dir", str(self.root_dir)])
        self.assertEqual(rc_bad, 1)
        self.assertIn("placeholder", err_bad.getvalue())

        # 5. `auth clear` removes stored global credential
        out_clear = io.StringIO()
        with redirect_stdout(out_clear):
            rc_clear = cli_main(["auth", "clear", "--root-dir", str(self.root_dir)])
        self.assertEqual(rc_clear, 0)
        self.assertIn("Removed GOOGLE_AI_STUDIO_KEY", out_clear.getvalue())


if __name__ == "__main__":
    unittest.main()
