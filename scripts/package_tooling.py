#!/usr/bin/env python3
"""Portable Dev Environment Tooling Packager.

Packages canonical rules, reviewer scripts, dynamic assistant installers,
and configuration templates into a standalone zip archive that can be
distributed and extracted into other workspaces.
"""

from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys
import zipfile
from collections.abc import Sequence
from pathlib import Path

DEFAULT_OUTPUT_ZIP = "dist/dev-env-tooling.zip"

DEFAULT_INCLUDED_FILES: tuple[str, ...] = (
    "AGENTS.md",
    ".env.example",
)

DEFAULT_INCLUDED_DIRECTORIES: tuple[str, ...] = (
    "scripts",
    ".agents/rules",
)

EXCLUDED_DIR_NAMES: tuple[str, ...] = (
    "__pycache__",
    ".git",
    ".venv",
    ".pytest_cache",
    ".llm_cache",
    "dist",
    "build",
    ".claude",
    "skills",
)

EXCLUDED_FILE_EXTENSIONS: tuple[str, ...] = (
    ".pyc",
    ".pyo",
    ".pyd",
    ".DS_Store",
    ".zip",
)

BUNDLE_README_CONTENT = """# Portable Dev-Environment Tooling Bundle

This package contains the canonical rules catalog, multi-assistant reviewer scripts,
and dynamic installers for Antigravity, Claude Code, and Git hooks.

## Quick Start in Any Workspace

1. Extract this bundle into the root of your project/workspace:
   ```bash
   unzip dev-env-tooling.zip -d /path/to/target-workspace
   ```

2. Run the dynamic installer to wire up hooks and assistant configurations:
   ```bash
   cd /path/to/target-workspace
   python3 scripts/install_tooling.py --all
   ```

3. (Optional) Configure Gemini API for LLM-powered reviews:
   ```bash
   cp .env.example .env
   # Edit .env and set GEMINI_API_KEY
   ```

4. Audit staged code changes at any time:
   ```bash
   python3 scripts/pre_commit_reviewer.py
   ```
"""


def _is_excluded(path: Path) -> bool:
    """Check if path should be excluded from packaging."""
    parts = path.parts
    for excluded in EXCLUDED_DIR_NAMES:
        if excluded in parts:
            return True

    if path.name in (".DS_Store", "hooks.json") and ".agents" in parts:
        return True

    if path.name == ".sync_timestamp":
        return True

    if "personal" in parts and ".agents" in parts and path.name != ".gitkeep":
        return True

    if path.suffix.lower() in EXCLUDED_FILE_EXTENSIONS:
        return True

    if path.name.startswith(".") and path.name not in (".env.example", ".agents"):
        return True

    return False


def resolve_package_files(
    root_dir: Path | str,
    include_tests: bool = False,
    extra_patterns: Sequence[str] | None = None,
) -> list[tuple[Path, str]]:
    """Resolve all file paths that should be packaged into the bundle.

    Returns a sorted list of (absolute_source_path, relative_archive_path).
    """
    root = Path(root_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Root directory does not exist: {root}")

    collected: dict[str, Path] = {}

    # 1. Include explicit files
    for rel_file in DEFAULT_INCLUDED_FILES:
        target = root / rel_file
        if target.is_file():
            collected[rel_file] = target

    # 2. Include default directories
    for rel_dir in DEFAULT_INCLUDED_DIRECTORIES:
        target_dir = root / rel_dir
        if target_dir.is_dir():
            for item in target_dir.rglob("*"):
                if item.is_file() and not _is_excluded(item):
                    archive_rel = str(item.relative_to(root)).replace("\\", "/")
                    collected[archive_rel] = item

    # 3. Include tests if requested
    if include_tests:
        tests_dir = root / "tests"
        if tests_dir.is_dir():
            for item in tests_dir.rglob("*"):
                if item.is_file() and not _is_excluded(item):
                    archive_rel = str(item.relative_to(root)).replace("\\", "/")
                    collected[archive_rel] = item

    # 4. Include extra custom patterns if specified
    if extra_patterns:
        for pattern in extra_patterns:
            for item in root.glob(pattern):
                if item.is_file() and not _is_excluded(item):
                    archive_rel = str(item.relative_to(root)).replace("\\", "/")
                    collected[archive_rel] = item
                elif item.is_dir():
                    for sub in item.rglob("*"):
                        if sub.is_file() and not _is_excluded(sub):
                            archive_rel = str(sub.relative_to(root)).replace("\\", "/")
                            collected[archive_rel] = sub

    return sorted([(path, rel) for rel, path in collected.items()], key=lambda x: x[1])


def package_tooling(
    root_dir: Path | str = ".",
    output_zip: Path | str = DEFAULT_OUTPUT_ZIP,
    include_tests: bool = False,
    extra_patterns: Sequence[str] | None = None,
    include_bundle_readme: bool = True,
) -> tuple[Path, list[str]]:
    """Package canonical repository tooling and rules into a portable zip file.

    Returns (created_zip_path, list_of_packaged_file_names).
    """
    root = Path(root_dir).resolve()
    out_path = Path(output_zip)
    if not out_path.is_absolute():
        out_path = root / out_path

    out_path.parent.mkdir(parents=True, exist_ok=True)

    files_to_pack = resolve_package_files(
        root_dir=root,
        include_tests=include_tests,
        extra_patterns=extra_patterns,
    )

    packaged_names: list[str] = []

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
        # Add quick start bundle README
        if include_bundle_readme:
            readme_zinfo = zipfile.ZipInfo("README_TOOLING.md")
            readme_zinfo.compress_type = zipfile.ZIP_DEFLATED
            readme_zinfo.external_attr = (0o644 & 0xFFFF) << 16
            zipf.writestr(readme_zinfo, BUNDLE_README_CONTENT.strip() + "\n")
            packaged_names.append("README_TOOLING.md")

        for src_path, rel_name in files_to_pack:
            data = src_path.read_bytes()
            zinfo = zipfile.ZipInfo(rel_name)
            zinfo.compress_type = zipfile.ZIP_DEFLATED

            src_mode = src_path.stat().st_mode
            # Ensure python and shell scripts retain executable permissions
            if src_path.suffix in (".py", ".sh") or (src_mode & stat.S_IXUSR):
                src_mode |= stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH | stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH

            zinfo.external_attr = (src_mode & 0xFFFF) << 16
            zipf.writestr(zinfo, data)
            packaged_names.append(rel_name)

    return out_path, packaged_names


def list_bundle_contents(zip_path: Path | str) -> list[str]:
    """List all entry names in a packaged tooling zip archive."""
    zpath = Path(zip_path).resolve()
    if not zpath.is_file():
        raise FileNotFoundError(f"Zip archive not found: {zpath}")

    with zipfile.ZipFile(zpath, "r") as zipf:
        return zipf.namelist()


def extract_tooling(
    zip_path: Path | str,
    destination_dir: Path | str,
    run_install: bool = False,
    assume_yes: bool = False,
) -> list[str]:
    """Extract tooling archive into a destination directory and optionally run installer.

    Safeguards existing AGENTS.md / CLAUDE.md files against silent overwrites.
    Returns the list of extracted relative file paths.
    """
    zpath = Path(zip_path).resolve()
    if not zpath.is_file():
        raise FileNotFoundError(f"Zip archive not found: {zpath}")

    dest = Path(destination_dir).resolve()
    dest.mkdir(parents=True, exist_ok=True)

    extracted_files: list[str] = []

    with zipfile.ZipFile(zpath, "r") as zipf:
        for zinfo in zipf.infolist():
            filename_lower = Path(zinfo.filename).name.lower()
            if filename_lower in ("agents.md", "claude.md"):
                dest_file = dest / zinfo.filename
                alt_name = "agents.md" if zinfo.filename == "AGENTS.md" else "claude.md"
                alt_file = dest / alt_name
                existing = dest_file if dest_file.exists() else (alt_file if alt_file.exists() else None)
                if existing and existing.is_file() and existing.stat().st_size > 0:
                    archive_content = zipf.read(zinfo)
                    if existing.read_bytes() != archive_content:
                        dist_name = zinfo.filename + ".dist"
                        (dest / dist_name).write_bytes(archive_content)
                        extracted_files.append(dist_name)
                        print(f"⚠️  Existing {existing.name} detected in target. Preserved user file; extracted package version as {dist_name}.")
                        continue

            extracted_path = zipf.extract(zinfo, dest)
            extracted_files.append(zinfo.filename)

            # Restore execution permission if recorded in external_attr
            mode = (zinfo.external_attr >> 16) & 0xFFFF
            if mode:
                try:
                    os.chmod(extracted_path, mode)
                except OSError:
                    pass

    if run_install:
        installer = dest / "scripts" / "install_tooling.py"
        if installer.is_file():
            cmd = [sys.executable, str(installer), "--all", "--root-dir", str(dest)]
            if assume_yes:
                cmd.append("-y")
            subprocess.run(
                cmd,
                check=True,
            )

    return extracted_files


def main() -> None:
    """CLI entry point for packaging and extracting portable dev tooling."""
    parser = argparse.ArgumentParser(
        description="Portable Dev Environment Tooling Packager & Extractor",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=DEFAULT_OUTPUT_ZIP,
        help=f"Output zip file path (default: {DEFAULT_OUTPUT_ZIP})",
    )
    parser.add_argument(
        "-r",
        "--root-dir",
        default=".",
        help="Root repository path to package from (default: .)",
    )
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="Include unit and integration tests in package",
    )
    parser.add_argument(
        "-l",
        "--list",
        metavar="ZIP_PATH",
        help="List contents of an existing tooling zip bundle",
    )
    parser.add_argument(
        "-x",
        "--extract",
        metavar="DEST_DIR",
        help="Extract an existing package bundle to the destination directory",
    )
    parser.add_argument(
        "--from-zip",
        metavar="ZIP_PATH",
        default=DEFAULT_OUTPUT_ZIP,
        help=f"Source zip file to extract (used with --extract, default: {DEFAULT_OUTPUT_ZIP})",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Automatically run scripts/install_tooling.py after extraction",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Automatically accept insert-only pointer injection prompts during install",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress detailed packaging output",
    )

    args = parser.parse_args()

    # Mode 1: List contents
    if args.list:
        contents = list_bundle_contents(args.list)
        print(f"📦 Contents of {args.list} ({len(contents)} files):")
        for name in contents:
            print(f"  - {name}")
        return

    # Mode 2: Extract bundle
    if args.extract:
        dest = Path(args.extract)
        src_zip = Path(args.from_zip)
        if not args.quiet:
            print(f"📂 Extracting {src_zip} to {dest}...")
        extracted = extract_tooling(
            zip_path=src_zip,
            destination_dir=dest,
            run_install=args.install,
            assume_yes=args.yes,
        )
        if not args.quiet:
            print(f"✅ Extracted {len(extracted)} files to {dest}")
            if args.install:
                print("✅ Assistant tooling and hooks automatically configured.")
        return

    # Mode 3: Create package bundle
    root = Path(args.root_dir).resolve()
    out_path, packed_files = package_tooling(
        root_dir=root,
        output_zip=args.output,
        include_tests=args.include_tests,
    )

    if not args.quiet:
        size_kb = out_path.stat().st_size / 1024
        print(f"📦 Successfully created portable tooling bundle -> {out_path} ({size_kb:.1f} KB)")
        print(f"   Packaged {len(packed_files)} files:")
        for name in packed_files:
            print(f"     • {name}")
        print("\n💡 To install in another workspace:")
        print(f"   unzip {out_path.name} -d /path/to/target-workspace")
        print("   cd /path/to/target-workspace && python3 scripts/install_tooling.py --all")


if __name__ == "__main__":
    main()
