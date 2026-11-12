#!/usr/bin/env python3
"""Build pipeline for `agentic-dev` standalone CLI binary and portable `.pyz` zipapp.

Supports two complementary packaging tiers:
1. `dist/agentic-dev.pyz`: Ultra-lightweight stdlib zipapp (<100 KB, 0 external build deps).
2. `dist/agentic-dev`: Standalone native `--onefile` executable via PyInstaller (includes Python runtime).
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipapp
from collections.abc import Sequence
from pathlib import Path

DEFAULT_DIST_DIR = "dist"
ZIPAPP_FILENAME = "agentic-dev.pyz"
BINARY_FILENAME = "agentic-dev.exe" if sys.platform == "win32" else "agentic-dev"

ZIPAPP_MAIN_CONTENT = """# -*- coding: utf-8 -*-
import sys
from pathlib import Path

# Ensure bundled `scripts` package is importable both as `scripts.<mod>` and `<mod>`
archive_root = Path(__file__).resolve().parent
scripts_dir = archive_root / "scripts"
for p in (str(archive_root), str(scripts_dir)):
    if p not in sys.path:
        sys.path.insert(0, p)

from scripts.cli import main

if __name__ == "__main__":
    sys.exit(main())
"""


def _set_executable(path: Path) -> None:
    """Ensure a file has user/group/other executable permissions."""
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def build_zipapp(
    root_dir: Path | str = ".",
    output_path: Path | str | None = None,
) -> Path:
    """Build portable single-file `dist/agentic-dev.pyz` using Python's stdlib `zipapp`."""
    root = Path(root_dir).resolve()
    scripts_src = root / "scripts"
    if not scripts_src.is_dir():
        raise FileNotFoundError(f"Scripts directory not found: {scripts_src}")

    out_path = (
        Path(output_path).resolve()
        if output_path is not None
        else (root / DEFAULT_DIST_DIR / ZIPAPP_FILENAME).resolve()
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        stage_dir = Path(tmp)
        (stage_dir / "__main__.py").write_text(ZIPAPP_MAIN_CONTENT, encoding="utf-8")

        stage_scripts = stage_dir / "scripts"
        stage_scripts.mkdir(parents=True, exist_ok=True)
        for py_file in sorted(scripts_src.glob("*.py")):
            shutil.copy2(py_file, stage_scripts / py_file.name)

        env_example = root / ".env.example"
        if env_example.is_file():
            shutil.copy2(env_example, stage_dir / ".env.example")

        zipapp.create_archive(
            source=stage_dir,
            target=out_path,
            interpreter="/usr/bin/env python3",
            compressed=True,
        )

    _set_executable(out_path)
    return out_path


def _find_pyinstaller_cmd(root_dir: Path) -> list[str] | None:
    """Locate PyInstaller executable or module runner."""
    venv_pyinstaller = root_dir / ".venv" / "bin" / "pyinstaller"
    if venv_pyinstaller.is_file() and os.access(venv_pyinstaller, os.X_OK):
        return [str(venv_pyinstaller)]

    which_pyinstaller = shutil.which("pyinstaller")
    if which_pyinstaller:
        return [which_pyinstaller]

    # Check if current python interpreter has PyInstaller installed
    res = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode == 0:
        return [sys.executable, "-m", "PyInstaller"]

    return None


def build_native_binary(
    root_dir: Path | str = ".",
    output_dir: Path | str | None = None,
) -> Path | None:
    """Build standalone native `--onefile` binary `dist/agentic-dev` using PyInstaller."""
    root = Path(root_dir).resolve()
    dist_dir = (
        Path(output_dir).resolve()
        if output_dir is not None
        else (root / DEFAULT_DIST_DIR).resolve()
    )
    dist_dir.mkdir(parents=True, exist_ok=True)

    pyinstaller_cmd = _find_pyinstaller_cmd(root)
    if pyinstaller_cmd is None:
        return None

    scripts_src = root / "scripts"
    cli_entrypoint = scripts_src / "cli.py"
    if not cli_entrypoint.is_file():
        raise FileNotFoundError(f"CLI entrypoint not found: {cli_entrypoint}")

    hidden_imports: list[str] = []
    for py_file in sorted(scripts_src.glob("*.py")):
        mod_name = py_file.stem
        if mod_name == "__init__":
            continue
        hidden_imports.extend(["--hidden-import", f"scripts.{mod_name}"])
        hidden_imports.extend(["--hidden-import", mod_name])

    data_sep = ";" if os.name == "nt" else ":"
    add_data_args: list[str] = []
    env_example = root / ".env.example"
    if env_example.is_file():
        add_data_args.extend(["--add-data", f"{env_example}{data_sep}."])

    with tempfile.TemporaryDirectory() as build_tmp:
        work_path = Path(build_tmp) / "work"
        spec_path = Path(build_tmp) / "spec"
        cmd = [
            *pyinstaller_cmd,
            "--onefile",
            "--clean",
            "--noconfirm",
            "--name",
            "agentic-dev",
            "--distpath",
            str(dist_dir),
            "--workpath",
            str(work_path),
            "--specpath",
            str(spec_path),
            "--paths",
            str(root),
            "--paths",
            str(scripts_src),
            *hidden_imports,
            *add_data_args,
            str(cli_entrypoint),
        ]
        subprocess.run(cmd, cwd=root, check=True)

    binary_path = dist_dir / BINARY_FILENAME
    if binary_path.is_file():
        _set_executable(binary_path)
        return binary_path
    return None


def install_local_binary(
    artifact_path: Path | str,
    install_dir: Path | str | None = None,
) -> Path:
    """Install the built `agentic-dev` binary (or `.pyz`) to `~/.local/bin/agentic-dev`."""
    src = Path(artifact_path).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Built artifact not found: {src}")

    target_dir = (
        Path(install_dir).resolve()
        if install_dir is not None
        else (Path.home() / ".local" / "bin")
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / BINARY_FILENAME
    shutil.copy2(src, dest)
    _set_executable(dest)
    return dest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build standalone `agentic-dev` binary and `.pyz` zipapp",
    )
    parser.add_argument(
        "--root-dir",
        default=".",
        help="Repository root directory (default: .)",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_DIST_DIR,
        help=f"Output directory for built artifacts (default: {DEFAULT_DIST_DIR})",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--zipapp-only",
        "--pyz-only",
        dest="zipapp_only",
        action="store_true",
        help="Build only `dist/agentic-dev.pyz` (skip PyInstaller native binary)",
    )
    mode_group.add_argument(
        "--native-only",
        action="store_true",
        help="Build only `dist/agentic-dev` via PyInstaller (skip .pyz zipapp)",
    )
    parser.add_argument(
        "--install-local",
        action="store_true",
        help="Install built `agentic-dev` executable to ~/.local/bin/agentic-dev",
    )
    parser.add_argument(
        "--install-dir",
        default=None,
        help="Custom installation directory for --install-local (default: ~/.local/bin)",
    )

    args = parser.parse_args(argv)
    root = Path(args.root_dir).resolve()
    out_dir = Path(args.output_dir)
    if not out_dir.is_absolute():
        out_dir = (root / out_dir).resolve()

    primary_artifact: Path | None = None

    # 1. Build stdlib zipapp (`dist/agentic-dev.pyz`) unless --native-only
    if not args.native_only:
        pyz_path = build_zipapp(root_dir=root, output_path=out_dir / ZIPAPP_FILENAME)
        pyz_kb = pyz_path.stat().st_size / 1024
        print(f"✅ Built portable zipapp -> {pyz_path} ({pyz_kb:.1f} KB)")
        primary_artifact = pyz_path

    # 2. Build standalone native binary via PyInstaller unless --zipapp-only
    if not args.zipapp_only:
        native_path = build_native_binary(root_dir=root, output_dir=out_dir)
        if native_path is not None and native_path.is_file():
            native_mb = native_path.stat().st_size / (1024 * 1024)
            print(f"✅ Built standalone native binary -> {native_path} ({native_mb:.1f} MB)")
            primary_artifact = native_path
        elif primary_artifact is not None:
            # Copy .pyz to dist/agentic-dev as executable fallback when PyInstaller is unavailable
            fallback_bin = out_dir / BINARY_FILENAME
            shutil.copy2(primary_artifact, fallback_bin)
            _set_executable(fallback_bin)
            print(
                f"ℹ️  PyInstaller not found; copied executable zipapp to {fallback_bin}"
            )
            primary_artifact = fallback_bin
        else:
            print("❌ PyInstaller not found; cannot build --native-only binary.", file=sys.stderr)
            return 1

    if args.install_local and primary_artifact is not None:
        installed = install_local_binary(primary_artifact, install_dir=args.install_dir)
        print(f"🚀 Installed `agentic-dev` locally -> {installed}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
