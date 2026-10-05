---
name: code-auditor
description: Audits staged or working-tree code diffs against repository canonical best-practice rules in .agents/rules/.
---

# Code Auditor Skill

This skill allows the agent to evaluate current git changes against the repository's canonical best-practice rules located in `.agents/rules/`.

## Instructions
1. Run `python3 scripts/pre_commit_reviewer.py` to evaluate staged changes.
2. If violations are reported, inspect each reported file and line number.
3. Automatically correct any `CRITICAL` or `ERROR` violations, stage the files with `git add`, and re-test.
4. Address `WARN` advisories if applicable to current task scope.
