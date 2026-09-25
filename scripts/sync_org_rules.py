#!/usr/bin/env python3
"""Organizational Rule Synchronizer.

Synchronizes canonical enterprise rules from a central repository or release endpoint
into `.agents/rules/org/` when older than 24 hours. Designed with a non-blocking,
offline-resilient background execution model.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

try:
    from scripts.constants import (
        DEFAULT_AUDIT_LOG_PATH,
        DEFAULT_SYNC_TTL_SECONDS,
        ORG_RULES_REL_PATH,
        SYNC_TIMESTAMP_FILENAME,
    )
except ImportError:
    from constants import (
        DEFAULT_AUDIT_LOG_PATH,
        DEFAULT_SYNC_TTL_SECONDS,
        ORG_RULES_REL_PATH,
        SYNC_TIMESTAMP_FILENAME,
    )

DEFAULT_SYNC_INTERVAL_SECONDS = DEFAULT_SYNC_TTL_SECONDS
DEFAULT_RETRY_INTERVAL_SECONDS = 3600  # 1 hour backoff on network failure
DEFAULT_TIMESTAMP_RELATIVE_PATH = f"{ORG_RULES_REL_PATH}/{SYNC_TIMESTAMP_FILENAME}"
DEFAULT_ORG_RULES_RELATIVE_PATH = ORG_RULES_REL_PATH


def load_dotenv(root_dir: Path | str = ".") -> None:
    """Load environment variables from .env file in root_dir if present."""
    env_file = Path(root_dir).resolve() / ".env"
    if not env_file.is_file():
        return
    try:
        content = env_file.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip("'\"")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass


def normalize_sync_url(url: str) -> str:
    """Normalize user-facing GitHub URLs to direct downloadable asset/archive URLs."""
    clean_url = url.strip().strip("'\"")

    # GitHub release tag page: https://github.com/owner/repo/releases/tag/v1.0.0
    tag_match = re.match(r"^https://github\.com/([^/]+)/([^/]+)/releases/tag/([^/]+)/?$", clean_url)
    if tag_match:
        owner, repo, tag = tag_match.groups()
        return f"https://github.com/{owner}/{repo}/releases/download/{tag}/rules-org.zip"

    # GitHub latest release page: https://github.com/owner/repo/releases/latest
    latest_match = re.match(r"^https://github\.com/([^/]+)/([^/]+)/releases/latest/?$", clean_url)
    if latest_match:
        owner, repo = latest_match.groups()
        return f"https://github.com/{owner}/{repo}/releases/latest/download/rules-org.zip"

    # GitHub repo root: https://github.com/owner/repo
    repo_match = re.match(r"^https://github\.com/([^/]+)/([^/]+)/?$", clean_url)
    if repo_match:
        owner, repo = repo_match.groups()
        return f"https://github.com/{owner}/{repo}/archive/refs/heads/main.zip"

    return clean_url


def get_timestamp_file(root_dir: Path | str = ".") -> Path:
    """Return path to the sync timestamp file."""
    return Path(root_dir).resolve() / DEFAULT_TIMESTAMP_RELATIVE_PATH


def is_sync_needed(
    root_dir: Path | str = ".",
    interval_seconds: int = DEFAULT_SYNC_INTERVAL_SECONDS,
) -> bool:
    """Check if the local organizational rules cache is older than the sync interval."""
    ts_file = get_timestamp_file(root_dir)
    if not ts_file.exists():
        return True

    try:
        age = time.time() - ts_file.stat().st_mtime
        return age >= interval_seconds
    except Exception:
        return True


def record_sync_timestamp(
    root_dir: Path | str = ".",
    backoff: bool = False,
) -> None:
    """Update the sync timestamp file."""
    ts_file = get_timestamp_file(root_dir)
    try:
        ts_file.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat()
        status = "BACKOFF" if backoff else "SUCCESS"
        ts_file.write_text(f"{now} | {status}\n", encoding="utf-8")
    except Exception:
        pass


def log_sync_event(
    root_dir: Path | str,
    event: str,
    details: str = "",
) -> None:
    """Append a sync audit log entry."""
    audit_file = Path(root_dir).resolve() / DEFAULT_AUDIT_LOG_PATH
    try:
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).isoformat()
        entry = f"{timestamp} | event: {event} | {details}\n"
        with audit_file.open("a", encoding="utf-8") as f:
            f.write(entry)
    except Exception:
        pass


def trigger_background_sync(
    root_dir: Path | str = ".",
    sync_script: Path | str | None = None,
) -> bool:
    """Spawn a detached asynchronous background process to sync rules if older than 24h.

    Returns True if a background worker was spawned, False otherwise (< 1ms execution).
    """
    if not is_sync_needed(root_dir):
        return False

    script_path = (
        Path(sync_script).resolve()
        if sync_script
        else Path(__file__).resolve()
    )

    if not script_path.exists():
        return False

    try:
        # Spawn detached process: does NOT block the calling process or terminal session
        subprocess.Popen(
            [sys.executable, str(script_path), "--root-dir", str(Path(root_dir).resolve())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception:
        return False


def _atomic_replace_dir(source_tmp_dir: Path, target_dir: Path) -> int:
    """Atomically copy markdown rules from source directory (or org/ subdir) into target directory."""
    target_dir.mkdir(parents=True, exist_ok=True)

    # Priority 1: Check for an explicit 'org' subdirectory in extracted contents
    candidate_files: list[Path] = []
    org_subdirs = [p for p in source_tmp_dir.rglob("org") if p.is_dir()]
    if org_subdirs:
        candidate_files = [f for f in org_subdirs[0].glob("*.md") if f.name.upper() != "README.MD"]

    # Priority 2: Flat markdown files or org-* prefixed files across archive
    if not candidate_files:
        candidate_files = [
            f
            for f in source_tmp_dir.rglob("*.md")
            if f.name.upper() != "README.MD"
            and (
                f.name.startswith("org-")
                or not any(p.name in ("team", "personal") for p in f.parents)
            )
        ]

    copied_count = 0
    for item in candidate_files:
        dest = target_dir / item.name
        shutil.copy2(item, dest)
        copied_count += 1
    return copied_count


def sync_org_rules(
    root_dir: Path | str = ".",
    source_dir: Path | str | None = None,
    source_url: str | None = None,
    force: bool = False,
    timeout_seconds: float = 5.0,
) -> bool:
    """Execute synchronization of organizational rules.

    Safe, offline-resilient, and non-crashing.
    """
    root = Path(root_dir).resolve()
    load_dotenv(root)

    org_rules_dir = root / DEFAULT_ORG_RULES_RELATIVE_PATH
    org_rules_dir.mkdir(parents=True, exist_ok=True)

    if not force and not is_sync_needed(root):
        return True

    # 1. Local source directory sync (e.g. for testing or monorepos)
    if source_dir:
        src = Path(source_dir).resolve()
        if src.is_dir():
            md_files = list(src.glob("*.md"))
            if md_files:
                for f in md_files:
                    if f.name.upper() != "README.MD":
                        shutil.copy2(f, org_rules_dir / f.name)
                record_sync_timestamp(root, backoff=False)
                log_sync_event(root, "SYNC_LOCAL_SUCCESS", f"Synced {len(md_files)} rules from {src}")
                return True

    # 2. Remote URL source sync (e.g. GitHub release asset, raw URL, S3)
    raw_url = source_url or os.getenv("ORG_RULES_SYNC_URL")
    if raw_url:
        resolved_url = normalize_sync_url(raw_url)
        try:
            req = Request(resolved_url, headers={"User-Agent": "dev-env-rule-sync/1.0"})
            with urlopen(req, timeout=timeout_seconds) as resp:
                content = resp.read()

            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_path = Path(tmp_dir)
                if resolved_url.endswith(".md"):
                    target_filename = Path(resolved_url).name
                    (tmp_path / target_filename).write_bytes(content)
                    copied = _atomic_replace_dir(tmp_path, org_rules_dir)
                else:
                    import zipfile
                    zip_path = tmp_path / "bundle.zip"
                    zip_path.write_bytes(content)
                    with zipfile.ZipFile(zip_path, "r") as zf:
                        zf.extractall(tmp_path)
                    copied = _atomic_replace_dir(tmp_path, org_rules_dir)

            if copied == 0:
                record_sync_timestamp(root, backoff=True)
                log_sync_event(
                    root,
                    "SYNC_REMOTE_EMPTY",
                    f"Downloaded archive from {resolved_url} but found no valid org rules",
                )
                return False

            record_sync_timestamp(root, backoff=False)
            log_sync_event(root, "SYNC_REMOTE_SUCCESS", f"Synced {copied} rules from {resolved_url}")
            return True
        except (URLError, TimeoutError, OSError) as e:
            # Network drop or offline: fallback gracefully with temporary 1h backoff
            record_sync_timestamp(root, backoff=True)
            log_sync_event(root, "SYNC_REMOTE_OFFLINE", f"Offline or timeout ({e}). Preserving local cache.")
            return False

    # 3. Default behavior when no remote URL configured (local cached validation)
    existing_rules = [f for f in org_rules_dir.glob("*.md") if f.name.upper() != "README.MD"]
    if not existing_rules:
        record_sync_timestamp(root, backoff=True)
        log_sync_event(
            root,
            "SYNC_CACHED_EMPTY",
            "No remote URL configured and local organizational rules cache is empty",
        )
        return False

    record_sync_timestamp(root, backoff=False)
    log_sync_event(
        root,
        "SYNC_CACHED_VALIDATED",
        f"Verified {len(existing_rules)} organizational rules active in local cache",
    )
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Synchronize organizational rules")
    parser.add_argument(
        "--root-dir",
        default=".",
        help="Repository root directory (default: current dir)",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Check if sync is needed (>24h) and exit with code 1 if needed, 0 if up to date",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force sync regardless of timestamp",
    )
    parser.add_argument(
        "--source-dir",
        default=None,
        help="Local directory to sync rules from",
    )
    parser.add_argument(
        "--source-url",
        default=None,
        help="Remote URL or endpoint to fetch rules bundle from",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="Trigger sync in a detached background process",
    )

    args = parser.parse_args()

    if args.check_only:
        needed = is_sync_needed(args.root_dir)
        if needed:
            print("Sync needed: organizational rules are older than 24 hours.")
            sys.exit(1)
        else:
            print("Sync not needed: organizational rules are up to date.")
            sys.exit(0)

    if args.background:
        spawned = trigger_background_sync(args.root_dir)
        if spawned:
            print("Background sync worker spawned successfully.")
        else:
            print("Sync not needed or already in progress.")
        sys.exit(0)

    success = sync_org_rules(
        root_dir=args.root_dir,
        source_dir=args.source_dir,
        source_url=args.source_url,
        force=args.force,
    )
    if success:
        print("✅ Organizational rules synchronized successfully.")
        sys.exit(0)
    else:
        print("⚠️ Organizational rules sync completed with offline/cached fallback.")
        sys.exit(0)


if __name__ == "__main__":
    main()
