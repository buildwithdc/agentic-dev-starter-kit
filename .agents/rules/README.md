# Canonical Best-Practices Multi-Tier Rule Catalog

This directory contains vendor-neutral, canonical engineering standards and operational rules organized into a **3-tier governance hierarchy** (Organization, Team, and Personal). All AI coding assistants (e.g., Antigravity, Claude Code, Cursor) and human contributors adhere to these standards.

## Multi-Tier Governance Architecture

Rules are partitioned into distinct lifecycles to prevent cross-contamination, rule drift, and VCS merge conflicts:

| Tier | Directory | Owner & Lifecycle | Version Control (VCS) | Precedence |
| :--- | :--- | :--- | :--- | :--- |
| **Tier 1: Organization** | [`org/`](org/) | AI CoE / InfoSec / Platform Engineering. Synced automatically via 24h background worker or reusable CI. | Read-only mirror in project repo. | **Highest ($\text{Tier 1} \succ \text{Tier 2} \succ \text{Tier 3}$)** |
| **Tier 2: Team** | [`team/`](team/) | Team Tech Leads / Service Pods. Evolves with service architecture and domain models. | Committed and reviewed via normal PRs. | **Middle** |
| **Tier 3: Personal** | [`personal/`](personal/) | Individual Developer. Ergonomics, personal workflow habits, communication preferences. | **Untracked (`.gitignore`)** or machine-wide (`~/.gemini/config/rules/`). | **Lowest (Cannot weaken Org/Team rules)** |

---

## Active Rule Index

### Tier 1: Organization Rules (`org/`)
| Rule File | Title | Default Severity | Scope / Applies To |
| :--- | :--- | :--- | :--- |
| [`org/00_meta_guidelines.md`](org/00_meta_guidelines.md) | VCS Workflow & Branch Protection | `CRITICAL` | `*` (All files & git operations) |
| [`org/02_security_and_secrets.md`](org/02_security_and_secrets.md) | Security Policy & Secret Sanitization | `CRITICAL` | `*` (All files) |

### Tier 2: Team / Service Rules (`team/`)
| Rule File | Title | Default Severity | Scope / Applies To |
| :--- | :--- | :--- | :--- |
| [`team/01_architecture.md`](team/01_architecture.md) | Modular Architecture & Domain Boundaries | `WARN` | `**/*.py`, `**/*.ts`, `**/*.go`, `**/*.rs` |
| [`team/03_code_quality.md`](team/03_code_quality.md) | Code Quality, Typing & Mock Transparency | `WARN` | `**/*.py`, `**/*.ts`, `**/*.go` |
| [`team/04_testing_standards.md`](team/04_testing_standards.md) | Isolated & Deterministic Testing | `WARN` | `tests/**`, `**/*test*` |

### Tier 3: Personal Preferences (`personal/`)
| Rule File | Title | Default Severity | Scope / Applies To |
| :--- | :--- | :--- | :--- |
| [`personal/05_personal_preferences.md`](personal/05_personal_preferences.md) | Personal Developer Workflow Preferences | `WARN` | `*` (All files, local only) |

---

## Severity & Precedence Principles

- **`CRITICAL`**: Blocks commits immediately. Uncompromising violations (e.g., direct commits to main, hardcoded credentials, secret leakage).
- **`ERROR`**: Blocks commits. Clear architectural breaks, missing critical type contracts, or silent mocks outside test harnesses.
- **`WARN`**: Emits actionable advisories during pre-commit without blocking local WIP commits. Fully evaluated during PR / CI audits and on-demand `/audit`.
- **`INFO`**: Informational suggestions and style hints.

### Non-Weakening Rule of Precedence
Lower tiers can specialize or add constraints, but **cannot weaken or silence** constraints defined in a higher tier. For example, a personal preference or team rule cannot downgrade an organizational `CRITICAL` or `ERROR` rule to `WARN` or `INFO`.
