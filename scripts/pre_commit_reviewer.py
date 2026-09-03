#!/usr/bin/env python3
"""Pre-commit best-practices review CLI engine."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Add script directory to sys.path for local module resolution
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from gemini_client import (
    GeminiClientConfig,
    GeminiReviewClient,
    ReviewResult,
    ReviewViolation,
)
from rule_loader import Rule, load_rules, match_rules_for_files

# ANSI Terminal Colors
BOLD = "\033[1m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"


def is_git_rebasing() -> bool:
    """Check if Git is currently in an interactive rebase or cherry-pick."""
    reflog_action = os.getenv("GIT_REFLOG_ACTION", "").lower()
    if "rebase" in reflog_action or "cherry-pick" in reflog_action:
        return True

    git_dir = Path(".git")
    if (
        (git_dir / "rebase-merge").exists()
        or (git_dir / "rebase-apply").exists()
        or (git_dir / "CHERRY_PICK_HEAD").exists()
        or (git_dir / "MERGE_HEAD").exists()
    ):
        return True

    return False


def get_staged_files() -> list[str]:
    """Retrieve list of staged files."""
    try:
        res = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            capture_output=True,
            text=True,
            check=True,
        )
        return [f.strip() for f in res.stdout.splitlines() if f.strip()]
    except Exception:
        return []


def get_staged_diff() -> str:
    """Retrieve unified diff of staged changes."""
    try:
        res = subprocess.run(
            ["git", "diff", "--cached", "--unified=3"],
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout
    except Exception:
        return ""


def run_local_static_fallback() -> bool:
    """Run local deterministic static checks if tool is available."""
    if shutil.which("ruff"):
        try:
            print(f"{CYAN}Running local static check (ruff)...{RESET}")
            res = subprocess.run(["ruff", "check", "."], check=False)
            return res.returncode == 0
        except Exception:
            pass
    return True


def format_violation(v: ReviewViolation) -> str:
    """Format a single violation for terminal display."""
    color = RED if v.severity in ("CRITICAL", "ERROR") else YELLOW
    loc = f"{v.file}:{v.line_number}" if v.line_number else v.file
    badge = f"{color}[{v.severity}]{RESET}"
    rule = f"{BOLD}{v.rule_title}{RESET}" if v.rule_title else v.rule_id

    out = [f"  {badge} {BOLD}{loc}{RESET} — {rule}"]
    out.append(f"     Issue: {v.issue}")
    if v.suggested_fix:
        out.append(f"     Fix:   {v.suggested_fix}")
    if v.suggested_code_diff:
        out.append(f"     Diff:\n{v.suggested_code_diff}")
    return "\n".join(out)


def print_review_report(result: ReviewResult) -> None:
    """Render structured diagnostic output."""
    print(f"\n{BOLD}{CYAN}=== Pre-Commit LLM Best-Practices Review ==={RESET}")

    if result.degraded:
        print(f"{YELLOW}[NOTICE: {result.summary}]{RESET}")
        return

    if result.cached:
        print(f"{CYAN}(Diff evaluated from cache){RESET}")

    critical_or_error = [
        v for v in result.violations if v.severity in ("CRITICAL", "ERROR")
    ]
    warnings = [v for v in result.violations if v.severity == "WARN"]
    infos = [v for v in result.violations if v.severity == "INFO"]

    if critical_or_error:
        print(f"\n{RED}{BOLD}❌ Commit Blocked ({len(critical_or_error)} Errors / Critical Violations):{RESET}")
        for v in critical_or_error:
            print(format_violation(v))

    if warnings:
        print(f"\n{YELLOW}{BOLD}⚠️ Advisories ({len(warnings)} Warnings - Non-blocking):{RESET}")
        for v in warnings:
            print(format_violation(v))

    if infos:
        print(f"\n{CYAN}{BOLD}ℹ️ Info ({len(infos)} Notices):{RESET}")
        for v in infos:
            print(format_violation(v))

    if not result.violations and result.passed:
        print(f"\n{GREEN}✅ All best-practices rules passed!{RESET}")


def run_review(
    rules_dir: Path | str = ".agents/rules",
    skip_llm: bool = False,
    config: GeminiClientConfig | None = None,
) -> int:
    """Execute pre-commit review workflow."""
    # 1. Check bypass flag
    if skip_llm or os.getenv("SKIP_LLM_HOOK") == "1":
        print(f"{CYAN}Pre-commit review skipped via SKIP_LLM_HOOK.{RESET}")
        return 0

    # 2. Check rebase status
    if is_git_rebasing():
        print(f"{CYAN}Git rebase/cherry-pick detected. Fast-tracking pre-commit review.{RESET}")
        return 0

    # 3. Get staged files
    staged_files = get_staged_files()
    if not staged_files:
        return 0

    diff_text = get_staged_diff()
    if not diff_text.strip():
        return 0

    # 4. Load rules
    all_rules = load_rules(rules_dir)
    if not all_rules:
        # No rules configured
        return 0

    matched_rules = match_rules_for_files(all_rules, staged_files)
    if not matched_rules:
        # No applicable rules for changed files
        return 0

    rules_summary = "\n\n".join(
        f"--- Rule: {r.id} ({r.title}) [Default Severity: {r.severity_default}] ---\n{r.content}"
        for r in matched_rules
    )

    # 5. Call review client
    client = GeminiReviewClient(config=config)
    result = client.review_diff(
        diff_text=diff_text,
        rules_text=rules_summary,
        staged_files=staged_files,
    )

    # 6. Print report
    print_review_report(result)

    if result.degraded:
        run_local_static_fallback()
        return 0

    # Two-Tier Gating Policy:
    # Hard violations (CRITICAL, ERROR) exit 1.
    # WARN and INFO exit 0.
    has_hard_violations = any(
        v.severity in ("CRITICAL", "ERROR") for v in result.violations
    )

    if has_hard_violations or not result.passed:
        print(
            f"\n{RED}Please resolve the critical/error violations above, stage your changes, and re-commit.{RESET}\n"
        )
        return 1

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-commit LLM Best-Practices Reviewer")
    parser.add_argument(
        "--rules-dir",
        default=".agents/rules",
        help="Directory containing canonical markdown rules (default: .agents/rules)",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Skip LLM evaluation and allow commit",
    )
    parser.add_argument(
        "--backend",
        choices=["vertex", "google_ai"],
        default=None,
        help="Backend to use: 'vertex' (default) or 'google_ai'",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="Google Cloud Project ID (defaults to GOOGLE_CLOUD_PROJECT or ADC default)",
    )
    parser.add_argument(
        "--location",
        default=None,
        help="Google Cloud Location for Vertex AI (default: us-central1)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name (default: gemini-2.5-flash)",
    )

    args = parser.parse_args()

    cfg = GeminiClientConfig.from_env()
    if args.backend:
        cfg.backend = args.backend
    if args.project:
        cfg.project_id = args.project
    if args.location:
        cfg.location = args.location
    if args.model:
        cfg.model = args.model

    sys.exit(run_review(rules_dir=args.rules_dir, skip_llm=args.skip_llm, config=cfg))


if __name__ == "__main__":
    main()
