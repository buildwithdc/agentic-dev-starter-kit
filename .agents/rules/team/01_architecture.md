---
id: rule-architecture
title: Modular Architecture & Domain Boundaries
severity_default: WARN
applies_to:
  - "**/*.py"
  - "**/*.ts"
  - "**/*.js"
  - "**/*.go"
  - "**/*.rs"
tags:
  - architecture
  - modularity
  - contracts
---

# Modular Architecture & Domain Boundaries

## 1. Separation of Concerns & Layer Isolation
- Isolate business logic, orchestration, tools, data persistence, and UI/CLI presentations into distinct modules.
- Maintain clear directional dependencies (e.g., Presentation -> Service/Orchestrator -> Domain Core/Contracts -> Infrastructure/Data).
- Prevent cyclic dependencies between modules.

## 2. Loose Coupling & High Cohesion
- Components must communicate via well-defined, public interfaces rather than reaching into internal implementation details or private methods.

## 3. Contract-First Development (Typed Interfaces)
- Define authoritative data models and contracts (e.g., Pydantic models, TypedDicts, dataclasses, interfaces) before implementing domain logic.
- Cross-module data exchanges must rely on validated typed contracts to prevent runtime type errors.
