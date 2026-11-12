#!/usr/bin/env python3
"""Build pipeline for `agentic-dev` standalone CLI binary and portable `.pyz` zipapp.

Supports two complementary packaging tiers:
1. `dist/agentic-dev.pyz`: Ultra-lightweight stdlib zipapp (<100 KB, 0 external build deps).
2. `dist/agentic-dev`: Standalone native executable via PyInstaller (`--onedir` wrapped in a
   content-addressed self-caching single-file executable on POSIX to eliminate per-run extraction
   and macOS Gatekeeper re-scanning overhead; `--onefile` on Windows).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tarfile
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


def set_executable(path: Path) -> None:
    """Ensure a file has user/group/other executable permissions."""
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


_set_executable = set_executable


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
    venv_python = root_dir / ".venv" / "bin" / "python"
    venv_pyinstaller = root_dir / ".venv" / "bin" / "pyinstaller"
    if venv_pyinstaller.is_file():
        if venv_python.is_file() and os.access(venv_python, os.X_OK):
            return [str(venv_python), "-m", "PyInstaller"]
        if os.access(venv_pyinstaller, os.X_OK):
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


def package_cached_onedir_binary(
    bundle_dir: Path,
    output_binary: Path,
    prewarm_cache: bool = True,
) -> Path:
    """Wrap a PyInstaller `--onedir` bundle into a content-addressed self-caching single-file executable.

    Eliminates the ~4-11s per-invocation temp extraction and macOS Gatekeeper re-scanning penalty
    of PyInstaller `--onefile` by extracting once into `${XDG_CACHE_HOME:-$HOME/.cache}/agentic-dev/rt-<hash>`
    and directly `exec`-ing the persistent native binary on all runs (~130-150ms).
    """
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp_tar:
        tar_path = Path(tmp_tar.name)

    try:
        with tarfile.open(tar_path, "w:gz", compresslevel=6) as tf:
            tf.add(bundle_dir, arcname="agentic-dev")
        payload_bytes = tar_path.read_bytes()
    finally:
        tar_path.unlink(missing_ok=True)

    build_id = hashlib.sha256(payload_bytes).hexdigest()[:16]

    # Build a fixed-length header so `tail -c +<OFFSET>` is 100% binary-safe and avoids awk scanning
    header_template = (
        "#!/bin/sh\n"
        "set -e\n"
        'export AGENTIC_DEV_INVOKING_BIN="${AGENTIC_DEV_INVOKING_BIN:-$0}"\n'
        f'CACHE_ROOT="${{XDG_CACHE_HOME:-$HOME/.cache}}/agentic-dev/rt-{build_id}"\n'
        'BIN_PATH="$CACHE_ROOT/agentic-dev/agentic-dev"\n'
        'if [ ! -x "$BIN_PATH" ]; then\n'
        '    TMP_DIR="$CACHE_ROOT.tmp.$$"\n'
        '    mkdir -p "$TMP_DIR"\n'
        '    tail -c +__PAYLOAD_OFFSET__ "$0" | tar -xzf - -C "$TMP_DIR"\n'
        '    mkdir -p "$(dirname "$CACHE_ROOT")"\n'
        '    mv "$TMP_DIR" "$CACHE_ROOT" 2>/dev/null || rm -rf "$TMP_DIR"\n'
        "fi\n"
        'exec "$BIN_PATH" "$@"\n'
    )
    # Compute 1-based byte offset where payload starts (no leading zeros; BSD tail parses leading 0 as octal)
    payload_offset = len(header_template.replace("__PAYLOAD_OFFSET__", "100").encode("utf-8")) + 1
    header_bytes = header_template.replace("__PAYLOAD_OFFSET__", str(payload_offset)).encode(
        "utf-8"
    )
    if len(header_bytes) + 1 != payload_offset:
        payload_offset = len(header_bytes) + 1
        header_bytes = header_template.replace("__PAYLOAD_OFFSET__", str(payload_offset)).encode(
            "utf-8"
        )

    output_binary.parent.mkdir(parents=True, exist_ok=True)
    output_binary.write_bytes(header_bytes + payload_bytes)
    _set_executable(output_binary)

    if prewarm_cache:
        try:
            xdg_cache = os.getenv("XDG_CACHE_HOME", "").strip()
            cache_base = Path(xdg_cache) if xdg_cache else (Path.home() / ".cache")
            cache_root = cache_base / "agentic-dev" / f"rt-{build_id}"
            target_bundle = cache_root / "agentic-dev"
            if not (target_bundle / "agentic-dev").is_file():
                cache_root.parent.mkdir(parents=True, exist_ok=True)
                if cache_root.exists():
                    shutil.rmtree(cache_root, ignore_errors=True)
                shutil.copytree(bundle_dir, target_bundle, symlinks=True)
            # Warm macOS Gatekeeper / vnode cache once at build time
            subprocess.run(
                [str(output_binary), "--version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            pass

    return output_binary


def build_native_binary(
    root_dir: Path | str = ".",
    output_dir: Path | str | None = None,
) -> Path | None:
    """Build standalone native binary `dist/agentic-dev` using PyInstaller."""
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
        if mod_name in ("__init__", "build_binary"):
            continue
        hidden_imports.extend(["--hidden-import", f"scripts.{mod_name}"])
        hidden_imports.extend(["--hidden-import", mod_name])

    data_sep = ";" if os.name == "nt" else ":"
    add_data_args: list[str] = []
    env_example = root / ".env.example"
    if env_example.is_file():
        add_data_args.extend(["--add-data", f"{env_example}{data_sep}."])

    use_cached_onedir = os.name != "nt"

    with tempfile.TemporaryDirectory() as build_tmp:
        work_path = Path(build_tmp) / "work"
        spec_path = Path(build_tmp) / "spec"
        pyi_dist_path = (Path(build_tmp) / "pyi_dist") if use_cached_onedir else dist_dir
        pyi_env = os.environ.copy()
        pyi_env.setdefault("PYINSTALLER_CONFIG_DIR", str(Path(build_tmp) / "pyi_config"))

        cmd = [
            *pyinstaller_cmd,
            "--onedir" if use_cached_onedir else "--onefile",
            "--clean",
            "--noconfirm",
            "--name",
            "agentic-dev",
            "--distpath",
            str(pyi_dist_path),
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
        subprocess.run(cmd, cwd=root, env=pyi_env, check=True)

        binary_path = dist_dir / BINARY_FILENAME
        if use_cached_onedir:
            bundle_dir = pyi_dist_path / "agentic-dev"
            if (bundle_dir / BINARY_FILENAME).is_file():
                return package_cached_onedir_binary(bundle_dir, binary_path)
            return None

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
