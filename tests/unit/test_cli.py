#!/usr/bin/env python3
"""Unit tests for scripts/cli.py unified CLI dispatcher."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.build_binary import (
    BINARY_FILENAME,
    ZIPAPP_FILENAME,
    package_cached_onedir_binary,
    set_executable,
)
from scripts.build_binary import (
    main as build_binary_main,
)
from scripts.cli import main as cli_main


class TestUnifiedCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_dir = Path(self.temp_dir.name)
        (self.root_dir / ".git" / "hooks").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_version_and_help(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli_main(["version"])
        self.assertEqual(rc, 0)
        self.assertIn("agentic-dev 0.2.0", buf.getvalue())

        buf_help = io.StringIO()
        with redirect_stdout(buf_help):
            rc_help = cli_main(["--help"])
        self.assertEqual(rc_help, 0)
        self.assertIn("subcommands:", buf_help.getvalue())

    def test_unknown_subcommand_returns_2(self) -> None:
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = cli_main(["nonexistent-cmd"])
        self.assertEqual(rc, 2)
        self.assertIn("Unknown subcommand", err_buf.getvalue())

    def test_init_and_install_subcommands(self) -> None:
        rc = cli_main(["init", "--all", "-y", "--no-gitignore", "--root-dir", str(self.root_dir)])
        self.assertEqual(rc, 0)
        self.assertTrue((self.root_dir / ".git" / "hooks" / "pre-commit").is_file())
        self.assertTrue((self.root_dir / ".env.example").is_file())
        self.assertTrue((self.root_dir / "AGENTS.md").is_file())
        self.assertTrue((self.root_dir / ".agents" / "rules" / "org").is_dir())
        self.assertTrue((self.root_dir / ".agents" / "rules" / "team").is_dir())

        # Clean via `install --clean`
        rc_clean = cli_main(["install", "--clean", "--root-dir", str(self.root_dir)])
        self.assertEqual(rc_clean, 0)
        self.assertFalse((self.root_dir / ".git" / "hooks" / "pre-commit").exists())

    def test_prefs_subcommand(self) -> None:
        rc_add = cli_main(
            [
                "prefs",
                "--add",
                "Prefer dataclasses over raw dicts",
                "--root-dir",
                str(self.root_dir),
            ]
        )
        self.assertEqual(rc_add, 0)

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc_list = cli_main(["prefs", "--list", "--root-dir", str(self.root_dir)])
        self.assertEqual(rc_list, 0)
        self.assertIn("Prefer dataclasses over raw dicts", buf.getvalue())

    def test_review_subcommand_skip_llm(self) -> None:
        rc = cli_main(
            [
                "review",
                "--skip-llm",
                "--root-dir",
                str(self.root_dir),
                "--audit-file",
                str(self.root_dir / ".agents" / "audit.log"),
            ]
        )
        self.assertEqual(rc, 0)

    def test_sync_subcommand_local_source(self) -> None:
        src_rules = self.root_dir / "remote_org_rules"
        src_rules.mkdir(parents=True, exist_ok=True)
        (src_rules / "org-01-test.md").write_text("# Org Rule\n", encoding="utf-8")

        rc = cli_main(
            [
                "sync",
                "--root-dir",
                str(self.root_dir),
                "--source-dir",
                str(src_rules),
                "--force",
            ]
        )
        self.assertEqual(rc, 0)
        self.assertTrue((self.root_dir / ".agents" / "rules" / "org" / "org-01-test.md").is_file())

    def test_doctor_subcommand(self) -> None:
        cli_main(["init", "--git-hook", "--no-gitignore", "--root-dir", str(self.root_dir)])
        buf = io.StringIO()
        with patch("scripts.cli.get_adc_access_token", return_value=None), redirect_stdout(buf):
            rc = cli_main(["doctor", "--root-dir", str(self.root_dir)])
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertIn("agentic-dev doctor", output)
        self.assertIn("Pre-Commit:    ✅ Installed", output)
        self.assertIn("Config (.env): ⚠️  Missing .env", output)
        self.assertIn("Active Rules:", output)

    def test_binary_init_gitignore_excludes_scripts(self) -> None:
        rc = cli_main(["init", "--all", "-y", "--gitignore", "--root-dir", str(self.root_dir)])
        self.assertEqual(rc, 0)
        gitignore_content = (self.root_dir / ".gitignore").read_text(encoding="utf-8")
        non_comment_lines = [
            line.strip()
            for line in gitignore_content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        self.assertIn(".agents/hooks.json", non_comment_lines)
        self.assertIn(".agents/audit.log", non_comment_lines)
        self.assertNotIn("scripts/pre_commit_reviewer.py", non_comment_lines)
        self.assertNotIn("scripts/install_tooling.py", non_comment_lines)
        self.assertFalse(any(line.startswith("scripts/") for line in non_comment_lines))

    def test_sync_without_dotenv_and_zero_rules_fails_loudly(self) -> None:
        import os

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ORG_RULES_SYNC_URL", None)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_main(["sync", "--root-dir", str(self.root_dir)])
            self.assertEqual(rc, 1)
            output = buf.getvalue()
            self.assertIn("No .env file found", output)
            self.assertIn("No organizational rules loaded (0 rules", output)
            self.assertNotIn("synchronized successfully", output)

    def test_init_warns_when_not_git_repository(self) -> None:
        non_git_dir = self.root_dir / "not_a_git_repo"
        non_git_dir.mkdir(parents=True, exist_ok=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli_main(["init", "--all", "-y", "--no-gitignore", "--root-dir", str(non_git_dir)])
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertIn("not a Git-initialized repository (.git directory not found)", output)

    def test_init_interactive_wizard(self) -> None:
        import os

        # Step 1: '2' (Antigravity only)
        # Step 2: 'https://example.com/org-rules.zip' (configure .env)
        # Step 3: 'y' (add generated files to .gitignore without scripts/)
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("builtins.input", side_effect=["2", "https://example.com/org-rules.zip", "y"]),
            patch("scripts.install_tooling.sync_org_rules", return_value=True),
        ):
            os.environ.pop("ORG_RULES_SYNC_URL", None)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_main(["init", "--root-dir", str(self.root_dir)])
            self.assertEqual(rc, 0)
            output = buf.getvalue()
            self.assertIn("Agentic Dev Interactive Setup Wizard", output)

        # Verify Antigravity installed, Claude NOT installed
        self.assertTrue((self.root_dir / ".agents" / "hooks.json").is_file())
        self.assertFalse((self.root_dir / ".claude").exists())

        # Verify .env created with ORG_RULES_SYNC_URL
        env_file = self.root_dir / ".env"
        self.assertTrue(env_file.is_file())
        self.assertIn('ORG_RULES_SYNC_URL="https://example.com/org-rules.zip"', env_file.read_text(encoding="utf-8"))

        # Verify .gitignore created without scripts/
        gi_text = (self.root_dir / ".gitignore").read_text(encoding="utf-8")
        gi_entries = [
            line.strip()
            for line in gi_text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        self.assertIn(".agents/hooks.json", gi_entries)
        self.assertFalse(any(line.startswith("scripts/") for line in gi_entries))

    def test_build_binary_pyz_only_builds_only_zipapp(self) -> None:
        project_root = Path(__file__).resolve().parent.parent.parent
        out_dir = self.root_dir / "dist_pyz"
        install_dir = self.root_dir / "local_bin"

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = build_binary_main(
                [
                    "--root-dir",
                    str(project_root),
                    "--output-dir",
                    str(out_dir),
                    "--pyz-only",
                    "--install-local",
                    "--install-dir",
                    str(install_dir),
                ]
            )
        self.assertEqual(rc, 0)
        self.assertTrue((out_dir / ZIPAPP_FILENAME).is_file())
        self.assertFalse((out_dir / BINARY_FILENAME).exists())
        self.assertTrue((install_dir / BINARY_FILENAME).is_file())

    def test_build_binary_native_only_success_and_missing_pyinstaller(self) -> None:
        project_root = Path(__file__).resolve().parent.parent.parent
        out_dir = self.root_dir / "dist_native"

        # 1. When PyInstaller is unavailable, --native-only returns 1 and does not build .pyz
        err_buf = io.StringIO()
        with (
            patch("scripts.build_binary.build_native_binary", return_value=None),
            redirect_stderr(err_buf),
        ):
            rc_fail = build_binary_main(
                [
                    "--root-dir",
                    str(project_root),
                    "--output-dir",
                    str(out_dir),
                    "--native-only",
                ]
            )
        self.assertEqual(rc_fail, 1)
        self.assertIn("cannot build --native-only binary", err_buf.getvalue())
        self.assertFalse((out_dir / ZIPAPP_FILENAME).exists())

        # 2. When PyInstaller succeeds, --native-only builds only native binary and skips .pyz
        out_dir.mkdir(parents=True, exist_ok=True)
        fake_native = out_dir / BINARY_FILENAME
        fake_native.write_bytes(b"#!/bin/sh\necho native\n")

        out_buf = io.StringIO()
        with (
            patch("scripts.build_binary.build_native_binary", return_value=fake_native),
            redirect_stdout(out_buf),
        ):
            rc_ok = build_binary_main(
                [
                    "--root-dir",
                    str(project_root),
                    "--output-dir",
                    str(out_dir),
                    "--native-only",
                ]
            )
        self.assertEqual(rc_ok, 0)
        self.assertTrue((out_dir / BINARY_FILENAME).is_file())
        self.assertFalse((out_dir / ZIPAPP_FILENAME).exists())

    def test_package_cached_onedir_binary_extracts_and_runs(self) -> None:
        import os
        import subprocess

        if os.name == "nt":
            return

        bundle_dir = self.root_dir / "fake_bundle" / "agentic-dev"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        inner_bin = bundle_dir / "agentic-dev"
        inner_bin.write_text(
            '#!/bin/sh\necho "invoked:$AGENTIC_DEV_INVOKING_BIN args:$*"\n',
            encoding="utf-8",
        )
        set_executable(inner_bin)

        out_bin = self.root_dir / "dist_cached" / BINARY_FILENAME
        cache_dir = self.root_dir / "xdg_cache"
        env = {**os.environ, "XDG_CACHE_HOME": str(cache_dir)}

        package_cached_onedir_binary(bundle_dir, out_bin, prewarm_cache=False)
        self.assertTrue(out_bin.is_file())

        # Cold run extracts into XDG_CACHE_HOME and executes inner binary
        res1 = subprocess.run([str(out_bin), "version"], env=env, capture_output=True, text=True, check=True)
        self.assertIn(f"invoked:{out_bin} args:version", res1.stdout)
        self.assertTrue((cache_dir / "agentic-dev").is_dir())

        # Warm run reuses extracted cache directory directly
        res2 = subprocess.run([str(out_bin), "doctor"], env=env, capture_output=True, text=True, check=True)
        self.assertIn(f"invoked:{out_bin} args:doctor", res2.stdout)

    def test_init_prints_auth_tip_when_key_missing(self) -> None:
        import os

        from scripts.constants import ENV_CONFIG_DIR

        cfg_dir = self.root_dir / "empty_cfg"
        with patch.dict(os.environ, {ENV_CONFIG_DIR: str(cfg_dir)}, clear=False):
            os.environ.pop("GOOGLE_AI_STUDIO_KEY", None)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_main(["init", "--git-hook", "-y", "--no-gitignore", "--root-dir", str(self.root_dir)])
        self.assertEqual(rc, 0)
        self.assertIn("agentic-dev auth set", buf.getvalue())

    def test_init_wizard_global_binary_and_path_step(self) -> None:
        import os

        from scripts.install_tooling import ensure_dir_in_shell_rc

        fake_download = self.root_dir / "Downloads" / "agentic-dev-macos-arm64"
        fake_download.parent.mkdir(parents=True, exist_ok=True)
        fake_download.write_text("#!/bin/sh\necho fake-bin\n", encoding="utf-8")

        fake_bin_dir = self.root_dir / "global_bin"
        fake_rc = self.root_dir / ".zshrc"

        # Step 1: '2', Step 2: 'skip', Step 3: 'n', Step 4a: 'y' (copy), Step 4b: 'y' (add to rc)
        with (
            patch.dict(
                os.environ,
                {"AGENTIC_DEV_BIN_DIR": str(fake_bin_dir), "PATH": "/usr/bin:/bin"},
                clear=False,
            ),
            patch("scripts.install_tooling._detect_invoking_binary_path", return_value=fake_download),
            patch("scripts.install_tooling._detect_shell_rc_file", return_value=fake_rc),
            patch("builtins.input", side_effect=["2", "skip", "n", "y", "y"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_main(["init", "--root-dir", str(self.root_dir)])
            self.assertEqual(rc, 0)
            out = buf.getvalue()
            self.assertIn("[Step 4/4] Global CLI Installation (PATH):", out)

        expected_bin = fake_bin_dir / ("agentic-dev.exe" if os.name == "nt" else "agentic-dev")
        self.assertTrue(expected_bin.is_file())
        if os.name != "nt":
            rc_text = fake_rc.read_text(encoding="utf-8")
            self.assertIn(f'export PATH="{fake_bin_dir.resolve()}:$PATH"', rc_text)
            # Calling ensure_dir_in_shell_rc a second time is idempotent
            _, modified_again = ensure_dir_in_shell_rc(fake_bin_dir, fake_rc)
            self.assertFalse(modified_again)

    def test_init_install_global_flag_non_interactive(self) -> None:
        import os

        fake_download = self.root_dir / "Downloads" / "agentic-dev"
        fake_download.parent.mkdir(parents=True, exist_ok=True)
        fake_download.write_text("#!/bin/sh\necho fake-bin\n", encoding="utf-8")

        fake_bin_dir = self.root_dir / "flag_bin"
        fake_rc = self.root_dir / ".zshrc"

        with (
            patch.dict(
                os.environ,
                {"AGENTIC_DEV_BIN_DIR": str(fake_bin_dir), "PATH": "/usr/bin:/bin"},
                clear=False,
            ),
            patch("scripts.install_tooling._detect_invoking_binary_path", return_value=fake_download),
            patch("scripts.install_tooling._detect_shell_rc_file", return_value=fake_rc),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_main(
                    [
                        "init",
                        "--git-hook",
                        "-y",
                        "--no-gitignore",
                        "--install-global",
                        "--root-dir",
                        str(self.root_dir),
                    ]
                )
            self.assertEqual(rc, 0)

        expected_bin = fake_bin_dir / ("agentic-dev.exe" if os.name == "nt" else "agentic-dev")
        self.assertTrue(expected_bin.is_file())

    def test_uninstall_subcommand_removes_binary_rc_cache_and_optional_purge(self) -> None:
        base = Path(self.root_dir) / "uninstall_case"
        fake_bin_dir = base / "bin"
        fake_cache_home = base / "cache_home"
        fake_config_dir = base / "config_dir"
        fake_rc = base / ".zshrc"

        fake_bin_dir.mkdir(parents=True, exist_ok=True)
        binary_name = "agentic-dev.exe" if os.name == "nt" else "agentic-dev"
        fake_bin = fake_bin_dir / binary_name
        fake_bin.write_bytes(b"#!/bin/sh\necho bin\n")

        cache_app_dir = fake_cache_home / "agentic-dev"
        cache_app_dir.mkdir(parents=True, exist_ok=True)
        (cache_app_dir / "model_cache.json").write_text("{}", encoding="utf-8")

        fake_config_dir.mkdir(parents=True, exist_ok=True)
        creds_file = fake_config_dir / "credentials.env"
        creds_file.write_text("GEMINI_API_KEY=secret\n", encoding="utf-8")

        fake_rc.write_text(
            "# user comment\n"
            'export PATH="/custom/bin:$PATH"\n'
            "\n"
            "# Added by agentic-dev\n"
            f'export PATH="{fake_bin_dir}:$PATH"\n',
            encoding="utf-8",
        )

        env_overrides = {
            "AGENTIC_DEV_BIN_DIR": str(fake_bin_dir),
            "XDG_CACHE_HOME": str(fake_cache_home),
            "AGENTIC_DEV_CONFIG_DIR": str(fake_config_dir),
        }
        with (
            patch.dict(os.environ, env_overrides, clear=False),
            patch("scripts.install_tooling._candidate_shell_rc_files", return_value=[fake_rc]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_main(["uninstall"])
            self.assertEqual(rc, 0)

            self.assertFalse(fake_bin.exists())
            self.assertFalse(cache_app_dir.exists())
            rc_text = fake_rc.read_text(encoding="utf-8")
            self.assertIn('export PATH="/custom/bin:$PATH"', rc_text)
            self.assertNotIn("# Added by agentic-dev", rc_text)
            self.assertNotIn(str(fake_bin_dir), rc_text)
            # Credentials preserved unless --purge is supplied
            self.assertTrue(creds_file.exists())

            buf_purge = io.StringIO()
            with redirect_stdout(buf_purge):
                rc_purge = cli_main(["uninstall", "--purge"])
            self.assertEqual(rc_purge, 0)
            self.assertFalse(fake_config_dir.exists())


if __name__ == "__main__":
    unittest.main()
