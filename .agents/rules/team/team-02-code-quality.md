---
id: team-02-code-quality
title: Code Quality, Typing & Mock Transparency
severity_default: WARN
applies_to:
  - "**/*.py"
  - "**/*.ts"
  - "**/*.js"
  - "**/*.go"
tags:
  - quality
  - typing
  - mocks
  - logging
---

# Code Quality, Typing & Mock Transparency

## 1. Scoped & Atomic Changes
- Keep changes focused and limited to the immediate task or feature scope.
- Avoid sprawling refactors or touching unrelated files in a single turn or commit.

## 2. Strict Static Typing
- All new functions, methods, and public interfaces must have explicit type annotations.
- Avoid loose fallback types (such as untyped `Any`, `Object`, `interface{}`) without documented justification.

## 3. Mocking Transparency & Non-Testing Runtime Visibility
- **User Clarification Required**: Coding assistants must confirm with the user before implementing a mock in place of real integration logic.
- **Non-Testing Runtime Visibility**: Whenever a mock, stub, or dummy fallback is triggered outside test suites, it must emit prominent logging/warning statements (e.g., `[WARNING: MOCK IMPLEMENTATION TRIGGERED]`) to prevent silent simulated behavior in development.

## 4. Documentation Integrity
- Preserve existing comments, docstrings, and type hints unrelated to the specific changes being made.

## 5. Autonomous Runtime Issue Resolution & Approval Boundaries
- **Autonomous Low-Impact Fixes**: Assistants are authorized and expected to autonomously diagnose and resolve runtime errors, exceptions, typing defects, and edge cases when the fix introduces no significant alterations to user journeys, UX workflows, or core business logic.
- **Mandatory Approval for Significant Changes**: If resolving a runtime issue requires significant functional changes (modifying business logic, user interaction flows, or public contracts) or significant non-functional changes (architectural redesigns, library/framework replacements, data storage overhauls, security model adjustments, or performance trade-offs), the assistant must halt, explain the trade-offs, and obtain explicit user approval before proceeding.

