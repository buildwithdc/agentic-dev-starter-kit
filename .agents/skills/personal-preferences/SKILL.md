---
name: personal-preferences
description: Captures, updates, and persists developer workflow preferences, habits, and engineering conventions into .agents/rules/personal/personal-01-preferences.md. Detects generally applicable workflow comments and always requests explicit user confirmation before recording new rules.
---

# Personal Preferences & Workflow Rules Skill

This skill enables the coding assistant to capture user engineering preferences, workflow conventions, and pair-programming guidelines, and persist them to `.agents/rules/personal/personal-01-preferences.md` (untracked in VCS).

## Triggers & Usage

Activate this skill when:
1. **Explicit User Intent**: The user asks to remember, add, update, list, or remove a coding or workflow preference / rule.
2. **Implicit Workflow Comments**: The assistant detects the user providing workflow-level feedback, corrections, or behavioral preferences that have general applicability across future tasks (e.g., preference for specific library patterns, formatting choices, confirmation checkpoints, error handling styles).

## Operational Workflow

### 1. General Workflow Feedback Detection
When the user gives feedback or instructions that are not merely one-off code edits but represent broader engineering preferences:
- Analyze whether the feedback applies generally to future coding or workflow interactions.
- Formulate a clear, concise, actionable rule statement.

### 2. Collision Check Against Higher-Tiered Rules (STRICT NON-WEAKENING POLICY)
> [!CAUTION]
> **Personal preferences must NEVER weaken, relax, bypass, or contradict higher-tiered rules.**
> Under the repository's mathematical governance model (Org > Team > Personal), Tier 1 Organization rules and Tier 2 Team rules take unconditional precedence.

Before proposing or storing any preference:
1. **Semantic Collision Check**:
   - Inspect canonical rules in `.agents/rules/org/` and `.agents/rules/team/`.
   - Run the semantic collision checker:
     ```bash
     python3 scripts/preference_manager.py --check-collision "<Proposed preference>"
     ```
2. **Evaluate Invariants**:
   - Does this preference attempt to permit committing secrets, API keys, or private tokens? (Conflicts with `org-02-security-and-secrets`)
   - Does it attempt to bypass `main` branch protection, PR workflows, or `--no-verify` commits? (Conflicts with `org-01-meta-guidelines`)
   - Does it attempt to weaken typed schemas, contract validation, or suppress runtime mock warnings? (Conflicts with `team-01-architecture` / `team-02-code-quality`)
3. **Collision Handling**:
   - **If a collision is detected:** HALT immediately. Do NOT ask the user to record the rule. Explain:
     > "I cannot record this preference because it conflicts with higher-tiered rule `[rule_id]`. Under repository governance, personal preferences cannot relax or contradict organization or team invariants."
   - **If clean:** Proceed to Step 3 (Mandatory User Confirmation).

### 3. Mandatory User Confirmation (STRICT REQUIREMENT)
> [!IMPORTANT]
> **NEVER silently add or modify rules in `.agents/rules/personal/personal-01-preferences.md`.**
> You MUST ALWAYS explicitly ask the user for confirmation before recording or modifying any preference.

Example confirmation prompt:
> "I noticed you mentioned [specific preference or workflow comment]. I verified that it does not conflict with higher-tier Org or Team rules. Would you like me to record this as a rule in `.agents/rules/personal/personal-01-preferences.md` so that future sessions automatically adhere to it?"

### 4. Recording Preferences
Once explicit confirmation is received from the user:
- Run the preference manager CLI with `--strict` check:
  ```bash
  python3 scripts/preference_manager.py --add "<Rule description>" --section "<1. General Workflow Preferences | 2. Code Style & Engineering Conventions | 3. Tooling & Environment Preferences>" --strict
  ```
- Or inspect existing preferences:
  ```bash
  python3 scripts/preference_manager.py --list
  ```
- Confirm to the user that the preference has been recorded and is now part of the active canonical rules reviewed during development and pre-commit checks.
