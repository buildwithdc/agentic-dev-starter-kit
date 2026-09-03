#!/usr/bin/env python3
"""Rule loader for canonical multi-assistant best practices.

Parses Markdown rule files with YAML frontmatter from `.agents/rules/`
and filters rules matching staged file paths.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class Rule:
    """Canonical rule representation."""

    id: str
    title: str
    severity_default: str
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


def load_rules(rules_dir: Path | str) -> list[Rule]:
    """Scan and load all markdown rules from the specified directory."""
    path = Path(rules_dir)
    if not path.is_dir():
        return []

    rules: list[Rule] = []
    for md_file in sorted(path.glob("*.md")):
        if md_file.name.upper() == "README.MD":
            continue

        raw_text = md_file.read_text(encoding="utf-8")
        meta, body = _parse_yaml_frontmatter(raw_text)

        rule_id = str(meta.get("id", md_file.stem))
        title = str(meta.get("title", md_file.stem.replace("_", " ").title()))
        severity = str(meta.get("severity_default", "WARN")).upper()
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

        rules.append(
            Rule(
                id=rule_id,
                title=title,
                severity_default=severity,
                applies_to=applies_to,
                tags=tags,
                content=body,
                file_path=str(md_file),
            )
        )
    return rules


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
