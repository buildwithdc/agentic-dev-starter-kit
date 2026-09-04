#!/usr/bin/env python3
"""Unit tests for portable tooling packager."""

import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.package_tooling import (
    extract_tooling,
    list_bundle_contents,
    package_tooling,
    resolve_package_files,
)


class TestPackageTooling(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_dir = Path(self.temp_dir.name)

        # Create mock repository structure
        (self.root_dir / "scripts").mkdir(parents=True, exist_ok=True)
        (self.root_dir / ".agents" / "rules").mkdir(parents=True, exist_ok=True)
        (self.root_dir / "tests" / "unit").mkdir(parents=True, exist_ok=True)

        # Mock files
        (self.root_dir / "AGENTS.md").write_text("# Mock Agents Guidelines\n", encoding="utf-8")
        (self.root_dir / ".env.example").write_text("GEMINI_API_KEY=test\n", encoding="utf-8")
        (self.root_dir / "scripts" / "install_tooling.py").write_text("print('install')\n", encoding="utf-8")
        (self.root_dir / "scripts" / "pre_commit_reviewer.py").write_text("print('review')\n", encoding="utf-8")
        (self.root_dir / ".agents" / "rules" / "00_meta.md").write_text("# Rule\n", encoding="utf-8")
        (self.root_dir / "tests" / "unit" / "test_sample.py").write_text("# Test\n", encoding="utf-8")

        # Unwanted files that should be excluded
        pycache_dir = self.root_dir / "scripts" / "__pycache__"
        pycache_dir.mkdir(parents=True, exist_ok=True)
        (pycache_dir / "install_tooling.cpython-314.pyc").write_bytes(b"pyc")
        (self.root_dir / ".DS_Store").write_bytes(b"ds_store")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_resolve_package_files(self) -> None:
        files = resolve_package_files(self.root_dir, include_tests=False)
        rel_paths = [rel for _, rel in files]

        self.assertIn("AGENTS.md", rel_paths)
        self.assertIn(".env.example", rel_paths)
        self.assertIn("scripts/install_tooling.py", rel_paths)
        self.assertIn("scripts/pre_commit_reviewer.py", rel_paths)
        self.assertIn(".agents/rules/00_meta.md", rel_paths)

        # Ensure excluded files are not included
        for path in rel_paths:
            self.assertNotIn("__pycache__", path)
            self.assertNotIn(".DS_Store", path)
            self.assertFalse(path.endswith(".pyc"))

    def test_resolve_package_files_with_tests(self) -> None:
        files = resolve_package_files(self.root_dir, include_tests=True)
        rel_paths = [rel for _, rel in files]
        self.assertIn("tests/unit/test_sample.py", rel_paths)

    def test_package_and_list_tooling(self) -> None:
        out_zip = self.root_dir / "dist" / "bundle.zip"
        zip_path, packaged_files = package_tooling(
            root_dir=self.root_dir,
            output_zip=out_zip,
            include_tests=False,
        )

        self.assertTrue(zip_path.is_file())
        self.assertIn("README_TOOLING.md", packaged_files)
        self.assertIn("scripts/install_tooling.py", packaged_files)

        # Test listing contents
        contents = list_bundle_contents(zip_path)
        self.assertEqual(contents, packaged_files)

    def test_extract_tooling(self) -> None:
        out_zip = self.root_dir / "dist" / "bundle.zip"
        package_tooling(
            root_dir=self.root_dir,
            output_zip=out_zip,
            include_tests=False,
        )

        extract_target = self.root_dir / "extracted_workspace"
        extracted = extract_tooling(out_zip, extract_target)

        self.assertIn("scripts/install_tooling.py", extracted)
        self.assertIn(".agents/rules/00_meta.md", extracted)
        self.assertIn("AGENTS.md", extracted)

        extracted_installer = extract_target / "scripts" / "install_tooling.py"
        self.assertTrue(extracted_installer.is_file())
        self.assertEqual(extracted_installer.read_text(encoding="utf-8"), "print('install')\n")

    def test_cli_execution(self) -> None:
        # Test CLI packager
        out_zip = self.root_dir / "cli_bundle.zip"
        pkg_script = Path(__file__).resolve().parent.parent.parent / "scripts" / "package_tooling.py"
        res = subprocess.run(
            [sys.executable, str(pkg_script), "--root-dir", str(self.root_dir), "--output", str(out_zip)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, msg=res.stderr)
        self.assertTrue(out_zip.is_file())

        # Test CLI list
        res_list = subprocess.run(
            [sys.executable, str(pkg_script), "--list", str(out_zip)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_list.returncode, 0)
        self.assertIn("AGENTS.md", res_list.stdout)


if __name__ == "__main__":
    unittest.main()
