---
id: org-01-meta-guidelines
title: VCS Workflow, Branch Protection & Commit Standards
severity_default: CRITICAL
applies_to:
  - "**/*"
tags:
  - git
  - workflow
  - branch-protection
---

# VCS Workflow, Branch Protection & Commit Standards

## 1. Strict Main Branch Protection
- **Never push or commit directly to the `main` branch under any circumstances.**
- All changes must go through a dedicated task/feature branch and a Pull Request.
- Direct execution of `git push origin main` or amending commits on `main` is strictly prohibited.

## 2. Branching Conventions
Create and switch to a dedicated branch from the latest `main`:
- `feature/<name>` : New features, tools, or capabilities
- `fix/<name>`     : Bug fixes and error resolutions
- `docs/<name>`    : Documentation updates or guides
- `refactor/<name>`: Code restructuring without behavior changes
- `chore/<name>`   : Build configs, dependencies, or tooling
- `test/<name>`    : New tests or evaluation benchmarks
- `perf/<name>`    : Performance optimizations

## 3. Atomic Git Operations & Prohibitions
- **Never combine git operations into a single command** (e.g., avoid `git add . && git commit`). Execute each step independently.
- **Never use `git commit --no-verify`** to bypass safety gates.

## 4. Conventional Commit Messages
Commits must follow Conventional Commits format:
`<type>(<scope>): <short summary in imperative present tense>`
Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`.
Scopes: module or domain name (e.g., `core`, `api`, `auth`, `agent`, `workflow`, `tools`, `db`, `config`, `tests`).
