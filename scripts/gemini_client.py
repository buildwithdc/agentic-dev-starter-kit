#!/usr/bin/env python3
"""Configurable Gemini Review Client supporting Google Application Default Credentials (ADC) and Vertex AI."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rule_loader import Rule

try:
    from scripts.constants import (
        DEFAULT_DECISION_MODEL,
        DEFAULT_DECISION_THRESHOLD,
        DEFAULT_REVIEW_MODE,
        ENV_DECISION_MODEL,
        ENV_DECISION_THRESHOLD,
        ENV_REVIEW_MODE,
        SEVERITY_PRIORITY,
        VALID_REVIEW_MODES,
    )
except ImportError:
    from constants import (
        DEFAULT_DECISION_MODEL,
        DEFAULT_DECISION_THRESHOLD,
        DEFAULT_REVIEW_MODE,
        ENV_DECISION_MODEL,
        ENV_DECISION_THRESHOLD,
        ENV_REVIEW_MODE,
        SEVERITY_PRIORITY,
        VALID_REVIEW_MODES,
    )

# Environment Variable Configuration Keys
ENV_BACKEND = "GEMINI_BACKEND"
ENV_MODEL = "GEMINI_MODEL"
ENV_PROJECT = "GOOGLE_CLOUD_PROJECT"
ENV_PROJECT_ALT = "GCP_PROJECT"
ENV_LOCATION = "GOOGLE_CLOUD_LOCATION"
ENV_LOCATION_ALT = "GCP_REGION"
ENV_TIMEOUT = "GEMINI_TIMEOUT"
ENV_API_KEY = "GOOGLE_AI_STUDIO_KEY"

DEFAULT_BACKEND = "vertex"
DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_LOCATION = "global"
DEFAULT_TIMEOUT = 30.0

PLACEHOLDER_API_KEYS: frozenset[str] = frozenset(
    {
        "",
        "your_google_ai_studio_key_here",
        "your-google-ai-studio-key-here",
        "your_gemini_api_key_here",
        "your_api_key_here",
        "your-gemini-api-key-here",
        "your-api-key",
        "changeme",
        "none",
        "null",
    }
)


def sanitize_api_key(raw_key: str | None) -> str | None:
    """Return a clean API key or None if unset or set to a template placeholder."""
    if not raw_key:
        return None
    cleaned = raw_key.strip().strip('"').strip("'").strip()
    if cleaned.lower() in PLACEHOLDER_API_KEYS:
        return None
    return cleaned


VERTEX_ENDPOINT_TEMPLATE = (
    "https://{location}-aiplatform.googleapis.com/v1/projects/{project_id}/"
    "locations/{location}/publishers/google/models/{model}:generateContent"
)
VERTEX_GLOBAL_ENDPOINT_TEMPLATE = (
    "https://aiplatform.googleapis.com/v1/projects/{project_id}/"
    "locations/global/publishers/google/models/{model}:generateContent"
)
GOOGLE_AI_ENDPOINT_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
GOOGLE_AI_DECISIONS_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/decisions"
)

DECISION_SEVERITY_CRITERIA: list[str] = [
    "COMPLIANT: The added or modified lines (+ prefix) in the staged diff comply with this specific rule, only delete (- prefix) old issues, or this rule is orthogonal/not applicable to the diff.",
    "INFO: Minor informational notice specifically within the scope of this rule.",
    "WARN: Non-blocking advisory deviation specifically within the scope of this rule.",
    "ERROR: Substantive violation specifically within the scope of this rule (e.g., broken typed contract, broken downstream caller, cyclic dependency, or missing mock warning).",
    "CRITICAL: Severe violation specifically within the scope of this rule (only applicable if this specific rule governs security/secrets or main branch protection).",
]

BUCKET_TO_SEVERITY: dict[str, str] = {
    "1": "INFO",
    "2": "WARN",
    "3": "ERROR",
    "4": "CRITICAL",
}

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

RULE_COLLISION_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "has_collision": {"type": "BOOLEAN"},
        "summary": {"type": "STRING"},
        "conflicts": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "rule_id": {"type": "STRING"},
                    "rule_title": {"type": "STRING"},
                    "tier": {"type": "STRING"},
                    "reason": {"type": "STRING"},
                },
                "required": ["rule_id", "rule_title", "tier", "reason"],
            },
        },
    },
    "required": ["has_collision", "summary", "conflicts"],
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
    confidence: float | None = None


@dataclass
class ReviewResult:
    """Overall review outcome."""

    passed: bool
    summary: str
    violations: list[ReviewViolation] = field(default_factory=list)
    cached: bool = False
    degraded: bool = False
    evaluation_path: str = "generative_only"
    stage1_model: str | None = None
    stage2_model: str | None = None
    stage1_latency_ms: float | None = None
    stage2_latency_ms: float | None = None
    total_latency_ms: float | None = None


@dataclass
class RuleConflictDetail:
    """Detail of a collision with a higher-tier rule."""

    rule_id: str
    rule_title: str
    tier: str
    reason: str
    confidence: float | None = None


@dataclass
class RuleCollisionResult:
    """Outcome of checking a proposed rule against higher-tier rules."""

    has_collision: bool
    summary: str
    conflicts: list[RuleConflictDetail] = field(default_factory=list)
    degraded: bool = False


@dataclass
class DecisionRuleEvaluation:
    """Result of evaluating a single rule via Gemini Decisions API (`gemini-decision-1`)."""

    rule: Any
    severity: str
    violation_probability: float
    confidence: float
    expected_score: float


@dataclass
class GeminiClientConfig:
    """Runtime configuration for Gemini Client."""

    backend: str = DEFAULT_BACKEND
    model: str = DEFAULT_MODEL
    decision_model: str = DEFAULT_DECISION_MODEL
    review_mode: str = DEFAULT_REVIEW_MODE
    decision_threshold: float = DEFAULT_DECISION_THRESHOLD
    project_id: str | None = None
    location: str = DEFAULT_LOCATION
    timeout: float = DEFAULT_TIMEOUT
    api_key: str | None = None

    def __post_init__(self) -> None:
        """Normalize placeholder API keys and review_mode."""
        self.api_key = sanitize_api_key(self.api_key)
        mode = (self.review_mode or DEFAULT_REVIEW_MODE).strip().lower()
        self.review_mode = mode if mode in VALID_REVIEW_MODES else DEFAULT_REVIEW_MODE

    @classmethod
    def from_env(cls) -> GeminiClientConfig:
        """Create config populated from environment variables."""
        api_key = sanitize_api_key(os.getenv(ENV_API_KEY))
        explicit_backend = os.getenv(ENV_BACKEND)
        if explicit_backend:
            backend = explicit_backend.lower()
        elif api_key:
            backend = "google_ai"
        else:
            backend = DEFAULT_BACKEND

        raw_mode = os.getenv(ENV_REVIEW_MODE, DEFAULT_REVIEW_MODE).strip().lower()
        review_mode = raw_mode if raw_mode in VALID_REVIEW_MODES else DEFAULT_REVIEW_MODE

        try:
            threshold = float(os.getenv(ENV_DECISION_THRESHOLD, str(DEFAULT_DECISION_THRESHOLD)))
        except ValueError:
            threshold = DEFAULT_DECISION_THRESHOLD

        return cls(
            backend=backend,
            model=os.getenv(ENV_MODEL, DEFAULT_MODEL),
            decision_model=os.getenv(ENV_DECISION_MODEL, DEFAULT_DECISION_MODEL),
            review_mode=review_mode,
            decision_threshold=max(0.05, min(0.99, threshold)),
            project_id=os.getenv(ENV_PROJECT) or os.getenv(ENV_PROJECT_ALT),
            location=os.getenv(ENV_LOCATION) or os.getenv(ENV_LOCATION_ALT, DEFAULT_LOCATION),
            timeout=float(os.getenv(ENV_TIMEOUT, str(DEFAULT_TIMEOUT))),
            api_key=api_key,
        )


def discover_gcp_project_id() -> str | None:
    """Discover Google Cloud Project ID hierarchically."""
    # 1. Environment variables
    env_proj = os.getenv(ENV_PROJECT) or os.getenv(ENV_PROJECT_ALT)
    if env_proj:
        return env_proj.strip()

    # 2. Inspect standard ADC file if present
    adc_paths = [
        Path(os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""))
        if os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        else None,
        Path.home() / ".config" / "gcloud" / "application_default_credentials.json",
        Path(os.getenv("APPDATA", "")) / "gcloud" / "application_default_credentials.json"
        if os.getenv("APPDATA")
        else None,
    ]
    for p in adc_paths:
        if p and p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                proj = data.get("quota_project_id") or data.get("project_id")
                if proj:
                    return str(proj).strip()
            except Exception:
                pass

    # 3. Fallback to gcloud config CLI
    if shutil.which("gcloud"):
        try:
            res = subprocess.run(
                ["gcloud", "config", "get-value", "project"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            val = res.stdout.strip()
            if val and val != "(unset)":
                return val
        except Exception:
            pass

    return None


def get_adc_access_token(cache_dir: Path | None = None) -> str | None:
    """Acquire Google Cloud OAuth2 access token with local caching."""
    # Check cache first
    now = time.time()
    cache_file = (cache_dir / "adc_token.json") if cache_dir else None

    if cache_file and cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if data.get("expires_at", 0) > now + 60:
                token = data.get("token")
                if token:
                    return str(token)
        except Exception:
            pass

    # 1. Try google.auth SDK if available
    try:
        if not os.getenv("GCE_METADATA_HOST") and not os.getenv("K_SERVICE"):
            os.environ.setdefault("NO_GCE_CHECK", "true")

        import google.auth
        import google.auth.transport.requests

        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        auth_req = google.auth.transport.requests.Request()
        creds.refresh(auth_req)
        token = creds.token
        if token and cache_file:
            try:
                cache_dir.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(
                    json.dumps({"token": token, "expires_at": now + 1800}),
                    encoding="utf-8",
                )
            except Exception:
                pass
        return token
    except Exception:
        pass

    # 2. Fallback to gcloud CLI
    if shutil.which("gcloud"):
        try:
            res = subprocess.run(
                ["gcloud", "auth", "application-default", "print-access-token"],
                capture_output=True,
                text=True,
                timeout=3.0,
                check=False,
            )
            token = res.stdout.strip()
            if res.returncode == 0 and token and not token.startswith("ERROR"):
                if cache_file:
                    try:
                        cache_dir.mkdir(parents=True, exist_ok=True)
                        cache_file.write_text(
                            json.dumps({"token": token, "expires_at": now + 1800}),
                            encoding="utf-8",
                        )
                    except Exception:
                        pass
                return token
        except Exception:
            pass

    return None


class GeminiReviewClient:
    """Client for evaluating code diffs against best-practice rules via ADC or API Key."""

    def __init__(
        self,
        config: GeminiClientConfig | None = None,
        cache_dir: Path | str | None = None,
    ) -> None:
        self.config = config or GeminiClientConfig.from_env()
        self.cache_dir = Path(cache_dir) if cache_dir else Path(".git/.llm_cache")

        if not self.config.project_id:
            self.config.project_id = discover_gcp_project_id()

    def _compute_cache_key(
        self,
        diff_text: str,
        rules_summary: str,
        dependency_context: str = "",
    ) -> str:
        dep_part = f"\n{dependency_context}" if dependency_context else ""
        mode_part = (
            f":{self.config.review_mode}:{self.config.decision_model}"
            if self.config.review_mode != DEFAULT_REVIEW_MODE
            or self.config.decision_model != DEFAULT_DECISION_MODEL
            else ""
        )
        payload = (
            f"{self.config.backend}:{self.config.model}:{self.config.project_id}{mode_part}\n"
            f"{rules_summary}\n{diff_text}{dep_part}"
        ).encode()
        return hashlib.sha256(payload).hexdigest()

    def _get_cached_result(self, cache_key: str) -> ReviewResult | None:
        try:
            cache_file = self.cache_dir / f"{cache_key}.json"
            if cache_file.exists():
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                violations = [ReviewViolation(**v) for v in data.get("violations", [])]
                orig_path = str(data.get("evaluation_path", "cache_hit"))
                return ReviewResult(
                    passed=bool(data.get("passed", True)),
                    summary=str(data.get("summary", "Cached review")),
                    violations=violations,
                    cached=True,
                    evaluation_path=f"cache_hit({orig_path})" if orig_path != "cache_hit" else "cache_hit",
                    stage1_model=data.get("stage1_model"),
                    stage2_model=data.get("stage2_model"),
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
                "evaluation_path": result.evaluation_path,
                "stage1_model": result.stage1_model,
                "stage2_model": result.stage2_model,
                "violations": [asdict(v) for v in result.violations],
            }
            cache_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _resolve_auth(self) -> tuple[str | None, str | None]:
        """Resolve authentication token or return error message."""
        token: str | None = None
        if not (self.config.backend in ("google_ai", "studio") and self.config.api_key):
            token = get_adc_access_token(self.cache_dir)
            if not token:
                return None, (
                    "Authentication skipped: Google ADC access token not found and GOOGLE_AI_STUDIO_KEY unset. "
                    "Run 'gcloud auth application-default login' or export GOOGLE_AI_STUDIO_KEY to enable LLM checks."
                )

        if self.config.backend == "vertex" and not self.config.project_id:
            return None, (
                "Vertex AI skipped: GCP Project ID could not be determined. "
                "Set GOOGLE_CLOUD_PROJECT or run 'gcloud config set project <PROJECT_ID>'."
            )

        return token, None

    def _prepare_request(
        self,
        prompt: str,
        token: str | None,
        response_schema: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        """Construct endpoint URL, headers, and payload according to configured backend."""
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": response_schema or REVIEW_SCHEMA,
                "temperature": 0.1,
            },
        }

        headers: dict[str, str] = {"Content-Type": "application/json"}

        if self.config.backend == "vertex":
            location = self.config.location or DEFAULT_LOCATION
            project_id = self.config.project_id or "default"
            if location == "global":
                url = VERTEX_GLOBAL_ENDPOINT_TEMPLATE.format(
                    project_id=project_id,
                    model=self.config.model,
                )
            else:
                url = VERTEX_ENDPOINT_TEMPLATE.format(
                    location=location,
                    project_id=project_id,
                    model=self.config.model,
                )
            if token:
                headers["Authorization"] = f"Bearer {token}"
        else:
            # Google AI Studio / Generative Language backend
            if self.config.api_key:
                url = f"{GOOGLE_AI_ENDPOINT_TEMPLATE.format(model=self.config.model)}?key={self.config.api_key}"
            else:
                url = GOOGLE_AI_ENDPOINT_TEMPLATE.format(model=self.config.model)
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                if self.config.project_id:
                    headers["x-goog-user-project"] = self.config.project_id

        return url, headers, payload

    def _prepare_decision_request(
        self,
        input_text: str,
        questions: dict[str, Any],
        token: str | None,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        """Construct Gemini Decisions API (`/v1beta/decisions`) URL, headers, and payload."""
        payload: dict[str, Any] = {
            "model": self.config.decision_model,
            "input": input_text,
            "questions": questions,
        }
        headers: dict[str, str] = {"Content-Type": "application/json"}
        url = GOOGLE_AI_DECISIONS_ENDPOINT

        if self.config.api_key:
            headers["x-goog-api-key"] = self.config.api_key
        else:
            if token:
                headers["Authorization"] = f"Bearer {token}"
            if self.config.project_id:
                headers["x-goog-user-project"] = self.config.project_id

        return url, headers, payload

    def _evaluate_rules_vertex_fast_decision(
        self,
        diff_text: str,
        matched_rules: Sequence[Rule],
        staged_files: Sequence[str] | None = None,
        dependency_context: str = "",
        token: str | None = None,
    ) -> tuple[list[DecisionRuleEvaluation], bool]:
        """Stage 1 Fast Decision Mode (`thinkingBudget=0` + closed enum schema, 0 prose)."""
        if not matched_rules or (not token and not self.config.api_key):
            return [], True

        clean_dep = dependency_context.strip()
        dep_section = (
            f"\n\n### LOCAL DEPENDENCY CONTEXT (UPSTREAM & DOWNSTREAM):\n{clean_dep}"
            if clean_dep
            else ""
        )
        rules_block = "\n\n".join(
            f"--- Rule ID: {r.id} ({r.title}) [Tier: {r.tier.upper()}] [Default Severity: {r.severity_default}] ---\n{r.content}"
            for r in matched_rules
        )

        prompt = (
            "You are a fast discriminative code compliance classifier. Evaluate whether the staged git diff "
            "violates each rule below. Output ONLY the enum classification for each rule ID:\n"
            "- 'COMPLIANT': Diff complies with the rule or rule is not applicable.\n"
            "- 'INFO': Minor informational note.\n"
            "- 'WARN': Non-blocking advisory deviation.\n"
            "- 'ERROR': Broken typed contract, broken downstream caller, cyclic dependency, or missing mock warning.\n"
            "- 'CRITICAL': Hardcoded secret/API key/credential exposure or direct main branch protection bypass.\n\n"
            "Use LOCAL DEPENDENCY CONTEXT strictly to detect relational contract breakage introduced by the diff; "
            "never flag pre-existing issues in untouched neighbor files.\n\n"
            f"### RULES:\n{rules_block}\n\n"
            f"### STAGED FILES:\n{json.dumps(list(staged_files or []))}\n\n"
            f"### STAGED DIFF:\n{diff_text}{dep_section}"
        )

        schema_props: dict[str, Any] = {
            r.id: {
                "type": "STRING",
                "enum": ["COMPLIANT", "INFO", "WARN", "ERROR", "CRITICAL"],
            }
            for r in matched_rules
        }
        decision_schema: dict[str, Any] = {
            "type": "OBJECT",
            "properties": schema_props,
            "required": [r.id for r in matched_rules],
        }

        fast_decision_model = (
            self.config.model
            if self.config.decision_model == DEFAULT_DECISION_MODEL
            else self.config.decision_model
        )
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.config.backend == "vertex" and not self.config.api_key:
            location = self.config.location or DEFAULT_LOCATION
            project_id = self.config.project_id or "default"
            if location == "global":
                url = VERTEX_GLOBAL_ENDPOINT_TEMPLATE.format(
                    project_id=project_id,
                    model=fast_decision_model,
                )
            else:
                url = VERTEX_ENDPOINT_TEMPLATE.format(
                    location=location,
                    project_id=project_id,
                    model=fast_decision_model,
                )
            if token:
                headers["Authorization"] = f"Bearer {token}"
        else:
            if self.config.api_key:
                url = f"{GOOGLE_AI_ENDPOINT_TEMPLATE.format(model=fast_decision_model)}?key={self.config.api_key}"
            else:
                url = GOOGLE_AI_ENDPOINT_TEMPLATE.format(model=fast_decision_model)
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                if self.config.project_id:
                    headers["x-goog-user-project"] = self.config.project_id

        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": decision_schema,
                "temperature": 0.0,
                "thinkingConfig": {"thinkingBudget": 0},
                "responseLogprobs": True,
            },
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return [], True

        try:
            candidate = resp_data.get("candidates", [{}])[0]
            text = (
                candidate.get("content", {})
                .get("parts", [{}])[0]
                .get("text", "{}")
            )
            decisions_map = json.loads(text)
            avg_logprobs = candidate.get("avgLogprobs")
            calibrated_conf = (
                round(max(0.50, min(0.99, math.exp(float(avg_logprobs)))), 4)
                if avg_logprobs is not None
                else 0.92
            )
        except Exception:
            return [], True

        score_map = {"INFO": 1.0, "WARN": 2.0, "ERROR": 3.0, "CRITICAL": 4.0}
        flagged: list[DecisionRuleEvaluation] = []
        for rule in matched_rules:
            verdict = str(decisions_map.get(rule.id, "COMPLIANT")).upper()
            if verdict in ("INFO", "WARN", "ERROR", "CRITICAL"):
                detected_severity = verdict
                if (
                    rule.tier.lower() == "org"
                    and rule.severity_default.upper() in ("CRITICAL", "ERROR")
                    and SEVERITY_PRIORITY.get(detected_severity, 1)
                    < SEVERITY_PRIORITY.get(rule.severity_default.upper(), 2)
                ):
                    detected_severity = rule.severity_default.upper()

                flagged.append(
                    DecisionRuleEvaluation(
                        rule=rule,
                        severity=detected_severity,
                        violation_probability=calibrated_conf,
                        confidence=calibrated_conf,
                        expected_score=score_map.get(detected_severity, 2.0),
                    )
                )

        return flagged, False

    def evaluate_rules_decision(
        self,
        diff_text: str,
        matched_rules: Sequence[Rule],
        staged_files: Sequence[str] | None = None,
        dependency_context: str = "",
        token: str | None = None,
    ) -> tuple[list[DecisionRuleEvaluation], bool]:
        """Stage 1 (System 1): Evaluate matched rules in parallel via `gemini-decision-1` (or Vertex AI ADC fast decision mode).

        Returns `(flagged_evaluations, degraded_or_failed)`.
        """
        if not matched_rules:
            return [], False

        # When running on Vertex AI with Google Cloud ADC (and no Google AI Studio API key),
        # use the Vertex AI Fast Decision pass (`thinkingBudget=0` + closed enum schema) directly
        # instead of failing with 403 on `generativelanguage.googleapis.com/v1beta/decisions`.
        if self.config.backend == "vertex" and not self.config.api_key and token:
            return self._evaluate_rules_vertex_fast_decision(
                diff_text=diff_text,
                matched_rules=matched_rules,
                staged_files=staged_files,
                dependency_context=dependency_context,
                token=token,
            )

        clean_dep = dependency_context.strip()
        dep_section = (
            f"\n\n### LOCAL DEPENDENCY CONTEXT (UPSTREAM & DOWNSTREAM):\n{clean_dep}"
            if clean_dep
            else ""
        )
        input_state = (
            f"### STAGED FILES:\n{json.dumps(list(staged_files or []))}\n\n"
            f"### STAGED DIFF:\n{diff_text}{dep_section}"
        )

        questions: dict[str, Any] = {}
        for rule in matched_rules:
            questions[rule.id] = {
                "type": "score",
                "instructions": (
                    f"Evaluate ONLY whether the newly added/modified lines (+ prefix) in the staged git diff violate "
                    f"THIS specific {rule.tier.upper()} best-practice rule ({rule.title}, Default Severity: {rule.severity_default}). "
                    "Do NOT flag violations that belong to other unrelated rules, and treat deleted lines (- prefix) "
                    "that remove bad code as COMPLIANT. Use LOCAL DEPENDENCY CONTEXT strictly to detect relational "
                    "contract breakage or cyclic dependencies introduced by the diff; never flag pre-existing issues in "
                    f"untouched neighbor files.\n\nRule Specification:\n{rule.content}"
                ),
                "criteria": DECISION_SEVERITY_CRITERIA,
            }

        url, headers, payload = self._prepare_decision_request(input_state, questions, token)
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return [], True

        answers = resp_data.get("answers")
        if not isinstance(answers, dict):
            return [], True

        flagged: list[DecisionRuleEvaluation] = []
        for rule in matched_rules:
            ans = answers.get(rule.id)
            if not isinstance(ans, dict):
                continue

            probs = ans.get("probabilities", {})
            if isinstance(probs, dict) and probs:
                p_compliant = float(probs.get("0", 0.0))
                p_violation = max(0.0, min(1.0, 1.0 - p_compliant))
                non_zero_buckets = {k: float(probs.get(k, 0.0)) for k in ("1", "2", "3", "4")}
                top_bucket = max(non_zero_buckets, key=lambda k: non_zero_buckets[k])
                detected_severity = BUCKET_TO_SEVERITY.get(top_bucket, rule.severity_default)
            else:
                raw_score = float(ans.get("score", 0.0))
                p_violation = min(1.0, raw_score / 4.0)
                bucket_idx = str(max(1, min(4, round(raw_score))))
                detected_severity = BUCKET_TO_SEVERITY.get(bucket_idx, rule.severity_default)

            expected_score = float(ans.get("score", 0.0))
            confidence = float(ans.get("confidence", p_violation))

            # Use slightly stricter threshold for Tier 1 Org guardrails
            rule_threshold = (
                max(0.50, self.config.decision_threshold - 0.05)
                if rule.tier.lower() == "org"
                else self.config.decision_threshold
            )

            if p_violation >= rule_threshold and expected_score >= 0.85:
                # Non-weakening floor for Tier 1 ORG CRITICAL rules:
                # If an ORG rule with CRITICAL default is substantively violated, enforce at least ERROR/CRITICAL
                if (
                    rule.tier.lower() == "org"
                    and rule.severity_default.upper() in ("CRITICAL", "ERROR")
                    and expected_score >= 2.0
                    and SEVERITY_PRIORITY.get(detected_severity, 1)
                    < SEVERITY_PRIORITY.get(rule.severity_default.upper(), 2)
                ):
                    detected_severity = rule.severity_default.upper()

                flagged.append(
                    DecisionRuleEvaluation(
                        rule=rule,
                        severity=detected_severity,
                        violation_probability=round(p_violation, 4),
                        confidence=round(confidence, 4),
                        expected_score=round(expected_score, 4),
                    )
                )

        return flagged, False

    def _build_decision_only_violations(
        self,
        flagged_evals: Sequence[DecisionRuleEvaluation],
        staged_files: Sequence[str] | None = None,
    ) -> list[ReviewViolation]:
        """Construct structured ReviewViolation records directly from Stage 1 decision outputs."""
        primary_file = staged_files[0] if staged_files else "staged_diff"
        violations: list[ReviewViolation] = []
        for ev in flagged_evals:
            rule = ev.rule
            first_line = next(
                (
                    line.strip().lstrip("-*# ").strip()
                    for line in rule.content.splitlines()
                    if line.strip() and not line.strip().startswith("#")
                ),
                rule.title,
            )
            violations.append(
                ReviewViolation(
                    file=primary_file,
                    line_number=None,
                    rule_id=rule.id,
                    rule_title=rule.title,
                    severity=ev.severity,
                    issue=(
                        f"Gemini Decision 1 flagged a {ev.severity} violation of "
                        f"[{rule.tier.upper()}] {rule.id} (probability={ev.violation_probability:.0%})."
                    ),
                    suggested_fix=f"Ensure staged changes satisfy '{rule.title}': {first_line}",
                    suggested_code_diff="",
                    confidence=ev.confidence,
                )
            )
        return violations

    def _generate_remediation_with_flash(
        self,
        diff_text: str,
        rules_text: str,
        staged_files: Sequence[str] | None,
        clean_dep_context: str,
        token: str | None,
        flagged_evals: Sequence[DecisionRuleEvaluation] | None = None,
    ) -> ReviewResult | None:
        """Stage 2 (System 2): Invoke generative Gemini model (`gemini-3.8-flash`) to synthesize exact locations and remediation diffs."""
        dep_section = (
            f"### LOCAL DEPENDENCY CONTEXT (UPSTREAM & DOWNSTREAM):\n{clean_dep_context}\n\n"
            if clean_dep_context
            else ""
        )

        if flagged_evals:
            scoped_rules_text = "\n\n".join(
                f"--- Flagged Rule: {ev.rule.id} ({ev.rule.title}) [Tier: {ev.rule.tier.upper()}] "
                f"[Stage-1 Detected Severity: {ev.severity} | Violation Probability: {ev.violation_probability:.0%}] ---\n"
                f"{ev.rule.content}"
                for ev in flagged_evals
            )
        else:
            scoped_rules_text = rules_text

        prompt = (
            "You are an automated pre-commit code review assistant. Evaluate the following "
            "staged git diff against the repository's canonical best-practice rules.\n\n"
            f"### CANONICAL RULES:\n{scoped_rules_text}\n\n"
            f"### STAGED FILES:\n{json.dumps(list(staged_files or []))}\n\n"
            f"### STAGED DIFF:\n{diff_text}\n\n"
            f"{dep_section}"
            "Instructions:\n"
            "1. Check if any rule is violated in the diff.\n"
            "2. Assign severity 'CRITICAL' for hard security secrets / main branch bypass, "
            "'ERROR' for broken typed interfaces, broken downstream callers, cyclic dependencies, or missing mock warnings, and 'WARN' for general advice.\n"
            "3. If no violations exist, set passed=true and violations=[].\n"
            "4. Return strictly valid JSON adhering to the provided schema.\n"
            "5. HIERARCHICAL PRECEDENCE DIRECTIVE: Rules tagged [Tier: ORG] strictly supersede [Tier: TEAM], "
            "which strictly supersede [Tier: PERSONAL]. If a lower-tier rule attempts to permit, relax, "
            "carve out exceptions to, or contradict a constraint mandated by a higher-tier rule, you MUST "
            "disregard the lower-tier allowance and strictly enforce the higher-tier requirement. "
            "Lower tiers can ONLY add stricter constraints, never weaken higher ones.\n"
            "6. RELATIONAL DEPENDENCY EVALUATION DIRECTIVE: When LOCAL DEPENDENCY CONTEXT is provided, "
            "use it strictly to detect cross-file impact caused by the staged diff (e.g., breaking a downstream "
            "caller's signature/contract, violating an upstream typed interface, or introducing cyclic/layer "
            "boundary violations). NEVER flag pre-existing, unrelated issues in untouched neighbor files."
        )

        url, headers, payload = self._prepare_request(prompt, token)
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        confidence_by_rule_id = (
            {ev.rule.id: ev.confidence for ev in flagged_evals} if flagged_evals else {}
        )
        confidence_by_title = (
            {ev.rule.title.lower(): ev.confidence for ev in flagged_evals} if flagged_evals else {}
        )

        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                candidate = resp_data.get("candidates", [{}])[0]
                text = (
                    candidate.get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "{}")
                )
                raw_json = json.loads(text)

                violations: list[ReviewViolation] = []
                for v in raw_json.get("violations", []):
                    r_id = str(v.get("rule_id", ""))
                    r_title = str(v.get("rule_title", ""))
                    conf = confidence_by_rule_id.get(r_id) or confidence_by_title.get(
                        r_title.lower()
                    )
                    violations.append(
                        ReviewViolation(
                            file=str(v.get("file", "unknown")),
                            line_number=v.get("line_number"),
                            rule_id=r_id,
                            rule_title=r_title,
                            severity=str(v.get("severity", "WARN")).upper(),
                            issue=str(v.get("issue", "")),
                            suggested_fix=str(v.get("suggested_fix", "")),
                            suggested_code_diff=str(v.get("suggested_code_diff", "")),
                            confidence=conf,
                        )
                    )

                hard_failures = [v for v in violations if v.severity in ("CRITICAL", "ERROR")]
                passed = raw_json.get("passed", True) and len(hard_failures) == 0

                return ReviewResult(
                    passed=passed,
                    summary=raw_json.get("summary", "Review complete."),
                    violations=violations,
                )
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, Exception):
            return None

    def check_rule_collision(
        self,
        proposed_rule: str,
        higher_tier_rules_text: str,
        higher_rules: Sequence[Rule] | None = None,
    ) -> RuleCollisionResult:
        """Evaluate whether a proposed personal preference semantically conflicts with or weakens higher-tier rules (Org, Team)."""
        clean_rule = proposed_rule.strip()
        if not clean_rule:
            return RuleCollisionResult(has_collision=False, summary="Empty rule text.")

        token, err = self._resolve_auth()
        if err:
            return RuleCollisionResult(
                has_collision=False,
                summary=err,
                degraded=True,
            )

        # Stage 1 (System 1): Parallel `noul` evaluation via `gemini-decision-1` when structured rules are provided
        if higher_rules and self.config.review_mode in ("hybrid", "decision_only"):
            questions: dict[str, Any] = {}
            for r in higher_rules:
                questions[r.id] = {
                    "type": "noul",
                    "instructions": (
                        f"Does the proposed personal preference contradict, relax, bypass, or carve out "
                        f"an exception to this {r.tier.upper()} rule ({r.title})?\n\n"
                        f"Canonical {r.tier.upper()} Rule:\n{r.content}"
                    ),
                    "criteria": {
                        "true_description": "The proposed preference conflicts with, relaxes, or weakens this higher-tier rule.",
                        "false_description": "The proposed preference is compatible with or orthogonal to this rule.",
                    },
                }

            input_state = f"### PROPOSED PERSONAL PREFERENCE:\n{clean_rule}"
            url, headers, payload = self._prepare_decision_request(input_state, questions, token)
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                answers = resp_data.get("answers")
                if isinstance(answers, dict):
                    colliding_rules: list[tuple[Rule, float, float]] = []
                    for r in higher_rules:
                        ans = answers.get(r.id)
                        if not isinstance(ans, dict):
                            continue
                        noul_prob = float(ans.get("noul", 0.0))
                        conf = float(ans.get("confidence", noul_prob))
                        if noul_prob >= self.config.decision_threshold:
                            colliding_rules.append((r, noul_prob, conf))

                    if not colliding_rules:
                        return RuleCollisionResult(
                            has_collision=False,
                            summary="No collisions detected with higher-tiered rules (evaluated via gemini-decision-1).",
                            conflicts=[],
                        )

                    if self.config.review_mode == "decision_only":
                        conflicts = [
                            RuleConflictDetail(
                                rule_id=r.id,
                                rule_title=r.title,
                                tier=r.tier.upper(),
                                reason=(
                                    f"Proposed preference conflicts with or weakens [{r.tier.upper()}] "
                                    f"{r.title} (collision probability={prob:.0%})."
                                ),
                                confidence=round(conf, 4),
                            )
                            for r, prob, conf in colliding_rules
                        ]
                        return RuleCollisionResult(
                            has_collision=True,
                            summary=f"Detected {len(conflicts)} collision(s) via gemini-decision-1.",
                            conflicts=conflicts,
                        )

                    # In hybrid mode, narrow higher_tier_rules_text to only the colliding rules for Stage 2 reason generation
                    higher_tier_rules_text = "\n\n".join(
                        f"--- Rule: {r.id} ({r.title}) [Tier: {r.tier.upper()}] [Collision Probability: {prob:.0%}] ---\n{r.content}"
                        for r, prob, _ in colliding_rules
                    )
            except Exception:
                # Fall through to generative check if decision endpoint is unavailable
                pass

        prompt = (
            "You are an authoritative AI rules governance auditor. Evaluate whether the following "
            "proposed developer personal preference semantically conflicts with, relaxes, carves out exceptions to, "
            "or weakens any higher-tier canonical rules (Tier 1 Organization or Tier 2 Team).\n\n"
            f"### CANONICAL HIGHER-TIER RULES:\n{higher_tier_rules_text}\n\n"
            f"### PROPOSED PERSONAL PREFERENCE:\n{clean_rule}\n\n"
            "Evaluation Criteria:\n"
            "1. Non-Weakening Invariant: Personal preferences may specialize workflows or add stricter requirements, "
            "but can NEVER contradict, weaken, bypass, or carve out exceptions to Organization or Team rules.\n"
            "2. If the proposed preference permits something a higher-tier rule forbids (e.g. hardcoding secrets, "
            "skipping branch protection/PRs, committing directly to main, suppressing mock warnings, skipping typed schemas), "
            "flag this as a collision (has_collision=true) and specify which rule is violated.\n"
            "3. If it is a benign personal habit, workflow preference, or stylistic choice that does not violate any "
            "higher-tier invariants, set has_collision=false and conflicts=[].\n"
            "4. Return strictly valid JSON adhering to the provided schema."
        )

        url, headers, payload = self._prepare_request(
            prompt, token, response_schema=RULE_COLLISION_SCHEMA
        )

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                candidate = resp_data.get("candidates", [{}])[0]
                text = (
                    candidate.get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "{}")
                )
                raw_json = json.loads(text)

                conflicts = [
                    RuleConflictDetail(
                        rule_id=str(c.get("rule_id", "")),
                        rule_title=str(c.get("rule_title", "")),
                        tier=str(c.get("tier", "")).upper(),
                        reason=str(c.get("reason", "")),
                    )
                    for c in raw_json.get("conflicts", [])
                ]

                return RuleCollisionResult(
                    has_collision=bool(raw_json.get("has_collision", False)) or len(conflicts) > 0,
                    summary=raw_json.get("summary", "Rule collision evaluation complete."),
                    conflicts=conflicts,
                )
        except Exception as e:
            return RuleCollisionResult(
                has_collision=False,
                summary=f"Collision check failed due to request error: {e}",
                degraded=True,
            )

    def review_diff(
        self,
        diff_text: str,
        rules_text: str,
        staged_files: Sequence[str] | None = None,
        dependency_context: str = "",
        matched_rules: Sequence[Rule] | None = None,
    ) -> ReviewResult:
        """Evaluate staged diff against rules using System 1 (`gemini-decision-1`) + System 2 (`gemini-3.8-flash`) hybrid pipeline."""
        review_t0 = time.perf_counter()
        if not diff_text.strip():
            return ReviewResult(
                passed=True,
                summary="No staged diff to review.",
                violations=[],
                evaluation_path="empty_diff",
                total_latency_ms=0.0,
            )

        clean_dep_context = dependency_context.strip()
        cache_key = self._compute_cache_key(diff_text, rules_text, clean_dep_context)
        cached = self._get_cached_result(cache_key)
        if cached is not None:
            cached.total_latency_ms = round((time.perf_counter() - review_t0) * 1000, 1)
            return cached

        token, err = self._resolve_auth()
        if err:
            return ReviewResult(
                passed=True,
                summary=err,
                violations=[],
                degraded=True,
                evaluation_path="skipped(no_auth)",
                total_latency_ms=round((time.perf_counter() - review_t0) * 1000, 1),
            )

        stage1_ms: float | None = None
        stage1_fallback_reason: str | None = None
        actual_stage1_model = (
            self.config.model
            if (
                self.config.backend == "vertex"
                and not self.config.api_key
                and self.config.decision_model == DEFAULT_DECISION_MODEL
            )
            else self.config.decision_model
        )

        # Stage 1 (System 1): Parallel Discriminative Gate via `gemini-decision-1`
        if matched_rules and self.config.review_mode in ("hybrid", "decision_only"):
            s1_t0 = time.perf_counter()
            flagged_evals, decision_failed = self.evaluate_rules_decision(
                diff_text=diff_text,
                matched_rules=matched_rules,
                staged_files=staged_files,
                dependency_context=clean_dep_context,
                token=token,
            )
            stage1_ms = round((time.perf_counter() - s1_t0) * 1000, 1)

            if not decision_failed:
                # Fast Path 1: Zero violations detected across all matched rules (~150-250ms)
                if not flagged_evals:
                    fast_pass = ReviewResult(
                        passed=True,
                        summary=f"All {len(matched_rules)} rule(s) passed via {actual_stage1_model}.",
                        violations=[],
                        evaluation_path=f"stage1_fast_pass({actual_stage1_model})",
                        stage1_model=actual_stage1_model,
                        stage1_latency_ms=stage1_ms,
                        total_latency_ms=round((time.perf_counter() - review_t0) * 1000, 1),
                    )
                    self._save_cached_result(cache_key, fast_pass)
                    return fast_pass

                has_hard_violation = any(
                    ev.severity in ("CRITICAL", "ERROR") for ev in flagged_evals
                )

                # Fast Path 2: Decision-only mode OR non-blocking advisories (WARN / INFO) in hybrid mode
                if self.config.review_mode == "decision_only" or not has_hard_violation:
                    decision_violations = self._build_decision_only_violations(
                        flagged_evals, staged_files=staged_files
                    )
                    path_label = (
                        f"stage1_decision_only({actual_stage1_model})"
                        if self.config.review_mode == "decision_only"
                        else f"stage1_advisory_pass({actual_stage1_model})"
                    )
                    result = ReviewResult(
                        passed=not has_hard_violation,
                        summary=(
                            f"Evaluated {len(matched_rules)} rule(s) via {actual_stage1_model}: "
                            f"{len(decision_violations)} violation(s) flagged."
                        ),
                        violations=decision_violations,
                        evaluation_path=path_label,
                        stage1_model=actual_stage1_model,
                        stage1_latency_ms=stage1_ms,
                        total_latency_ms=round((time.perf_counter() - review_t0) * 1000, 1),
                    )
                    self._save_cached_result(cache_key, result)
                    return result

                # Stage 2 (System 2) Escalation: Hard violation(s) detected in hybrid mode.
                # Call `gemini-3.8-flash` scoped strictly to the violated rules to generate code diffs & fixes.
                s2_t0 = time.perf_counter()
                stage2_result = self._generate_remediation_with_flash(
                    diff_text=diff_text,
                    rules_text=rules_text,
                    staged_files=staged_files,
                    clean_dep_context=clean_dep_context,
                    token=token,
                    flagged_evals=flagged_evals,
                )
                s2_ms = round((time.perf_counter() - s2_t0) * 1000, 1)
                if stage2_result is not None and stage2_result.violations:
                    stage2_result.evaluation_path = (
                        f"hybrid_escalated({actual_stage1_model}->{self.config.model})"
                    )
                    stage2_result.stage1_model = actual_stage1_model
                    stage2_result.stage2_model = self.config.model
                    stage2_result.stage1_latency_ms = stage1_ms
                    stage2_result.stage2_latency_ms = s2_ms
                    stage2_result.total_latency_ms = round(
                        (time.perf_counter() - review_t0) * 1000, 1
                    )
                    self._save_cached_result(cache_key, stage2_result)
                    return stage2_result

                # Fallback to Stage 1 deterministic violations if Stage 2 fails or returns empty
                fallback_violations = self._build_decision_only_violations(
                    flagged_evals, staged_files=staged_files
                )
                fallback_result = ReviewResult(
                    passed=False,
                    summary=(
                        f"Commit blocked by {actual_stage1_model} "
                        f"({len(fallback_violations)} violation(s) detected)."
                    ),
                    violations=fallback_violations,
                    evaluation_path=(
                        f"stage1_fallback_after_stage2_error({actual_stage1_model})"
                    ),
                    stage1_model=actual_stage1_model,
                    stage2_model=self.config.model,
                    stage1_latency_ms=stage1_ms,
                    stage2_latency_ms=s2_ms,
                    total_latency_ms=round((time.perf_counter() - review_t0) * 1000, 1),
                )
                self._save_cached_result(cache_key, fallback_result)
                return fallback_result

            stage1_fallback_reason = (
                "skipped_stage1_no_api_key_or_scope"
                if not self.config.api_key
                else "skipped_stage1_error"
            )

        # Standard Generative Path (`generative_only` or fallback when `matched_rules` not supplied or Stage 1 failed)
        s2_t0 = time.perf_counter()
        gen_result = self._generate_remediation_with_flash(
            diff_text=diff_text,
            rules_text=rules_text,
            staged_files=staged_files,
            clean_dep_context=clean_dep_context,
            token=token,
            flagged_evals=None,
        )
        s2_ms = round((time.perf_counter() - s2_t0) * 1000, 1)
        if gen_result is not None:
            if stage1_fallback_reason:
                gen_result.evaluation_path = (
                    f"stage2_fallback({stage1_fallback_reason}->{self.config.model})"
                )
                gen_result.stage1_model = actual_stage1_model
            else:
                gen_result.evaluation_path = f"generative_only({self.config.model})"
            gen_result.stage2_model = self.config.model
            gen_result.stage1_latency_ms = stage1_ms
            gen_result.stage2_latency_ms = s2_ms
            gen_result.total_latency_ms = round((time.perf_counter() - review_t0) * 1000, 1)
            self._save_cached_result(cache_key, gen_result)
            return gen_result

        return ReviewResult(
            passed=True,
            summary="Review skipped: API call encountered an error. Graceful fallback enabled.",
            violations=[],
            degraded=True,
            evaluation_path=f"skipped(api_error:{self.config.model})",
            stage1_model=actual_stage1_model if stage1_ms is not None else None,
            stage2_model=self.config.model,
            stage1_latency_ms=stage1_ms,
            stage2_latency_ms=s2_ms,
            total_latency_ms=round((time.perf_counter() - review_t0) * 1000, 1),
        )
