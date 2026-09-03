---
id: rule-security-secrets
title: Security Policy & Secret Sanitization
severity_default: CRITICAL
applies_to:
  - "**/*"
tags:
  - security
  - secrets
  - api-keys
---

# Security Policy & Secret Sanitization

## 1. Zero Hardcoded Credentials
- **Strictly Prohibited in Version Control**: Plaintext API tokens, private keys, database passwords, OAuth secrets, or production credentials.
- Do not commit `.env` files containing real secrets. Maintain an up-to-date `.env.example` template without sensitive values.

## 2. Dynamic Secret Management
- Load credentials dynamically through environment variables or secure secret stores (e.g., Secret Manager, Vault).
- Mask or redact sensitive strings from logs, traces, exception dumps, and diagnostic outputs.

## 3. Safe Defaults
- Disallow fallback defaults in code that expose fake or test secrets to production endpoints.
