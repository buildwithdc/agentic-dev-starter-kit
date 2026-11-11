#!/usr/bin/env python3
"""End-to-end integration test for the standalone `agentic-dev` binary lifecycle.

Verifies that a target Git repository with ZERO `scripts/*.py` files can:
1. Initialize hooks, skills, and rule directories via `agentic-dev init`.
2. Dynamically sync organizational rules via `agentic-dev sync`.
3. Record personal preferences via `agentic-dev prefs`.
4. Run `agentic-dev doctor` diagnostics.
5. Execute `.git/hooks/pre-commit` via `agentic-dev review` on `git commit`.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.build_binary import BINARY_FILENAME, build_zipapp


class TestBinaryLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base_dir = Path(self.temp_dir.name)
        self.repo_dir = self.base_dir / "target_app_repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.bin_dir = self.base_dir / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)

        # Initialize clean target git repo (NO scripts/ directory copied!)
        subprocess.run(["git", "init"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
        )

        # Build executable `agentic-dev` into `self.bin_dir` for fast, deterministic execution
        project_root = Path(__file__).resolve().parent.parent.parent
        self.agentic_bin = self.bin_dir / BINARY_FILENAME
        build_zipapp(root_dir=project_root, output_path=self.agentic_bin)

        # Create mock remote org rules source directory
        self.remote_org_dir = self.base_dir / "remote_org_rules"
        self.remote_org_dir.mkdir(parents=True, exist_ok=True)
        (self.remote_org_dir / "org-01-security.md").write_text(
            "---\nid: org-01-security\ntitle: No Hardcoded Secrets\nseverity_default: CRITICAL\napplies_to:\n  - \"**/*\"\n---\n# No Secrets\nNever commit plaintext API keys.\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_zero_script_target_repo_full_validation_loop(self) -> None:
        # Confirm target repo has NO scripts/ directory
        self.assertFalse((self.repo_dir / "scripts").exists())

        env = {
            "PATH": f"{self.bin_dir}{os.pathsep}/opt/homebrew/bin{os.pathsep}/usr/local/bin{os.pathsep}/usr/bin{os.pathsep}/bin",
            "HOME": str(self.base_dir),
            "NO_GCE_CHECK": "true",
        }

        # 1. `agentic-dev init --all -y --no-gitignore`
        init_res = subprocess.run(
            [str(self.agentic_bin), "init", "--all", "-y", "--no-gitignore", "--root-dir", str(self.repo_dir)],
            cwd=self.repo_dir,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(init_res.returncode, 0, msg=init_res.stderr + init_res.stdout)
        self.assertFalse((self.repo_dir / "scripts").exists())
        self.assertTrue((self.repo_dir / ".git" / "hooks" / "pre-commit").is_file())
        self.assertTrue((self.repo_dir / ".env.example").is_file())
        self.assertTrue((self.repo_dir / "AGENTS.md").is_file())
        self.assertTrue((self.repo_dir / ".agents" / "hooks.json").is_file())

        # 2. `agentic-dev sync --source-dir ... --force` (dynamic org rule sync)
        sync_res = subprocess.run(
            [
                str(self.agentic_bin),
                "sync",
                "--source-dir",
                str(self.remote_org_dir),
                "--force",
                "--root-dir",
                str(self.repo_dir),
            ],
            cwd=self.repo_dir,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(sync_res.returncode, 0, msg=sync_res.stderr)
        self.assertTrue((self.repo_dir / ".agents" / "rules" / "org" / "org-01-security.md").is_file())

        # 3. `agentic-dev prefs --add ...`
        prefs_res = subprocess.run(
            [
                str(self.agentic_bin),
                "prefs",
                "--add",
                "Use explicit type hints on all public functions",
                "--root-dir",
                str(self.repo_dir),
            ],
            cwd=self.repo_dir,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(prefs_res.returncode, 0, msg=prefs_res.stderr)
        prefs_file = self.repo_dir / ".agents" / "rules" / "personal" / "personal-01-preferences.md"
        self.assertTrue(prefs_file.is_file())

        # 4. `agentic-dev doctor`
        doc_res = subprocess.run(
            [str(self.agentic_bin), "doctor", "--root-dir", str(self.repo_dir)],
            cwd=self.repo_dir,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(doc_res.returncode, 0, msg=doc_res.stderr)
        self.assertIn("Pre-Commit:    ✅ Installed", doc_res.stdout)
        self.assertIn("org=1", doc_res.stdout)
        self.assertIn("personal=1", doc_res.stdout)

        # 5. Stage a file and run `git commit` -> triggers `.git/hooks/pre-commit` -> `agentic-dev review`
        app_file = self.repo_dir / "handler.py"
        app_file.write_text("def handle_request(event: dict[str, str]) -> bool:\n    return True\n", encoding="utf-8")
        subprocess.run(["git", "add", "handler.py"], cwd=self.repo_dir, env=env, check=True)

        commit_res = subprocess.run(
            ["git", "commit", "-m", "feat(core): add request handler"],
            cwd=self.repo_dir,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(commit_res.returncode, 0, msg=commit_res.stderr + commit_res.stdout)
        self.assertTrue((self.repo_dir / ".agents" / "audit.log").is_file())
        self.assertFalse((self.repo_dir / "scripts").exists())

    def test_binary_in_project_root_not_on_path_runs_on_commit(self) -> None:
        """Verify `./agentic-dev` placed in project root works on `git commit` even when not on PATH."""
        import shutil

        local_bin = self.repo_dir / BINARY_FILENAME
        shutil.copy2(self.agentic_bin, local_bin)

        # Notice self.bin_dir is NOT in PATH here
        env_no_bin_path = {
            "PATH": f"/opt/homebrew/bin{os.pathsep}/usr/local/bin{os.pathsep}/usr/bin{os.pathsep}/bin",
            "HOME": str(self.base_dir),
            "NO_GCE_CHECK": "true",
        }

        init_res = subprocess.run(
            [str(local_bin), "init", "--all", "-y", "--gitignore", "--root-dir", str(self.repo_dir)],
            cwd=self.repo_dir,
            env=env_no_bin_path,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(init_res.returncode, 0, msg=init_res.stderr + init_res.stdout)

        # Local binary in project root should also be added to .gitignore automatically
        gi_text = (self.repo_dir / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(BINARY_FILENAME, gi_text)

        # Stage a file and commit without `agentic-dev` on PATH
        sample_file = self.repo_dir / "script.py"
        sample_file.write_text("def greet() -> str:\n    return 'hi'\n", encoding="utf-8")
        subprocess.run(["git", "add", "script.py"], cwd=self.repo_dir, env=env_no_bin_path, check=True)

        commit_res = subprocess.run(
            ["git", "commit", "-m", "feat(core): add greet"],
            cwd=self.repo_dir,
            env=env_no_bin_path,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(commit_res.returncode, 0, msg=commit_res.stderr + commit_res.stdout)
        combined_output = commit_res.stdout + commit_res.stderr
        self.assertNotIn("agentic-dev binary not found", combined_output)
        self.assertTrue((self.repo_dir / ".agents" / "audit.log").is_file())


if __name__ == "__main__":
    unittest.main()
