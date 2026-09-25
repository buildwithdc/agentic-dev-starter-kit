#!/usr/bin/env python3
"""Rule loader for canonical multi-assistant best practices.

Parses Markdown rule files with YAML frontmatter from `.agents/rules/`
and filters rules matching staged file paths across a 3-tier governance
hierarchy (Organization, Team, Personal).
"""

from __future__ import annotations

import fnmatch
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

try:
    from scripts.constants import (
        DEFAULT_TIER,
        ORG_TIER_DIR_NAME,
        PERSONAL_TIER_DIR_NAME,
        SEVERITY_PRIORITY,
        TEAM_TIER_DIR_NAME,
        TIER_ORG,
        TIER_PERSONAL,
        TIER_PREFIX_MAP,
        TIER_PRIORITY,
        TIER_TEAM,
    )
except ImportError:
    from constants import (
        DEFAULT_TIER,
        ORG_TIER_DIR_NAME,
        PERSONAL_TIER_DIR_NAME,
        SEVERITY_PRIORITY,
        TEAM_TIER_DIR_NAME,
        TIER_ORG,
        TIER_PERSONAL,
        TIER_PREFIX_MAP,
        TIER_PRIORITY,
        TIER_TEAM,
    )


@dataclass(frozen=True)
class Rule:
    """Canonical rule representation."""

    id: str
    title: str
    severity_default: str
    tier: str = "team"  # "org", "team", "personal"
    applies_to: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    content: str = ""
    file_path: str = ""


def _parse_yaml_frontmatter(content: str) -> tuple[dict[str, object], str]:
    """Extract frontmatter and body from markdown content."""
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


def detect_rule_tier(file_path: Path, meta: dict[str, object]) -> str:
    """Infer rule tier from frontmatter, filename prefix, or directory hierarchy."""
    if "tier" in meta and str(meta["tier"]).lower() in TIER_PRIORITY:
        return str(meta["tier"]).lower()

    # Check filename prefix (e.g. org-01-..., team-01-..., personal-01-...)
    fname = file_path.name.lower()
    for tier, prefix in TIER_PREFIX_MAP.items():
        if fname.startswith(prefix):
            return tier

    parts = [p.lower() for p in file_path.parts]
    if ORG_TIER_DIR_NAME in parts:
        return TIER_ORG
    if PERSONAL_TIER_DIR_NAME in parts or ".gemini" in parts:
        return TIER_PERSONAL
    if TEAM_TIER_DIR_NAME in parts:
        return TIER_TEAM

    return DEFAULT_TIER


def _parse_rule_file(md_file: Path) -> Rule | None:
    """Parse a single Markdown rule file into a Rule instance."""
    if md_file.name.upper() == "README.MD" or md_file.name.startswith("."):
        return None

    try:
        raw_text = md_file.read_text(encoding="utf-8")
    except Exception:
        return None

    meta, body = _parse_yaml_frontmatter(raw_text)

    rule_id = str(meta.get("id", md_file.stem))
    title = str(meta.get("title", md_file.stem.replace("_", " ").title()))
    severity = str(meta.get("severity_default", "WARN")).upper()
    if severity not in SEVERITY_PRIORITY:
        severity = "WARN"

    tier = detect_rule_tier(md_file, meta)

    applies_to_raw = meta.get("applies_to", ["*"])
    if isinstance(applies_to_raw, list):
        applies_to = [str(x) for x in applies_to_raw]
    else:
        applies_to = [str(applies_to_raw)]

    tags_raw = meta.get("tags", [])
    if isinstance(tags_raw, list):
        tags = [str(x) for x in tags_raw]
    else:
        tags = [str(tags_raw)]

    return Rule(
        id=rule_id,
        title=title,
        severity_default=severity,
        tier=tier,
        applies_to=applies_to,
        tags=tags,
        content=body,
        file_path=str(md_file),
    )


def load_rules(
    rules_dir: Path | str,
    include_personal: bool = True,
    include_global: bool = True,
    global_rules_dir: Path | str | None = None,
) -> list[Rule]:
    """Scan and load rules across the 3-tier hierarchy with non-weakening precedence."""
    path = Path(rules_dir)
    discovered_files: list[Path] = []

    if path.is_dir():
        # Scan both flat rules and subdirectories (org/, team/, personal/)
        for md_file in sorted(path.rglob("*.md")):
            if md_file.name.upper() != "README.MD" and not md_file.name.startswith("."):
                if not include_personal and ("personal" in md_file.parts):
                    continue
                discovered_files.append(md_file)

    # Optional: include machine-global user rules (e.g., ~/.gemini/config/rules)
    if include_personal and include_global:
        gdir = (
            Path(global_rules_dir)
            if global_rules_dir
            else Path.home() / ".gemini" / "config" / "rules"
        )
        if gdir.is_dir():
            for gfile in sorted(gdir.rglob("*.md")):
                if gfile.name.upper() != "README.MD" and not gfile.name.startswith("."):
                    discovered_files.append(gfile)

    # Parse and index rules
    rules_by_id: dict[str, Rule] = {}

    for md_file in discovered_files:
        rule = _parse_rule_file(md_file)
        if not rule:
            continue

        existing = rules_by_id.get(rule.id)
        if not existing:
            rules_by_id[rule.id] = rule
            continue

        # Precedence Resolution: Org (3) > Team (2) > Personal (1)
        existing_priority = TIER_PRIORITY.get(existing.tier, 2)
        new_priority = TIER_PRIORITY.get(rule.tier, 2)

        if new_priority > existing_priority:
            # Higher tier always replaces lower tier
            rules_by_id[rule.id] = rule
        elif new_priority == existing_priority:
            # Same tier: later file overrides unless existing has higher severity
            rules_by_id[rule.id] = rule
        else:
            # Lower tier attempting to override higher tier:
            # Non-weakening principle: Lower tier cannot override or downgrade higher tier
            pass

    # Sort rules deterministically: Org first, then Team, then Personal, then by ID
    return sorted(
        rules_by_id.values(),
        key=lambda r: (-TIER_PRIORITY.get(r.tier, 0), r.id),
    )


def _matches_pattern(file_path: str, pattern: str) -> bool:
    """Check if file_path matches glob pattern."""
    clean_path = file_path.replace("\\", "/").lstrip("/")
    clean_pattern = pattern.replace("\\", "/").lstrip("/")

    if clean_pattern in ("*", "**/*", "**"):
        return True

    if fnmatch.fnmatch(clean_path, clean_pattern):
        return True

    if clean_pattern.startswith("**/"):
        suffix = clean_pattern[3:]
        if fnmatch.fnmatch(Path(clean_path).name, suffix):
            return True
        if fnmatch.fnmatch(clean_path, suffix):
            return True

    return False


def match_rules_for_files(rules: Sequence[Rule], file_paths: Sequence[str]) -> list[Rule]:
    """Filter rules that match at least one of the given file paths."""
    if not file_paths:
        return []

    matched: list[Rule] = []
    for rule in rules:
        for fpath in file_paths:
            if any(_matches_pattern(fpath, pattern) for pattern in rule.applies_to):
                matched.append(rule)
                break
    return matched
