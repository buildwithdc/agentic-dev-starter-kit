---
id: rule-testing-standards
title: Isolated & Deterministic Testing Standards
severity_default: WARN
applies_to:
  - "tests/**/*"
  - "**/*test*"
  - "**/*_test.py"
  - "**/*.spec.ts"
tags:
  - testing
  - unit-tests
  - mocks
---

# Isolated & Deterministic Testing Standards

## 1. Structured & Isolated Test Suites
- Place unit tests verifying individual component logic in `tests/unit/`.
- Place multi-component, end-to-end, or integration workflows in `tests/integration/` or `tests/e2e/`.

## 2. Deterministic & Offline Testing
- Tests must be fast, deterministic, and runnable offline without dependencies on unseeded live cloud services.
- Use explicit test fixtures, hermetic mock harnesses, and deterministic stubs within the test suite.
