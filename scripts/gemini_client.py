#!/usr/bin/env python3
"""Configurable Gemini Review Client supporting Google Application Default Credentials (ADC) and Vertex AI."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

# Environment Variable Configuration Keys
ENV_BACKEND = "GEMINI_BACKEND"
ENV_MODEL = "GEMINI_MODEL"
ENV_PROJECT = "GOOGLE_CLOUD_PROJECT"
ENV_PROJECT_ALT = "GCP_PROJECT"
ENV_LOCATION = "GOOGLE_CLOUD_LOCATION"
ENV_LOCATION_ALT = "GCP_REGION"
ENV_TIMEOUT = "GEMINI_TIMEOUT"
ENV_API_KEY = "GEMINI_API_KEY"

DEFAULT_BACKEND = "vertex"
DEFAULT_MODEL = "gemini-3.7-flash"
DEFAULT_LOCATION = "us-central1"
DEFAULT_TIMEOUT = 5.0

VERTEX_ENDPOINT_TEMPLATE = (
    "https://{location}-aiplatform.googleapis.com/v1/projects/{project_id}/"
    "locations/{location}/publishers/google/models/{model}:generateContent"
)
GOOGLE_AI_ENDPOINT_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

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


@dataclass
class GeminiClientConfig:
    """Runtime configuration for Gemini Client."""

    backend: str = DEFAULT_BACKEND
    model: str = DEFAULT_MODEL
    project_id: str | None = None
    location: str = DEFAULT_LOCATION
    timeout: float = DEFAULT_TIMEOUT
    api_key: str | None = None

    @classmethod
    def from_env(cls) -> GeminiClientConfig:
        """Create config populated from environment variables."""
        return cls(
            backend=os.getenv(ENV_BACKEND, DEFAULT_BACKEND).lower(),
            model=os.getenv(ENV_MODEL, DEFAULT_MODEL),
            project_id=os.getenv(ENV_PROJECT) or os.getenv(ENV_PROJECT_ALT),
            location=os.getenv(ENV_LOCATION) or os.getenv(ENV_LOCATION_ALT, DEFAULT_LOCATION),
            timeout=float(os.getenv(ENV_TIMEOUT, str(DEFAULT_TIMEOUT))),
            api_key=os.getenv(ENV_API_KEY),
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
    """Client for evaluating code diffs against best-practice rules via ADC."""

    def __init__(
        self,
        config: GeminiClientConfig | None = None,
        cache_dir: Path | str | None = None,
    ) -> None:
        self.config = config or GeminiClientConfig.from_env()
        self.cache_dir = Path(cache_dir) if cache_dir else Path(".git/.llm_cache")

        if not self.config.project_id:
            self.config.project_id = discover_gcp_project_id()

    def _compute_cache_key(self, diff_text: str, rules_summary: str) -> str:
        payload = f"{self.config.backend}:{self.config.model}:{self.config.project_id}\n{rules_summary}\n{diff_text}".encode(
            "utf-8"
        )
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

    def _prepare_request(
        self, prompt: str, token: str | None
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
                "response_schema": REVIEW_SCHEMA,
                "temperature": 0.1,
            },
        }

        headers: dict[str, str] = {"Content-Type": "application/json"}

        if self.config.backend == "vertex":
            location = self.config.location or DEFAULT_LOCATION
            project_id = self.config.project_id or "default"
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

    def review_diff(
        self,
        diff_text: str,
        rules_text: str,
        staged_files: Sequence[str] | None = None,
    ) -> ReviewResult:
        """Evaluate staged diff against rules using Gemini Flash via ADC."""
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

        # Resolve authentication
        token: str | None = None
        if not (self.config.backend == "google_ai" and self.config.api_key):
            token = get_adc_access_token(self.cache_dir)
            if not token:
                return ReviewResult(
                    passed=True,
                    summary=(
                        "Review skipped: Google ADC access token not found. "
                        "Run 'gcloud auth application-default login' to enable LLM pre-commit checks."
                    ),
                    violations=[],
                    degraded=True,
                )

        if self.config.backend == "vertex" and not self.config.project_id:
            return ReviewResult(
                passed=True,
                summary=(
                    "Review skipped: GCP Project ID could not be determined for Vertex AI. "
                    "Set GOOGLE_CLOUD_PROJECT or run 'gcloud config set project <PROJECT_ID>'."
                ),
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

        url, headers, payload = self._prepare_request(prompt, token)

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
