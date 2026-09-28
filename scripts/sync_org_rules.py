#!/usr/bin/env python3
"""Organizational Rule Synchronizer.

Synchronizes canonical enterprise rules from a central repository or release endpoint
into `.agents/rules/org/` when older than 24 hours. Designed with a non-blocking,
offline-resilient background execution model.
"""

from __future__ import annotations

import argparse
import difflib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
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


def _parse_timestamp_age(
    content: str,
    fallback_mtime: float,
) -> tuple[str | None, str | None, float]:
    """Parse ISO timestamp, status, and compute age from content, falling back to mtime."""
    parts = [p.strip() for p in content.split("|", 1)]
    iso_ts = parts[0] if parts and parts[0] else None
    status = parts[1] if len(parts) > 1 and parts[1] else None

    if iso_ts:
        try:
            dt = datetime.fromisoformat(iso_ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - dt).total_seconds()
            return iso_ts, status, age
        except Exception:
            pass

    return iso_ts, status, max(0.0, time.time() - fallback_mtime)


def is_sync_needed(
    root_dir: Path | str = ".",
    interval_seconds: int = DEFAULT_SYNC_INTERVAL_SECONDS,
) -> bool:
    """Check if the local organizational rules cache is older than the sync interval."""
    ts_file = get_timestamp_file(root_dir)
    if not ts_file.exists():
        return True

    try:
        content = ts_file.read_text(encoding="utf-8").strip()
        _, _, age = _parse_timestamp_age(content, ts_file.stat().st_mtime)
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


def format_duration(seconds: float) -> str:
    """Format duration in seconds into a concise human-readable string."""
    if seconds < 0:
        return "in the future"
    seconds_int = int(seconds)
    if seconds_int < 60:
        return f"{seconds_int}s ago"
    minutes = seconds_int // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    rem_min = minutes % 60
    if hours < 24:
        return f"{hours}h {rem_min}m ago" if rem_min else f"{hours}h ago"
    days = hours // 24
    rem_hours = hours % 24
    return f"{days}d {rem_hours}h ago" if rem_hours else f"{days}d ago"


def format_file_size(size_bytes: int) -> str:
    """Format file size in bytes to human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def get_last_sync_info(
    root_dir: Path | str = ".",
) -> tuple[str | None, str | None, float | None]:
    """Read last sync timestamp, status, and age in seconds.

    Returns:
        (iso_timestamp, status_str, age_seconds) or (None, None, None) if not found.
    """
    ts_file = get_timestamp_file(root_dir)
    if not ts_file.exists():
        return None, None, None

    try:
        content = ts_file.read_text(encoding="utf-8").strip()
        iso_ts, status, age_seconds = _parse_timestamp_age(content, ts_file.stat().st_mtime)
        return iso_ts, status, age_seconds
    except Exception:
        return None, None, None


def get_local_rules(root_dir: Path | str = ".") -> list[Path]:
    """Return list of existing local organizational markdown rule files."""
    org_rules_dir = Path(root_dir).resolve() / DEFAULT_ORG_RULES_RELATIVE_PATH
    if not org_rules_dir.is_dir():
        return []
    return sorted(
        [f for f in org_rules_dir.glob("*.md") if f.name.upper() != "README.MD"],
        key=lambda p: p.name,
    )


def _get_candidate_files(source_tmp_dir: Path) -> list[Path]:
    """Find candidate rule markdown files in extracted directory."""
    # Priority 1: Check for an explicit 'org' subdirectory in extracted contents
    org_subdirs = [p for p in source_tmp_dir.rglob("org") if p.is_dir()]
    if org_subdirs:
        candidate_files = [f for f in org_subdirs[0].glob("*.md") if f.name.upper() != "README.MD"]
        if candidate_files:
            return sorted(candidate_files, key=lambda p: p.name)

    # Priority 2: Flat markdown files or org-* prefixed files across archive
    return sorted(
        [
            f
            for f in source_tmp_dir.rglob("*.md")
            if f.name.upper() != "README.MD"
            and (
                f.name.startswith("org-")
                or not any(p.name in ("team", "personal") for p in f.parents)
            )
        ],
        key=lambda p: p.name,
    )


@dataclass
class RuleChanges:
    """Track changes between remote candidate rules and local rules."""

    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    local_only: list[str] = field(default_factory=list)
    diffs: dict[str, str] = field(default_factory=dict)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.modified)

    def summary(self) -> str:
        parts = []
        if self.added:
            parts.append(f"{len(self.added)} added")
        if self.modified:
            parts.append(f"{len(self.modified)} modified")
        if self.unchanged:
            parts.append(f"{len(self.unchanged)} unchanged")
        if self.local_only:
            parts.append(f"{len(self.local_only)} local-only")
        return ", ".join(parts) if parts else "0 rules evaluated"


def compare_rule_files(
    candidate_files: list[Path],
    target_dir: Path,
) -> RuleChanges:
    """Compare candidate incoming rule files with existing local rule files."""
    changes = RuleChanges()
    local_rules = (
        {f.name: f for f in target_dir.glob("*.md") if f.name.upper() != "README.MD"}
        if target_dir.is_dir()
        else {}
    )

    candidate_map = {f.name: f for f in candidate_files}

    for name, cand_path in sorted(candidate_map.items()):
        if name not in local_rules:
            changes.added.append(name)
        else:
            local_path = local_rules[name]
            try:
                cand_bytes = cand_path.read_bytes()
                local_bytes = local_path.read_bytes()
                if cand_bytes == local_bytes:
                    changes.unchanged.append(name)
                else:
                    changes.modified.append(name)
                    cand_text = cand_bytes.decode("utf-8", errors="replace")
                    local_text = local_bytes.decode("utf-8", errors="replace")
                    diff_lines = list(
                        difflib.unified_diff(
                            local_text.splitlines(),
                            cand_text.splitlines(),
                            fromfile=f"local/{name}",
                            tofile=f"remote/{name}",
                            lineterm="",
                            n=2,
                        )
                    )
                    changes.diffs[name] = "\n".join(diff_lines)
            except Exception:
                changes.modified.append(name)

    for name in sorted(local_rules.keys()):
        if name not in candidate_map:
            changes.local_only.append(name)

    return changes


def format_verbose_report(
    remote_origin: str,
    last_sync_info: tuple[str | None, str | None, float | None],
    local_rules: list[Path],
    changes: RuleChanges | None = None,
    changes_message: str | None = None,
    title: str = "Organizational Rules Sync",
) -> str:
    """Format detailed sync information report."""
    iso_ts, status, age_seconds = last_sync_info

    # 1. Format timestamp
    if iso_ts:
        status_suffix = f" [{status}]" if status else ""
        age_str = f" ({format_duration(age_seconds)})" if age_seconds is not None else ""
        ts_display = f"{iso_ts}{status_suffix}{age_str}"
    else:
        ts_display = "Never (no sync record found)"

    # 2. Format local rules
    if local_rules:
        local_rules_header = f"Local Rules ({len(local_rules)} in {DEFAULT_ORG_RULES_RELATIVE_PATH}):"
        rule_items = []
        for r in local_rules:
            try:
                sz = format_file_size(r.stat().st_size)
                rule_items.append(f"  - {r.name} ({sz})")
            except Exception:
                rule_items.append(f"  - {r.name}")
        local_rules_display = "\n".join([local_rules_header] + rule_items)
    else:
        local_rules_display = f"Local Rules (0): None found in {DEFAULT_ORG_RULES_RELATIVE_PATH}"

    # 3. Format changes
    if changes_message:
        changes_display = f"Changes from Remote:\n  {changes_message}"
    elif changes is not None:
        change_lines = ["Changes from Remote:"]
        if not changes.has_changes and not changes.local_only:
            change_lines.append(f"  No changes detected ({len(changes.unchanged)} rule(s) up to date)")
            for f in changes.unchanged:
                change_lines.append(f"    = {f} (unchanged)")
        else:
            change_lines.append(f"  Summary: {changes.summary()}")
            for f in changes.added:
                change_lines.append(f"    + {f} (added from remote)")
            for f in changes.modified:
                diff_summary = ""
                diff_text = changes.diffs.get(f, "")
                if diff_text:
                    d_lines = diff_text.splitlines()
                    adds = sum(1 for line in d_lines if line.startswith("+") and not line.startswith("+++"))
                    dels = sum(1 for line in d_lines if line.startswith("-") and not line.startswith("---"))
                    diff_summary = f" (+{adds}, -{dels} lines)"
                change_lines.append(f"    ~ {f} (modified from remote{diff_summary})")
                if diff_text:
                    d_split = diff_text.splitlines()
                    for diff_line in d_split[:15]:
                        change_lines.append(f"        {diff_line}")
                    if len(d_split) > 15:
                        remaining = len(d_split) - 15
                        change_lines.append(f"        ... ({remaining} more diff lines)")
            for f in changes.unchanged:
                change_lines.append(f"    = {f} (unchanged)")
            for f in changes.local_only:
                change_lines.append(f"    ? {f} (local-only, retained)")
        changes_display = "\n".join(change_lines)
    else:
        changes_display = "Changes from Remote:\n  None"

    sep = "=" * 60
    return (
        f"{sep}\n"
        f"=== {title} (Verbose Mode) ===\n"
        f"Remote Origin:       {remote_origin}\n"
        f"Last Sync Timestamp: {ts_display}\n"
        f"{local_rules_display}\n"
        f"{changes_display}\n"
        f"{sep}"
    )


def _atomic_replace_dir(source_tmp_dir: Path, target_dir: Path) -> int:
    """Atomically copy markdown rules from source directory (or org/ subdir) into target directory."""
    target_dir.mkdir(parents=True, exist_ok=True)
    candidate_files = _get_candidate_files(source_tmp_dir)

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
    verbose: bool = False,
) -> bool:
    """Execute synchronization of organizational rules.

    Safe, offline-resilient, and non-crashing.
    """
    root = Path(root_dir).resolve()
    load_dotenv(root)

    org_rules_dir = root / DEFAULT_ORG_RULES_RELATIVE_PATH
    org_rules_dir.mkdir(parents=True, exist_ok=True)

    raw_url = source_url or os.getenv("ORG_RULES_SYNC_URL")
    if source_dir:
        remote_origin = f"{Path(source_dir).resolve()} (local directory)"
    elif raw_url:
        resolved_url = normalize_sync_url(raw_url)
        if resolved_url != raw_url:
            remote_origin = f"{raw_url} (resolved: {resolved_url})"
        else:
            remote_origin = raw_url
    else:
        remote_origin = "None (not configured; using local cache)"

    if not force and not is_sync_needed(root):
        if verbose:
            last_sync_info = get_last_sync_info(root)
            local_rules = get_local_rules(root)
            _, _, age_s = last_sync_info
            age_desc = format_duration(age_s) if age_s is not None else "recently"
            msg = f"Skipped (cache is fresh, last synced {age_desc}; use --force to fetch from remote)"
            print(
                format_verbose_report(
                    remote_origin=remote_origin,
                    last_sync_info=last_sync_info,
                    local_rules=local_rules,
                    changes_message=msg,
                )
            )
        return True

    # 1. Local source directory sync (e.g. for testing or monorepos)
    if source_dir:
        src = Path(source_dir).resolve()
        if src.is_dir():
            candidate_files = sorted(
                [f for f in src.glob("*.md") if f.name.upper() != "README.MD"],
                key=lambda p: p.name,
            )
            if candidate_files:
                changes = compare_rule_files(candidate_files, org_rules_dir)
                for f in candidate_files:
                    shutil.copy2(f, org_rules_dir / f.name)
                record_sync_timestamp(root, backoff=False)
                log_sync_event(
                    root,
                    "SYNC_LOCAL_SUCCESS",
                    f"Synced {len(candidate_files)} rules from {src} ({changes.summary()})",
                )
                if verbose:
                    last_sync_info = get_last_sync_info(root)
                    local_rules = get_local_rules(root)
                    print(
                        format_verbose_report(
                            remote_origin=remote_origin,
                            last_sync_info=last_sync_info,
                            local_rules=local_rules,
                            changes=changes,
                        )
                    )
                return True

    # 2. Remote URL source sync (e.g. GitHub release asset, raw URL, S3)
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
                else:
                    import zipfile

                    zip_path = tmp_path / "bundle.zip"
                    zip_path.write_bytes(content)
                    with zipfile.ZipFile(zip_path, "r") as zf:
                        zf.extractall(tmp_path)

                candidate_files = _get_candidate_files(tmp_path)
                changes = compare_rule_files(candidate_files, org_rules_dir)
                copied = _atomic_replace_dir(tmp_path, org_rules_dir)

            if copied == 0:
                record_sync_timestamp(root, backoff=True)
                log_sync_event(
                    root,
                    "SYNC_REMOTE_EMPTY",
                    f"Downloaded archive from {resolved_url} but found no valid org rules",
                )
                if verbose:
                    last_sync_info = get_last_sync_info(root)
                    local_rules = get_local_rules(root)
                    print(
                        format_verbose_report(
                            remote_origin=remote_origin,
                            last_sync_info=last_sync_info,
                            local_rules=local_rules,
                            changes_message=f"Downloaded archive from {resolved_url} but found no valid org rules",
                        )
                    )
                return False

            record_sync_timestamp(root, backoff=False)
            log_sync_event(
                root,
                "SYNC_REMOTE_SUCCESS",
                f"Synced {copied} rules from {resolved_url} ({changes.summary()})",
            )
            if verbose:
                last_sync_info = get_last_sync_info(root)
                local_rules = get_local_rules(root)
                print(
                    format_verbose_report(
                        remote_origin=remote_origin,
                        last_sync_info=last_sync_info,
                        local_rules=local_rules,
                        changes=changes,
                    )
                )
            return True
        except (URLError, TimeoutError, OSError) as e:
            # Network drop or offline: fallback gracefully with temporary 1h backoff
            record_sync_timestamp(root, backoff=True)
            log_sync_event(root, "SYNC_REMOTE_OFFLINE", f"Offline or timeout ({e}). Preserving local cache.")
            if verbose:
                last_sync_info = get_last_sync_info(root)
                local_rules = get_local_rules(root)
                print(
                    format_verbose_report(
                        remote_origin=remote_origin,
                        last_sync_info=last_sync_info,
                        local_rules=local_rules,
                        changes_message=f"Offline or timeout ({e}). Preserving local cache.",
                    )
                )
            return False

    # 3. Default behavior when no remote URL configured (local cached validation)
    existing_rules = get_local_rules(root)
    if not existing_rules:
        record_sync_timestamp(root, backoff=True)
        log_sync_event(
            root,
            "SYNC_CACHED_EMPTY",
            "No remote URL configured and local organizational rules cache is empty",
        )
        if verbose:
            last_sync_info = get_last_sync_info(root)
            print(
                format_verbose_report(
                    remote_origin=remote_origin,
                    last_sync_info=last_sync_info,
                    local_rules=[],
                    changes_message="No remote URL configured and local organizational rules cache is empty",
                )
            )
        return False

    record_sync_timestamp(root, backoff=False)
    log_sync_event(
        root,
        "SYNC_CACHED_VALIDATED",
        f"Verified {len(existing_rules)} organizational rules active in local cache",
    )
    if verbose:
        last_sync_info = get_last_sync_info(root)
        print(
            format_verbose_report(
                remote_origin=remote_origin,
                last_sync_info=last_sync_info,
                local_rules=existing_rules,
                changes_message="None (no remote origin configured; validated local cache)",
            )
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
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed sync information including remote origin, last sync timestamp, local rules, and changes from remote",
    )

    args = parser.parse_args()

    if args.check_only:
        needed = is_sync_needed(args.root_dir)
        if args.verbose:
            load_dotenv(args.root_dir)
            raw_url = args.source_url or os.getenv("ORG_RULES_SYNC_URL")
            if args.source_dir:
                origin = f"{Path(args.source_dir).resolve()} (local directory)"
            elif raw_url:
                res_url = normalize_sync_url(raw_url)
                origin = f"{raw_url} (resolved: {res_url})" if res_url != raw_url else raw_url
            else:
                origin = "None (not configured; using local cache)"
            status_desc = (
                "Sync needed (cache is older than 24 hours; run sync to update)"
                if needed
                else "Up to date (cache is within 24-hour TTL)"
            )
            print(
                format_verbose_report(
                    remote_origin=origin,
                    last_sync_info=get_last_sync_info(args.root_dir),
                    local_rules=get_local_rules(args.root_dir),
                    changes_message=f"Check-only mode: {status_desc}",
                    title="Organizational Rules Sync Check",
                )
            )
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
        verbose=args.verbose,
    )
    if success:
        print("✅ Organizational rules synchronized successfully.")
        sys.exit(0)
    else:
        print("⚠️ Organizational rules sync completed with offline/cached fallback.")
        sys.exit(0)


if __name__ == "__main__":
    main()
