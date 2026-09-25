# Human-Agent Collaboration Protocol & Repository Operating Guidelines

> [!IMPORTANT]
> **STRICT MAIN BRANCH PROTECTION POLICY**
> Even if GitHub repository-level branch protection rules are not yet enabled in the web settings, **ALL human contributors and AI agents MUST strictly treat `main` as a protected branch**:
> 1. **NEVER push or commit directly to the `main` branch under any circumstances.**
> 2. **ALL changes must go through a dedicated feature/fix branch and a Pull Request (PR).**
> 3. Direct execution of `git push origin main` or amending commits on `main` is strictly prohibited.

---

## 1. Modular Architecture & Domain Boundaries

To ensure codebase maintainability, scalability, and seamless collaboration between the developer and AI coding assistants, the repository is organized into distinct, modular layers with clear separation of concerns.

### Architecture Principles

* **Separation of Concerns**: Isolate core business logic, agent orchestration, external tools/integrations, data persistence, and API/presentation layers.
* **Loose Coupling & High Cohesion**: Components should communicate through well-defined interfaces rather than reaching into internal implementation details of other modules.
* **Explicit Dependency Management**: Maintain clear directional dependencies (e.g., UI/API -> Orchestrator/Services -> Domain Core/Contracts -> Infrastructure/Data).
* **Predictable Directory Structure**: Keep files logically grouped by domain or functionality so both human and agent can locate, modify, and test components with precision.

---

## 2. Development Guidelines & Quality Standards

Every development session and AI assistant interaction must adhere to these foundational principles:

### Rule 1: Scoped & Atomic Changes
* Keep changes focused and limited to the immediate task or feature scope.
* Avoid sprawling refactors or touching unrelated files in a single turn or branch.

### Rule 2: Contract-First Development (Typed Interfaces)
* Define authoritative data models, schemas, and API contracts (e.g., Pydantic models, TypedDicts, or interface schemas) before implementing business logic.
* Ensure all cross-module data exchanges rely on validated typed contracts to prevent subtle runtime bugs.

### Rule 3: Isolated Mock Harnesses During Development & Mocking Transparency
* **User Clarification Required**: The AI agent must always clarify and confirm with the user before implementing a mock or stub in place of real integration logic.
* **Non-Testing Runtime Visibility**: Whenever a mock or fallback is triggered outside of test suites (e.g., in development/runtime processes), it must emit prominent, unmistakable logging or warning statements (e.g., `[WARNING: MOCK IMPLEMENTATION TRIGGERED]`) to prevent silent simulated behavior.
* **Offline & Deterministic Testing**: Provide and consume mock harnesses and stubs within the test suite to keep testing deterministic, fast, and repeatable offline without relying on unseeded or live third-party services.

### Rule 4: Structured & Isolated Unit Test Suites
* Maintain unit tests alongside modules (e.g., in `tests/unit/`) to verify individual component logic in isolation.
* Place multi-component, end-to-end, or integration workflows in `tests/integration/` or `tests/e2e/`.

### Rule 5: Centralized Configuration Management
* Never hardcode environment-specific parameters, hostnames, ports, or project coordinates in application code.
* Manage configurations via typed settings objects backed by environment variables (e.g., `.env` files).
* Always maintain an up-to-date `.env.example` template without sensitive values.

### Rule 6: Secret Management & Security Policy
* **Permitted in Version Control**: Non-sensitive configuration templates, resource names, sample payloads, and mock credentials for testing.
* **Strictly Prohibited in Version Control**: Plaintext API tokens, private keys, database passwords, or production credentials.
* **Secrets Handling**: Load secrets dynamically through environment variables or a secure secret manager (e.g., Google Cloud Secret Manager, AWS Secrets Manager, Vault).

### Rule 7: Autonomous Runtime Issue Resolution & Approval Boundaries
* **Autonomous Low-Impact Fixes**: The AI agent is authorized and encouraged to autonomously diagnose and resolve runtime errors, exceptions, typing mismatches, and unexpected bugs when the fix requires **no significant changes to user journeys, UX workflows, or core business logic**.
* **Mandatory User Approval for Significant Changes**: If resolving a runtime issue necessitates **significant functional changes** (altering business logic, user interaction flows, or public contracts) or **significant non-functional changes** (architectural redesigns, dependency/framework swaps, database schema alterations, security model changes, or performance trade-offs), the AI agent **MUST halt, present the proposed approach and trade-offs to the user, and obtain explicit approval before applying the changes**.

---

## 3. Branching Strategy & Naming Conventions

Before writing code or making any modifications, always create and switch to a dedicated branch from the latest `main`:

```bash
# 1. Fetch latest changes from origin
git checkout main
git pull origin main

# 2. Create and switch to your task branch
git checkout -b <branch-type>/<short-description>
```

### Branch Type Prefixes
* `feature/<name>` : New features, tools, or capabilities
* `fix/<name>`     : Bug fixes and error resolutions
* `docs/<name>`    : Documentation updates or new guides
* `refactor/<name>`: Code restructuring without behavior changes
* `chore/<name>`   : Build configs, dependencies, or tooling updates
* `test/<name>`    : New test datasets or evaluation benchmarks
* `perf/<name>`    : Performance optimizations

### Branch Naming Examples
* `feature/user-auth-flow`
* `feature/rag-pipeline`
* `fix/session-timeout-handling`
* `refactor/db-connection-pool`
* `chore/update-dependencies`

Never combine git operations into a single command.
Never ever use "git commit --no-verify"

---

## 4. Commit Message Guidelines

All commits must follow the **Conventional Commits** standard:

### Format
```text
<type>(<scope>): <short summary in imperative present tense>

[optional body: explain WHY this change was made and WHAT it solves]

[optional footer: e.g. Closes #123]
```

### Allowed Types
* `feat`    : A new user-facing feature or capability
* `fix`     : A bug fix or patch
* `docs`    : Documentation-only changes (README, markdown guides)
* `refactor`: Code restructuring without bug fixes or new features
* `test`    : Adding or updating unit/integration tests
* `chore`   : Build tools, package dependencies, CI configuration
* `perf`    : Performance optimizations

### Allowed Scopes
Scopes should match the relevant domain, module, or component being modified (e.g., `core`, `api`, `auth`, `agent`, `workflow`, `tools`, `db`, `config`, `tests`).

### Examples
* `feat(agent): add intent classification for multi-turn dialogs`
* `feat(tools): implement file search and retrieval MCP tool`
* `fix(auth): handle expired token refresh gracefully`
* `refactor(db): optimize query indexing for session history`
* `test(eval): add golden evaluation test cases`

---

## 5. Local Verification & Testing

Before opening a Pull Request, verify that all local checks and tests pass:

```bash
# Run linters / formatting checks
uv run ruff check .

# Run test suites
uv run pytest
```

---

## 6. Pull Request (PR) & Rebase Workflow

To ensure a clean, linear git history:

1. **Rebase on Main**:
   ```bash
   git fetch origin main
   git rebase origin/main
   ```
2. **Push Branch**:
   ```bash
   git push -u origin <your-branch-name>
   ```
3. **Open PR**: Create a Pull Request targeting `main`.
4. **PR Description**:
   * **Summary**: High-level overview of what changed.
   * **Scope & Impact**: Modules affected and rationale.
   * **Verification**: Test and linting results (`ruff`, `pytest`, etc.).
5. **Review & Merge**:
   * Merge via **Squash and Merge**.

---

<!-- BEGIN CANONICAL RULES POINTER (Auto-generated by scripts/install_tooling.py) -->
## Repository Standards & Canonical Rules
- Canonical best-practice rules and guidelines are maintained in `.agents/rules/` across 3 tiers (org, team, personal).
- Custom developer and workflow preferences are managed in `.agents/rules/personal/personal-01-preferences.md`.
<!-- END CANONICAL RULES POINTER -->
