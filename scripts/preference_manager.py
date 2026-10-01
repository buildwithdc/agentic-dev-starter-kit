#!/usr/bin/env python3
"""Personal & Workflow Preference Manager.

Provides CLI and programmatic utilities to inspect, record, and maintain
custom engineering rules and workflow preferences in `.agents/rules/05_personal_preferences.md`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from scripts.constants import (
        DEFAULT_PERSONAL_PREFERENCES_PATH,
        RULES_DIR_NAME,
    )
    from scripts.gemini_client import (
        GeminiReviewClient,
        RuleCollisionResult,
    )
    from scripts.rule_loader import load_rules
except ImportError:
    from constants import (
        DEFAULT_PERSONAL_PREFERENCES_PATH,
        RULES_DIR_NAME,
    )
    from gemini_client import (
        GeminiReviewClient,
        RuleCollisionResult,
    )
    from rule_loader import load_rules

DEFAULT_RULE_RELATIVE_PATH = DEFAULT_PERSONAL_PREFERENCES_PATH


def check_rule_collision(
    rule_text: str,
    rules_dir: Path | str = RULES_DIR_NAME,
    root_dir: Path | str = ".",
    client: GeminiReviewClient | None = None,
) -> RuleCollisionResult:
    """Evaluate whether proposed personal preference semantically conflicts with higher-tier rules (Org, Team) using Gemini LLM."""
    clean_rule = rule_text.strip()
    if not clean_rule:
        return RuleCollisionResult(has_collision=False, summary="Empty rule text.")

    resolved_root = Path(root_dir).resolve()
    rpath = resolved_root / rules_dir if not Path(rules_dir).is_absolute() else Path(rules_dir)
    if not rpath.is_dir():
        return RuleCollisionResult(has_collision=False, summary=f"Rules directory not found: {rpath}")

    # Load only Tier 1 (Org) and Tier 2 (Team) rules
    higher_rules = load_rules(rpath, include_personal=False, include_global=False)
    if not higher_rules:
        return RuleCollisionResult(has_collision=False, summary="No higher-tier rules found to evaluate against.")

    rules_summary = "\n\n".join(
        f"--- Rule: {r.id} ({r.title}) [Tier: {r.tier.upper()}] [Default Severity: {r.severity_default}] ---\n{r.content}"
        for r in higher_rules
    )

    llm_client = client or GeminiReviewClient()
    return llm_client.check_rule_collision(
        proposed_rule=clean_rule,
        higher_tier_rules_text=rules_summary,
    )

DEFAULT_TEMPLATE = """---
id: personal-01-preferences
title: Personal & Team Engineering Preferences
severity_default: WARN
applies_to:
  - "**/*"
tags:
  - preferences
  - workflow
  - custom
---

# Personal & Team Engineering Preferences

This document captures customized workflow guidelines, developer preferences, and team-specific conventions added during collaboration sessions.

## 1. General Workflow Preferences
- Explicitly confirm before modifying repository rule definitions or personal preferences.

## 2. Code Style & Engineering Conventions
<!-- Language, framework, and repository specific stylistic preferences -->

## 3. Tooling & Environment Preferences
<!-- CLI, editor, testing, and operational environment preferences -->
"""


def _parse_yaml_frontmatter(content: str) -> tuple[dict[str, object], str]:
    """Extract YAML frontmatter and body from Markdown."""
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, content

    end_idx = -1
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = idx
            break

    if end_idx == -1:
        return {}, content

    frontmatter_lines = lines[1:end_idx]
    body = "\n".join(lines[end_idx + 1 :]).strip()

    metadata: dict[str, object] = {}
    current_key: str | None = None
    current_list: list[str] | None = None

    for raw_line in frontmatter_lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("- ") and current_key and current_list is not None:
            item = stripped[2:].strip().strip("\"'")
            current_list.append(item)
            continue

        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip().strip("\"'")
            current_key = key
            if not val:
                current_list = []
                metadata[key] = current_list
            else:
                current_list = None
                metadata[key] = val

    return metadata, body


def _dump_yaml_frontmatter(metadata: dict[str, object]) -> str:
    """Serialize dictionary metadata to YAML frontmatter block."""
    lines = ["---"]
    for key, val in metadata.items():
        if isinstance(val, list):
            lines.append(f"{key}:")
            for item in val:
                lines.append(f"  - {item}")
        else:
            lines.append(f"{key}: {val}")
    lines.append("---")
    return "\n".join(lines)


def _resolve_rule_path(rule_file: Path | str | None, root_dir: Path | str = ".") -> Path:
    """Resolve absolute path to the personal preferences rule file."""
    if rule_file:
        p = Path(rule_file)
        if p.is_absolute():
            return p
        return Path(root_dir).resolve() / p

    return Path(root_dir).resolve() / DEFAULT_RULE_RELATIVE_PATH


def parse_preferences_document(content: str) -> tuple[dict[str, object], str, dict[str, list[str]]]:
    """Parse preferences Markdown into frontmatter, preamble, and sectioned rules."""
    metadata, body = _parse_yaml_frontmatter(content)
    if not metadata:
        metadata = {
            "id": "rule-personal-preferences",
            "title": "Personal & Team Engineering Preferences",
            "severity_default": "WARN",
            "applies_to": ["**/*"],
            "tags": ["preferences", "workflow", "custom"],
        }

    lines = body.splitlines()
    preamble_lines: list[str] = []
    sections: dict[str, list[str]] = {}
    current_section: str | None = None

    header_pattern = re.compile(r"^##\s+(.+)$")
    bullet_pattern = re.compile(r"^[-*]\s+(.+)$")

    for line in lines:
        stripped = line.strip()
        header_match = header_pattern.match(stripped)
        if header_match:
            current_section = header_match.group(1).strip()
            if current_section not in sections:
                sections[current_section] = []
            continue

        if current_section is None:
            preamble_lines.append(line)
        else:
            bullet_match = bullet_pattern.match(stripped)
            if bullet_match:
                sections[current_section].append(bullet_match.group(1).strip())

    preamble = "\n".join(preamble_lines).strip()
    return metadata, preamble, sections


def format_preferences_document(
    metadata: dict[str, object],
    preamble: str,
    sections: dict[str, list[str]],
) -> str:
    """Reconstruct Markdown document from structured preferences data."""
    frontmatter = _dump_yaml_frontmatter(metadata)
    output: list[str] = [frontmatter, "", preamble if preamble else "# Personal & Team Engineering Preferences", ""]

    default_sections = [
        "1. General Workflow Preferences",
        "2. Code Style & Engineering Conventions",
        "3. Tooling & Environment Preferences",
    ]

    all_section_names: list[str] = []
    for sec in default_sections:
        if sec in sections:
            all_section_names.append(sec)
    for sec in sections:
        if sec not in all_section_names:
            all_section_names.append(sec)

    for sec_name in all_section_names:
        bullets = sections.get(sec_name, [])
        output.append(f"## {sec_name}")
        if bullets:
            for b in bullets:
                output.append(f"- {b}")
        else:
            output.append("<!-- No specific rules recorded yet -->")
        output.append("")

    return "\n".join(output).rstrip() + "\n"


def load_preferences(
    rule_file: Path | str | None = None,
    root_dir: Path | str = ".",
) -> dict[str, list[str]]:
    """Load all rules categorized by section."""
    target_file = _resolve_rule_path(rule_file, root_dir)
    if not target_file.is_file():
        return {}

    content = target_file.read_text(encoding="utf-8")
    _, _, sections = parse_preferences_document(content)
    return sections


def add_preference(
    rule_text: str,
    section: str = "1. General Workflow Preferences",
    rule_file: Path | str | None = None,
    root_dir: Path | str = ".",
) -> tuple[bool, str]:
    """Add a new rule to the personal preferences document.

    Returns (success, message).
    """
    clean_rule = rule_text.strip()
    if clean_rule.startswith(("- ", "* ")):
        clean_rule = clean_rule[2:].strip()

    if not clean_rule:
        return False, "Error: Rule text cannot be empty."

    target_file = _resolve_rule_path(rule_file, root_dir)
    if not target_file.exists():
        target_file.parent.mkdir(parents=True, exist_ok=True)
        content = DEFAULT_TEMPLATE
    else:
        content = target_file.read_text(encoding="utf-8")

    metadata, preamble, sections = parse_preferences_document(content)

    # Check for duplicates across all sections
    for sec_name, bullets in sections.items():
        for b in bullets:
            if b.lower().strip() == clean_rule.lower().strip():
                return False, f"Rule already exists under '{sec_name}': {b}"

    # Find matching section (case-insensitive substring match or exact match)
    target_section_name = section.strip()
    matched_section: str | None = None
    for existing_sec in sections:
        if existing_sec.lower() == target_section_name.lower():
            matched_section = existing_sec
            break
        if target_section_name.lower() in existing_sec.lower():
            matched_section = existing_sec
            break

    if matched_section:
        sections[matched_section].append(clean_rule)
    else:
        # Create new section if not found
        sections[target_section_name] = [clean_rule]
        matched_section = target_section_name

    new_content = format_preferences_document(metadata, preamble, sections)
    target_file.write_text(new_content, encoding="utf-8")
    return True, f"Successfully added rule under '{matched_section}': {clean_rule}"


def remove_preference(
    pattern: str,
    rule_file: Path | str | None = None,
    root_dir: Path | str = ".",
) -> tuple[int, list[str]]:
    """Remove rule entries matching pattern (case-insensitive substring)."""
    target_file = _resolve_rule_path(rule_file, root_dir)
    if not target_file.is_file():
        return 0, []

    content = target_file.read_text(encoding="utf-8")
    metadata, preamble, sections = parse_preferences_document(content)

    removed: list[str] = []
    pattern_lower = pattern.lower().strip()

    for sec_name, bullets in list(sections.items()):
        retained: list[str] = []
        for b in bullets:
            if pattern_lower in b.lower():
                removed.append(b)
            else:
                retained.append(b)
        sections[sec_name] = retained

    if removed:
        new_content = format_preferences_document(metadata, preamble, sections)
        target_file.write_text(new_content, encoding="utf-8")

    return len(removed), removed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Personal & Workflow Engineering Preference Manager",
    )
    parser.add_argument(
        "--add",
        "-a",
        metavar="RULE_TEXT",
        help="Add a new engineering rule or workflow preference",
    )
    parser.add_argument(
        "--section",
        "-s",
        default="1. General Workflow Preferences",
        help="Target section name for the rule (default: '1. General Workflow Preferences')",
    )
    parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="List all recorded preferences",
    )
    parser.add_argument(
        "--remove",
        "-r",
        metavar="PATTERN",
        help="Remove preferences matching a substring pattern",
    )
    parser.add_argument(
        "--rule-file",
        metavar="FILE_PATH",
        help=f"Custom path to preferences markdown (default: {DEFAULT_RULE_RELATIVE_PATH})",
    )
    parser.add_argument(
        "--root-dir",
        default=".",
        help="Root repository directory (default: .)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output in JSON format",
    )
    parser.add_argument(
        "--check-collision",
        metavar="RULE_TEXT",
        help="Check if a proposed preference semantically conflicts with higher-tiered rules (Org, Team) via Gemini",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Enforce Gemini semantic collision check before adding rule via --add",
    )

    args = parser.parse_args(argv)

    if args.check_collision:
        result = check_rule_collision(
            args.check_collision,
            root_dir=args.root_dir,
        )
        if result.degraded:
            print(f"⚠️ {result.summary}", file=sys.stderr)
            return 0
        if result.has_collision:
            print("❌ Semantic collision detected with higher-tiered rules:")
            for c in result.conflicts:
                print(f"  - [{c.tier}] {c.rule_id} ({c.rule_title}): {c.reason}")
            return 1
        else:
            print("✅ No collisions detected with higher-tiered rules (Org / Team).")
            return 0

    if args.add:
        if args.strict:
            collision_res = check_rule_collision(args.add, root_dir=args.root_dir)
            if collision_res.has_collision:
                print("❌ Cannot add preference: semantic collision detected with higher-tier rules:", file=sys.stderr)
                for c in collision_res.conflicts:
                    print(f"  - [{c.tier}] {c.rule_id} ({c.rule_title}): {c.reason}", file=sys.stderr)
                return 1

        success, msg = add_preference(
            rule_text=args.add,
            section=args.section,
            rule_file=args.rule_file,
            root_dir=args.root_dir,
        )
        if success:
            print(f"✅ {msg}")
            return 0
        else:
            print(f"⚠️ {msg}", file=sys.stderr)
            return 1

    if args.remove:
        count, removed_items = remove_preference(
            pattern=args.remove,
            rule_file=args.rule_file,
            root_dir=args.root_dir,
        )
        if count > 0:
            print(f"🗑️ Removed {count} matching rule(s):")
            for item in removed_items:
                print(f"  - {item}")
            return 0
        else:
            print(f"No rules matched pattern '{args.remove}'.")
            return 1

    if args.list or (not args.add and not args.remove):
        prefs = load_preferences(rule_file=args.rule_file, root_dir=args.root_dir)
        if args.json:
            print(json.dumps(prefs, indent=2))
            return 0

        target_path = _resolve_rule_path(args.rule_file, args.root_dir)
        print(f"📋 Personal & Team Engineering Preferences ({target_path}):\n")
        if not prefs:
            print("  (No preferences recorded yet)")
            return 0

        total_count = sum(len(bullets) for bullets in prefs.values())
        if total_count == 0:
            print("  (No preferences recorded yet)")
            return 0

        for sec, bullets in prefs.items():
            print(f"## {sec}")
            if bullets:
                for b in bullets:
                    print(f"  - {b}")
            else:
                print("  (No rules)")
            print()
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
