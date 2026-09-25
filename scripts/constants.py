#!/usr/bin/env python3
"""Centralized repository constants for rule paths, filenames, tiers, and tooling."""

from __future__ import annotations

from pathlib import Path

# ==============================================================================
# Rule Directory Hierarchies
# ==============================================================================
RULES_DIR_NAME = ".agents/rules"
ORG_TIER_DIR_NAME = "org"
TEAM_TIER_DIR_NAME = "team"
PERSONAL_TIER_DIR_NAME = "personal"

ORG_RULES_REL_PATH = f"{RULES_DIR_NAME}/{ORG_TIER_DIR_NAME}"
TEAM_RULES_REL_PATH = f"{RULES_DIR_NAME}/{TEAM_TIER_DIR_NAME}"
PERSONAL_RULES_REL_PATH = f"{RULES_DIR_NAME}/{PERSONAL_TIER_DIR_NAME}"

# ==============================================================================
# Canonical Rule File Names (Tier Prefixes and Re-numbered)
# ==============================================================================
# Tier 1: Organization Rules (org-01 to org-99)
RULE_ORG_META_GUIDELINES = "org-01-meta-guidelines.md"
RULE_ORG_SECURITY_SECRETS = "org-02-security-and-secrets.md"

ORG_RULE_FILES = (
    RULE_ORG_META_GUIDELINES,
    RULE_ORG_SECURITY_SECRETS,
)

# Tier 2: Team / Service Rules (team-01 to team-99)
RULE_TEAM_ARCHITECTURE = "team-01-architecture.md"
RULE_TEAM_CODE_QUALITY = "team-02-code-quality.md"
RULE_TEAM_TESTING_STANDARDS = "team-03-testing-standards.md"

TEAM_RULE_FILES = (
    RULE_TEAM_ARCHITECTURE,
    RULE_TEAM_CODE_QUALITY,
    RULE_TEAM_TESTING_STANDARDS,
)

# Tier 3: Personal Preferences (personal-01 to personal-99)
RULE_PERSONAL_PREFERENCES = "personal-01-preferences.md"

PERSONAL_RULE_FILES = (
    RULE_PERSONAL_PREFERENCES,
)

# Full Relative Paths
DEFAULT_PERSONAL_PREFERENCES_PATH = f"{PERSONAL_RULES_REL_PATH}/{RULE_PERSONAL_PREFERENCES}"

# ==============================================================================
# Governance, Tiers & Precedence
# ==============================================================================
TIER_ORG = "org"
TIER_TEAM = "team"
TIER_PERSONAL = "personal"

TIER_PRIORITY: dict[str, int] = {
    TIER_PERSONAL: 1,
    TIER_TEAM: 2,
    TIER_ORG: 3,
}

TIER_ORDER = [TIER_ORG, TIER_TEAM, TIER_PERSONAL]
DEFAULT_TIER = TIER_TEAM

TIER_PREFIX_MAP = {
    TIER_ORG: "org-",
    TIER_TEAM: "team-",
    TIER_PERSONAL: "personal-",
}

SEVERITY_PRIORITY: dict[str, int] = {
    "INFO": 0,
    "WARN": 1,
    "ERROR": 2,
    "CRITICAL": 3,
}

# ==============================================================================
# Synchronization & Audit Defaults
# ==============================================================================
SYNC_TIMESTAMP_FILENAME = ".sync_timestamp"
DEFAULT_SYNC_TTL_SECONDS = 86400  # 24 hours
DEFAULT_AUDIT_LOG_PATH = ".agents/audit.log"
DEFAULT_CACHE_DIR_PATH = ".git/.llm_cache"


def get_rule_path(tier: str, rule_filename: str, root_dir: Path | None = None) -> Path:
    """Resolve full Path for a tier and rule filename."""
    base = root_dir if root_dir is not None else Path(".")
    return base / RULES_DIR_NAME / tier / rule_filename
