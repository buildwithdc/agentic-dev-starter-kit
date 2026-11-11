#!/usr/bin/env python3
"""Unified CLI entrypoint (`agentic-dev`) for the Agentic Dev Governance & Validation Loop."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from scripts import __version__
    from scripts.bundled_assets import bootstrap_workspace_templates
    from scripts.constants import RULES_DIR_NAME
    from scripts.gemini_client import GeminiClientConfig, get_adc_access_token
    from scripts.install_tooling import main as install_main
    from scripts.package_tooling import main as package_main
    from scripts.pre_commit_reviewer import main as review_main
    from scripts.preference_manager import main as prefs_main
    from scripts.rule_loader import load_rules
    from scripts.sync_org_rules import (
        format_duration,
        get_last_sync_info,
        load_dotenv,
    )
    from scripts.sync_org_rules import (
        main as sync_main,
    )
except ImportError:
    from bundled_assets import bootstrap_workspace_templates
    from constants import RULES_DIR_NAME
    from gemini_client import GeminiClientConfig, get_adc_access_token
    from install_tooling import main as install_main
    from package_tooling import main as package_main
    from pre_commit_reviewer import main as review_main
    from preference_manager import main as prefs_main
    from rule_loader import load_rules
    from sync_org_rules import (
        format_duration,
        get_last_sync_info,
        load_dotenv,
    )
    from sync_org_rules import (
        main as sync_main,
    )

    __version__ = "0.2.0"


SUBCOMMANDS: tuple[str, ...] = (
    "init",
    "install",
    "review",
    "sync",
    "prefs",
    "package",
    "doctor",
    "version",
)


def run_doctor(argv: Sequence[str] | None = None) -> int:
    """Inspect local workspace readiness for the pre-commit validation loop."""
    parser = argparse.ArgumentParser(
        prog="agentic-dev doctor",
        description="Verify local Git hook, rule tiers, and LLM authentication setup",
    )
    parser.add_argument(
        "--root-dir",
        default=".",
        help="Repository root directory (default: .)",
    )
    args = parser.parse_args(argv)
    root_dir = Path(args.root_dir).resolve()
    load_dotenv(root_dir)

    print(f"=== agentic-dev doctor (v{__version__}) ===")
    print(f"Workspace:     {root_dir}")

    # 1. Git repository & pre-commit hook status
    git_dir = root_dir / ".git"
    hook_file = git_dir / "hooks" / "pre-commit"
    if not git_dir.is_dir():
        print("Git Repo:      ⚠️  Not a Git repository (.git not found)")
    elif hook_file.is_file():
        print(f"Pre-Commit:    ✅ Installed ({hook_file.relative_to(root_dir)})")
    else:
        print("Pre-Commit:    ⚠️  Not installed (run `agentic-dev init` to configure)")

    # 2. Config (.env) & Rule tiers status
    env_file = root_dir / ".env"
    sync_url = os.getenv("ORG_RULES_SYNC_URL", "").strip()
    if env_file.is_file():
        sync_note = f"ORG_RULES_SYNC_URL={sync_url}" if sync_url else "ORG_RULES_SYNC_URL not set"
        print(f"Config (.env): ✅ Found ({sync_note})")
    else:
        print(
            "Config (.env): ⚠️  Missing .env (run `cp .env.example .env` to configure ORG_RULES_SYNC_URL / GEMINI_API_KEY)"
        )

    rules_dir = root_dir / RULES_DIR_NAME
    if rules_dir.is_dir():
        all_rules = load_rules(rules_dir, include_personal=True, include_global=False)
        org_count = sum(1 for r in all_rules if r.tier == "org")
        team_count = sum(1 for r in all_rules if r.tier == "team")
        personal_count = sum(1 for r in all_rules if r.tier == "personal")
        print(
            f"Active Rules:  {len(all_rules)} total "
            f"(org={org_count}, team={team_count}, personal={personal_count})"
        )
    else:
        print(f"Active Rules:  0 ({RULES_DIR_NAME} not found)")

    iso_ts, status, age_s = get_last_sync_info(root_dir)
    if iso_ts:
        age_str = f" ({format_duration(age_s)})" if age_s is not None else ""
        print(f"Org Rule Sync: {iso_ts} [{status or 'UNKNOWN'}]{age_str}")
    else:
        print("Org Rule Sync: Never synced (configure ORG_RULES_SYNC_URL and run `agentic-dev sync`)")

    # 3. LLM backend & credentials check
    cfg = GeminiClientConfig.from_env()
    has_api_key = bool(cfg.api_key)
    has_adc = bool(get_adc_access_token(root_dir / ".git" / ".llm_cache"))
    remote_endpoint = getattr(cfg, "remote_agent_endpoint", None)
    if remote_endpoint:
        print(f"Review Mode:   Remote GCP Agent ({remote_endpoint})")
    else:
        print(f"Review Mode:   Direct Gemini API (backend={cfg.backend}, model={cfg.model})")

    if has_api_key:
        print("Auth Status:   ✅ GEMINI_API_KEY configured")
    elif has_adc:
        print("Auth Status:   ✅ Google Cloud ADC token available")
    else:
        print("Auth Status:   ⚠️  No GEMINI_API_KEY or ADC token detected (offline fallback active)")

    return 0


def _extract_root_dir_from_argv(sub_argv: Sequence[str]) -> Path:
    """Extract --root-dir from subcommand argv if present, defaulting to current directory."""
    for idx, token in enumerate(sub_argv):
        if token == "--root-dir" and idx + 1 < len(sub_argv):
            return Path(sub_argv[idx + 1]).resolve()
        if token.startswith("--root-dir="):
            return Path(token.split("=", 1)[1]).resolve()
    return Path(".").resolve()


def print_top_level_help() -> None:
    """Print top-level unified CLI usage."""
    print(
        f"""usage: agentic-dev <subcommand> [options]

Agentic Dev Governance & Local Validation Loop CLI (v{__version__})

subcommands:
  init (install)  Initialize workspace hooks, assistant skills, .env.example, and org rules
  review          Run pre-commit best-practices review (dependency + prompt + LLM audit)
  sync            Synchronize Tier 1 organizational rules from remote origin
  prefs           Manage local developer preferences (.agents/rules/personal/)
  package         Package or extract portable tooling zip bundle
  doctor          Verify local Git hook, rule tiers, and LLM authentication setup
  version         Print agentic-dev version

Run `agentic-dev <subcommand> --help` for subcommand-specific options."""
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch `agentic-dev` subcommands to the underlying module entrypoints."""
    args_list = list(sys.argv[1:] if argv is None else argv)

    if not args_list or args_list[0] in ("-h", "--help", "help"):
        print_top_level_help()
        return 0

    if args_list[0] in ("-V", "--version", "version"):
        print(f"agentic-dev {__version__}")
        return 0

    subcommand = args_list[0]
    sub_argv = args_list[1:]

    if subcommand in ("init", "install"):
        if "--clean" not in sub_argv and "-h" not in sub_argv and "--help" not in sub_argv:
            root_dir = _extract_root_dir_from_argv(sub_argv)
            bootstrap_workspace_templates(root_dir, overwrite=False, include_env_example=True)
            if "--binary-mode" not in sub_argv:
                sub_argv = ["--binary-mode", *sub_argv]
        return install_main(sub_argv)

    if subcommand == "review":
        return review_main(sub_argv)

    if subcommand == "sync":
        return sync_main(sub_argv)

    if subcommand == "prefs":
        return prefs_main(sub_argv)

    if subcommand == "package":
        return package_main(sub_argv)

    if subcommand == "doctor":
        return run_doctor(sub_argv)

    print(f"Error: Unknown subcommand '{subcommand}'.\n", file=sys.stderr)
    print_top_level_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
