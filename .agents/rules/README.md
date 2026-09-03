# Canonical Best-Practices Rule Catalog

This directory contains vendor-neutral, canonical engineering standards and operational rules for the repository. All AI coding assistants (e.g., Antigravity, Claude Code, Cursor) and human contributors adhere to these standards.

## Rule Index

| Rule File | Title | Default Severity | Scope / Applies To |
| :--- | :--- | :--- | :--- |
| [`00_meta_guidelines.md`](00_meta_guidelines.md) | VCS Workflow & Branch Protection | `CRITICAL` | `*` (All files & git operations) |
| [`01_architecture.md`](01_architecture.md) | Modular Architecture & Domain Boundaries | `WARN` | `**/*.py`, `**/*.ts`, `**/*.go`, `**/*.rs` |
| [`02_security_and_secrets.md`](02_security_and_secrets.md) | Security Policy & Secret Sanitization | `CRITICAL` | `*` (All files) |
| [`03_code_quality.md`](03_code_quality.md) | Code Quality, Typing & Mock Transparency | `WARN` | `**/*.py`, `**/*.ts`, `**/*.go` |
| [`04_testing_standards.md`](04_testing_standards.md) | Isolated & Deterministic Testing | `WARN` | `tests/**`, `**/*test*` |

## Severity Levels

- **`CRITICAL`**: Blocks commits immediately. Uncompromising violations (e.g., direct commits to main, hardcoded credentials, secret leakage).
- **`ERROR`**: Blocks commits. Clear architectural breaks, missing critical type contracts, or silent mocks outside test harnesses.
- **`WARN`**: Emits actionable advisories during pre-commit without blocking local WIP commits. Fully evaluated during PR / CI audits and on-demand `/audit`.
- **`INFO`**: Informational suggestions and style hints.
