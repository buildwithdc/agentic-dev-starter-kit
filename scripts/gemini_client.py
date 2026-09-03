#!/usr/bin/env python3
"""Gemini Flash API client with JSON schema enforcement and local diff caching."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
DEFAULT_TIMEOUT = float(os.getenv("GEMINI_TIMEOUT", "5.0"))
GEMINI_API_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "passed": {"type": "BOOLEAN"},
        "summary": {"type": "STRING"},
        "violations": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "file": {"type": "STRING"},
                    "line_number": {"type": "INTEGER"},
                    "rule_id": {"type": "STRING"},
                    "rule_title": {"type": "STRING"},
                    "severity": {
                        "type": "STRING",
                        "enum": ["INFO", "WARN", "ERROR", "CRITICAL"],
                    },
                    "issue": {"type": "STRING"},
                    "suggested_fix": {"type": "STRING"},
                    "suggested_code_diff": {"type": "STRING"},
                },
                "required": [
                    "file",
                    "rule_title",
                    "severity",
                    "issue",
                    "suggested_fix",
                ],
            },
        },
    },
    "required": ["passed", "summary", "violations"],
}


@dataclass
class ReviewViolation:
    """Diagnostic violation detail."""

    file: str
    rule_title: str
    severity: str
    issue: str
    suggested_fix: str
    line_number: int | None = None
    rule_id: str = ""
    suggested_code_diff: str = ""


@dataclass
class ReviewResult:
    """Overall review outcome."""

    passed: bool
    summary: str
    violations: list[ReviewViolation] = field(default_factory=list)
    cached: bool = False
    degraded: bool = False


class GeminiReviewClient:
    """Client for evaluating code diffs against best-practice rules."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
        cache_dir: Path | str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self.model = model
        self.timeout = timeout
        self.cache_dir = Path(cache_dir) if cache_dir else Path(".git/.llm_cache")

    def _compute_cache_key(self, diff_text: str, rules_summary: str) -> str:
        payload = f"{self.model}\n{rules_summary}\n{diff_text}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _get_cached_result(self, cache_key: str) -> ReviewResult | None:
        try:
            cache_file = self.cache_dir / f"{cache_key}.json"
            if cache_file.exists():
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                violations = [ReviewViolation(**v) for v in data.get("violations", [])]
                return ReviewResult(
                    passed=bool(data.get("passed", True)),
                    summary=str(data.get("summary", "Cached review")),
                    violations=violations,
                    cached=True,
                )
        except Exception:
            pass
        return None

    def _save_cached_result(self, cache_key: str, result: ReviewResult) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = self.cache_dir / f"{cache_key}.json"
            data = {
                "passed": result.passed,
                "summary": result.summary,
                "violations": [asdict(v) for v in result.violations],
            }
            cache_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def review_diff(
        self,
        diff_text: str,
        rules_text: str,
        staged_files: Sequence[str] | None = None,
    ) -> ReviewResult:
        """Evaluate staged diff against rules using Gemini Flash."""
        if not diff_text.strip():
            return ReviewResult(
                passed=True,
                summary="No staged diff to review.",
                violations=[],
            )

        cache_key = self._compute_cache_key(diff_text, rules_text)
        cached = self._get_cached_result(cache_key)
        if cached is not None:
            return cached

        if not self.api_key:
            return ReviewResult(
                passed=True,
                summary="Review skipped: GEMINI_API_KEY is unset. Fallback to local checks.",
                violations=[],
                degraded=True,
            )

        prompt = (
            "You are an automated pre-commit code review assistant. Evaluate the following "
            "staged git diff against the repository's canonical best-practice rules.\n\n"
            f"### CANONICAL RULES:\n{rules_text}\n\n"
            f"### STAGED FILES:\n{json.dumps(list(staged_files or []))}\n\n"
            f"### STAGED DIFF:\n{diff_text}\n\n"
            "Instructions:\n"
            "1. Check if any rule is violated in the diff.\n"
            "2. Assign severity 'CRITICAL' for hard security secrets / main branch bypass, "
            "'ERROR' for broken typed interfaces or missing mock warnings, and 'WARN' for general advice.\n"
            "3. If no violations exist, set passed=true and violations=[].\n"
            "4. Return strictly valid JSON adhering to the provided schema."
        )

        url = f"{GEMINI_API_ENDPOINT.format(model=self.model)}?key={self.api_key}"
        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": REVIEW_SCHEMA,
                "temperature": 0.1,
            },
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                candidate = resp_data.get("candidates", [{}])[0]
                text = (
                    candidate.get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "{}")
                )
                raw_json = json.loads(text)

                violations = [
                    ReviewViolation(
                        file=str(v.get("file", "unknown")),
                        line_number=v.get("line_number"),
                        rule_id=str(v.get("rule_id", "")),
                        rule_title=str(v.get("rule_title", "")),
                        severity=str(v.get("severity", "WARN")).upper(),
                        issue=str(v.get("issue", "")),
                        suggested_fix=str(v.get("suggested_fix", "")),
                        suggested_code_diff=str(v.get("suggested_code_diff", "")),
                    )
                    for v in raw_json.get("violations", [])
                ]

                # If there are any CRITICAL or ERROR violations, passed is false
                hard_failures = [v for v in violations if v.severity in ("CRITICAL", "ERROR")]
                passed = raw_json.get("passed", True) and len(hard_failures) == 0

                result = ReviewResult(
                    passed=passed,
                    summary=raw_json.get("summary", "Review complete."),
                    violations=violations,
                )
                self._save_cached_result(cache_key, result)
                return result

        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as e:
            # Graceful degradation on network/API failure
            return ReviewResult(
                passed=True,
                summary=f"Review skipped: API call encountered {type(e).__name__} ({e}). Graceful fallback enabled.",
                violations=[],
                degraded=True,
            )
