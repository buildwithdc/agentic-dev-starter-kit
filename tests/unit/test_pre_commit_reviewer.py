#!/usr/bin/env python3
"""Unit tests for pre-commit reviewer and Gemini ADC client."""

import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.dependency_scanner import DependencyContext, DependencySnippet
from scripts.gemini_client import (
    DEFAULT_LOCATION,
    DEFAULT_MODEL,
    GeminiClientConfig,
    GeminiReviewClient,
    ReviewResult,
    ReviewViolation,
    discover_gcp_project_id,
    get_adc_access_token,
)
from scripts.pre_commit_reviewer import (
    get_current_commit_hash,
    is_git_rebasing,
    log_audit_entry,
    print_review_report,
    run_review,
)
from scripts.rule_loader import Rule


class TestGeminiADCClientAndReviewer(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.temp_dir.name) / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_config_from_env(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GEMINI_BACKEND": "google_ai",
                "GEMINI_MODEL": "gemini-1.5-pro",
                "GOOGLE_CLOUD_PROJECT": "my-test-project",
                "GOOGLE_CLOUD_LOCATION": "asia-east1",
            },
        ):
            cfg = GeminiClientConfig.from_env()
            self.assertEqual(cfg.backend, "google_ai")
            self.assertEqual(cfg.model, "gemini-1.5-pro")
            self.assertEqual(cfg.project_id, "my-test-project")
            self.assertEqual(cfg.location, "asia-east1")

    def test_project_id_discovery_from_env(self) -> None:
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "env-project-123"}):
            self.assertEqual(discover_gcp_project_id(), "env-project-123")

    def test_adc_token_caching(self) -> None:
        cache_file = self.cache_dir / "adc_token.json"
        cache_file.write_text(
            json.dumps({"token": "cached-token-abc", "expires_at": time.time() + 1000}),
            encoding="utf-8",
        )
        token = get_adc_access_token(self.cache_dir)
        self.assertEqual(token, "cached-token-abc")

    def test_client_degraded_when_no_adc_token(self) -> None:
        with patch("scripts.gemini_client.get_adc_access_token", return_value=None):
            cfg = GeminiClientConfig(backend="vertex", project_id="test-proj")
            client = GeminiReviewClient(config=cfg, cache_dir=self.cache_dir)
            res = client.review_diff(
                diff_text="+ print('hello')",
                rules_text="No plaintext secrets",
                staged_files=["main.py"],
            )
            self.assertTrue(res.passed)
            self.assertTrue(res.degraded)
            self.assertIn("Google ADC access token not found", res.summary)

    def test_vertex_ai_request_preparation(self) -> None:
        cfg = GeminiClientConfig(
            backend="vertex",
            model=DEFAULT_MODEL,
            project_id="test-proj-456",
            location="us-central1",
        )
        client = GeminiReviewClient(config=cfg, cache_dir=self.cache_dir)
        url, headers, payload = client._prepare_request(
            prompt="check diff", token="mock-bearer-token"
        )
        self.assertIn("us-central1-aiplatform.googleapis.com", url)
        self.assertIn("projects/test-proj-456", url)
        self.assertIn(f"models/{DEFAULT_MODEL}:generateContent", url)
        self.assertEqual(headers.get("Authorization"), "Bearer mock-bearer-token")

    def test_vertex_ai_global_request_preparation(self) -> None:
        cfg = GeminiClientConfig(
            backend="vertex",
            model=DEFAULT_MODEL,
            project_id="test-proj-456",
            location="global",
        )
        client = GeminiReviewClient(config=cfg, cache_dir=self.cache_dir)
        url, headers, payload = client._prepare_request(
            prompt="check diff", token="mock-bearer-token"
        )
        self.assertEqual(
            url,
            f"https://aiplatform.googleapis.com/v1/projects/test-proj-456/locations/global/publishers/google/models/{DEFAULT_MODEL}:generateContent",
        )
        self.assertEqual(headers.get("Authorization"), "Bearer mock-bearer-token")

    def test_google_ai_request_preparation_with_adc(self) -> None:
        cfg = GeminiClientConfig(
            backend="google_ai",
            model=DEFAULT_MODEL,
            project_id="test-proj-456",
        )
        client = GeminiReviewClient(config=cfg, cache_dir=self.cache_dir)
        url, headers, payload = client._prepare_request(
            prompt="check diff", token="mock-bearer-token"
        )
        self.assertIn("generativelanguage.googleapis.com", url)
        self.assertIn(f"models/{DEFAULT_MODEL}:generateContent", url)
        self.assertEqual(headers.get("Authorization"), "Bearer mock-bearer-token")
        self.assertEqual(headers.get("x-goog-user-project"), "test-proj-456")

    def test_default_config_values(self) -> None:
        cfg = GeminiClientConfig()
        self.assertEqual(cfg.model, DEFAULT_MODEL)
        self.assertEqual(cfg.location, DEFAULT_LOCATION)
        self.assertEqual(cfg.timeout, 30.0)

    def test_auth_fallback_to_adc_when_api_key_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            cfg = GeminiClientConfig.from_env()
            self.assertEqual(cfg.backend, "vertex")
            self.assertIsNone(cfg.api_key)

        with patch("scripts.gemini_client.get_adc_access_token", return_value="mock-adc-token"), patch(
            "urllib.request.urlopen"
        ) as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(
                {"candidates": [{"content": {"parts": [{"text": json.dumps({"passed": True, "violations": []})}]}}]}
            ).encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp

            cfg = GeminiClientConfig(backend="vertex", project_id="test-proj")
            client = GeminiReviewClient(config=cfg, cache_dir=self.cache_dir)
            res = client.review_diff(
                diff_text="+ def foo(): pass",
                rules_text="No rules broken",
                staged_files=["app.py"],
            )
            self.assertTrue(res.passed)
            self.assertFalse(res.degraded)

    def test_auth_uses_api_key_when_provided(self) -> None:
        with patch.dict(os.environ, {"GEMINI_API_KEY": "secret-key-123"}, clear=True):
            cfg = GeminiClientConfig.from_env()
            self.assertEqual(cfg.backend, "google_ai")
            self.assertEqual(cfg.api_key, "secret-key-123")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(
                {"candidates": [{"content": {"parts": [{"text": json.dumps({"passed": True, "violations": []})}]}}]}
            ).encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp

            cfg = GeminiClientConfig(backend="google_ai", api_key="secret-key-123")
            client = GeminiReviewClient(config=cfg, cache_dir=self.cache_dir)
            res = client.review_diff(
                diff_text="+ def foo(): pass",
                rules_text="No rules broken",
                staged_files=["app.py"],
            )
            self.assertTrue(res.passed)
            self.assertFalse(res.degraded)

    def test_client_diff_caching(self) -> None:
        cfg = GeminiClientConfig(backend="vertex", project_id="test-proj")
        client = GeminiReviewClient(config=cfg, cache_dir=self.cache_dir)
        mock_result = ReviewResult(
            passed=True,
            summary="All good",
            violations=[],
        )
        cache_key = client._compute_cache_key("diff-123", "rules-123")
        client._save_cached_result(cache_key, mock_result)

        cached = client._get_cached_result(cache_key)
        self.assertIsNotNone(cached)
        self.assertTrue(cached.passed)
        self.assertTrue(cached.cached)

        # Verify dependency context alters cache key
        key_with_deps = client._compute_cache_key("diff-123", "rules-123", "upstream snippet")
        self.assertNotEqual(cache_key, key_with_deps)

    @patch("scripts.pre_commit_reviewer.is_git_rebasing", return_value=False)
    @patch("scripts.pre_commit_reviewer.GeminiReviewClient")
    @patch("scripts.pre_commit_reviewer.load_rules")
    @patch("scripts.pre_commit_reviewer.get_staged_diff")
    @patch("scripts.pre_commit_reviewer.get_staged_files")
    def test_run_review_pass(self, mock_files, mock_diff, mock_load, mock_client_cls, mock_rebasing) -> None:
        mock_files.return_value = ["src/main.py"]
        mock_diff.return_value = "+ def hello(): pass"
        rule = Rule(
            id="rule-code-quality",
            title="Code Quality",
            severity_default="WARN",
            applies_to=["**/*.py"],
            content="Write typed code.",
        )
        mock_load.return_value = [rule]

        mock_instance = MagicMock()
        mock_instance.review_diff.return_value = ReviewResult(
            passed=True,
            summary="Pass",
            violations=[],
            degraded=False,
        )
        mock_client_cls.return_value = mock_instance

        exit_code = run_review()
        self.assertEqual(exit_code, 0)

    @patch("scripts.pre_commit_reviewer.is_git_rebasing", return_value=False)
    @patch("scripts.pre_commit_reviewer.GeminiReviewClient")
    @patch("scripts.pre_commit_reviewer.load_rules")
    @patch("scripts.pre_commit_reviewer.get_staged_diff")
    @patch("scripts.pre_commit_reviewer.get_staged_files")
    def test_run_review_warn_is_non_blocking(self, mock_files, mock_diff, mock_load, mock_client_cls, mock_rebasing) -> None:
        mock_files.return_value = ["src/main.py"]
        mock_diff.return_value = "+ def hello(): pass"
        rule = Rule(
            id="rule-code-quality",
            title="Code Quality",
            severity_default="WARN",
            applies_to=["**/*.py"],
            content="Write typed code.",
        )
        mock_load.return_value = [rule]

        mock_instance = MagicMock()
        mock_instance.review_diff.return_value = ReviewResult(
            passed=True,
            summary="Found warnings",
            violations=[
                ReviewViolation(
                    file="src/main.py",
                    rule_title="Code Quality",
                    severity="WARN",
                    issue="Missing type annotation",
                    suggested_fix="Add -> None",
                )
            ],
            degraded=False,
        )
        mock_client_cls.return_value = mock_instance

        exit_code = run_review()
        self.assertEqual(exit_code, 0)

    @patch("scripts.pre_commit_reviewer.is_git_rebasing", return_value=False)
    @patch("scripts.pre_commit_reviewer.GeminiReviewClient")
    @patch("scripts.pre_commit_reviewer.load_rules")
    @patch("scripts.pre_commit_reviewer.get_staged_diff")
    @patch("scripts.pre_commit_reviewer.get_staged_files")
    def test_run_review_error_and_critical_block_commit(self, mock_files, mock_diff, mock_load, mock_client_cls, mock_rebasing) -> None:
        mock_files.return_value = ["src/main.py"]
        mock_diff.return_value = "+ API_KEY = 'secret'"
        rule = Rule(
            id="rule-secrets",
            title="Security",
            severity_default="CRITICAL",
            applies_to=["**/*"],
            content="No secrets.",
        )
        mock_load.return_value = [rule]

        mock_instance = MagicMock()
        mock_instance.review_diff.return_value = ReviewResult(
            passed=False,
            summary="Found critical violation",
            violations=[
                ReviewViolation(
                    file="src/main.py",
                    rule_title="Security",
                    severity="CRITICAL",
                    issue="Hardcoded API key",
                    suggested_fix="Use os.getenv",
                )
            ],
            degraded=False,
        )
        mock_client_cls.return_value = mock_instance

        exit_code = run_review()
        self.assertEqual(exit_code, 1)

    def test_rebase_detection_and_bypass_env(self) -> None:
        with patch.dict(os.environ, {"SKIP_LLM_HOOK": "1"}):
            self.assertEqual(run_review(skip_llm=False), 0)

        with patch.dict(os.environ, {"GIT_REFLOG_ACTION": "rebase (finish)"}):
            self.assertTrue(is_git_rebasing())
            self.assertEqual(run_review(), 0)

    def test_get_current_commit_hash(self) -> None:
        hash_val = get_current_commit_hash()
        self.assertIsInstance(hash_val, str)
        self.assertTrue(len(hash_val) > 0)

    def test_log_audit_entry(self) -> None:
        audit_file = Path(self.temp_dir.name) / "test_audit.log"
        log_audit_entry(
            "PASSED",
            commit_hash="abcdef123456",
            audit_file=audit_file,
            eval_path="stage1_fast_pass(gemini-decision-1)",
            latency_ms=184.5,
            stage_details="dep_scan=12ms, stage1=170ms",
        )
        self.assertTrue(audit_file.exists())
        content = audit_file.read_text(encoding="utf-8")
        self.assertIn("commit: abcdef123456", content)
        self.assertIn("decision: PASSED", content)
        self.assertIn("path: stage1_fast_pass(gemini-decision-1)", content)
        self.assertIn("latency_ms: 184.5", content)
        self.assertIn("dep_scan=12ms, stage1=170ms", content)

    @patch("scripts.pre_commit_reviewer.is_git_rebasing", return_value=False)
    @patch("scripts.pre_commit_reviewer.GeminiReviewClient")
    @patch("scripts.pre_commit_reviewer.load_rules")
    @patch("scripts.pre_commit_reviewer.get_staged_diff")
    @patch("scripts.pre_commit_reviewer.get_staged_files")
    def test_run_review_writes_audit_log(self, mock_files, mock_diff, mock_load, mock_client_cls, mock_rebasing) -> None:
        audit_file = Path(self.temp_dir.name) / "review_audit.log"
        mock_files.return_value = ["src/main.py"]
        mock_diff.return_value = "+ def hello(): pass"
        rule = Rule(
            id="rule-code-quality",
            title="Code Quality",
            severity_default="WARN",
            applies_to=["**/*.py"],
            content="Write typed code.",
        )
        mock_load.return_value = [rule]
        mock_instance = MagicMock()
        mock_instance.review_diff.return_value = ReviewResult(
            passed=True,
            summary="Pass",
            violations=[],
            degraded=False,
        )
        mock_client_cls.return_value = mock_instance

        exit_code = run_review(audit_file=audit_file)
        self.assertEqual(exit_code, 0)
        self.assertTrue(audit_file.exists())
        content = audit_file.read_text(encoding="utf-8")
        self.assertIn("decision: PASSED", content)
        self.assertIn("commit: ", content)

    @patch("scripts.pre_commit_reviewer.run_local_static_fallback", return_value=True)
    @patch("scripts.pre_commit_reviewer.is_git_rebasing", return_value=False)
    @patch("scripts.pre_commit_reviewer.GeminiReviewClient")
    @patch("scripts.pre_commit_reviewer.load_rules")
    @patch("scripts.pre_commit_reviewer.get_staged_diff")
    @patch("scripts.pre_commit_reviewer.get_staged_files")
    def test_run_review_degraded_no_auth_logs_skipped(
        self, mock_files, mock_diff, mock_load, mock_client_cls, mock_rebasing, mock_fallback
    ) -> None:
        audit_file = Path(self.temp_dir.name) / "degraded_audit.log"
        mock_files.return_value = ["src/main.py"]
        mock_diff.return_value = "+ def hello(): pass"
        rule = Rule(
            id="rule-code-quality",
            title="Code Quality",
            severity_default="WARN",
            applies_to=["**/*.py"],
            content="Write typed code.",
        )
        mock_load.return_value = [rule]
        mock_instance = MagicMock()
        mock_instance.review_diff.return_value = ReviewResult(
            passed=True,
            summary="Authentication skipped: Google ADC access token not found.",
            violations=[],
            degraded=True,
            evaluation_path="skipped(no_auth)",
            total_latency_ms=3.2,
        )
        mock_client_cls.return_value = mock_instance

        exit_code = run_review(audit_file=audit_file)
        self.assertEqual(exit_code, 0)
        content = audit_file.read_text(encoding="utf-8")
        self.assertIn("decision: SKIPPED (NO_AUTH)", content)
        self.assertIn("path: skipped(no_auth)", content)
        self.assertNotIn("decision: PASSED", content)

    @patch("urllib.request.urlopen")
    def test_check_rule_collision_detects_conflict(self, mock_urlopen) -> None:
        inner_json = json.dumps({
            "has_collision": True,
            "summary": "Proposed rule conflicts with zero-hardcoded-secrets policy.",
            "conflicts": [
                {
                    "rule_id": "org-02-security-and-secrets",
                    "rule_title": "Zero Hardcoded Credentials",
                    "tier": "ORG",
                    "reason": "Allows committing mock API keys, which violates enterprise policy.",
                }
            ],
        })
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "candidates": [{"content": {"parts": [{"text": inner_json}]}}]
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        cfg = GeminiClientConfig(backend="google_ai", api_key="test-key")
        client = GeminiReviewClient(config=cfg, cache_dir=self.cache_dir)
        res = client.check_rule_collision(
            proposed_rule="Allow hardcoded mock API tokens in test scripts",
            higher_tier_rules_text="Never commit secrets or tokens.",
        )
        self.assertTrue(res.has_collision)
        self.assertEqual(len(res.conflicts), 1)
        self.assertEqual(res.conflicts[0].rule_id, "org-02-security-and-secrets")
        self.assertEqual(res.conflicts[0].tier, "ORG")

    @patch("urllib.request.urlopen")
    def test_check_rule_collision_clean(self, mock_urlopen) -> None:
        inner_json = json.dumps({
            "has_collision": False,
            "summary": "No conflicts found.",
            "conflicts": [],
        })
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "candidates": [{"content": {"parts": [{"text": inner_json}]}}]
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        cfg = GeminiClientConfig(backend="google_ai", api_key="test-key")
        client = GeminiReviewClient(config=cfg, cache_dir=self.cache_dir)
        res = client.check_rule_collision(
            proposed_rule="Always prefer f-strings over percent formatting",
            higher_tier_rules_text="Code standards.",
        )
        self.assertFalse(res.has_collision)
        self.assertEqual(len(res.conflicts), 0)

    def test_print_review_report_shows_truncated_chars(self) -> None:
        dep_ctx = DependencyContext(
            max_depth=2,
            snippets=[
                DependencySnippet(
                    file="pkg/contracts.py",
                    direction="upstream",
                    degree=1,
                    symbol="UserRecord",
                    source_file="pkg/service.py",
                    line_start=1,
                    line_end=10,
                    snippet="1: class UserRecord: pass",
                )
            ],
            truncated=True,
            truncated_chars=1250,
        )
        res = ReviewResult(passed=True, summary="All good", violations=[])
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_review_report(res, dep_context=dep_ctx)
        output = buf.getvalue()
        self.assertIn("[truncated to budget: 1,250 chars removed]", output)

    @patch.dict("os.environ", {"GEMINI_API_KEY": "your_gemini_api_key_here"}, clear=True)
    def test_placeholder_api_key_is_ignored_and_defaults_to_vertex_adc(self) -> None:
        cfg = GeminiClientConfig.from_env()
        self.assertIsNone(cfg.api_key)
        self.assertEqual(cfg.backend, "vertex")

    @patch("urllib.request.urlopen")
    def test_hybrid_stage1_fast_pass_skips_stage2(self, mock_urlopen) -> None:
        """When gemini-decision-1 finds zero violations, Stage 2 (gemini-3.8-flash) is never called."""
        decision_resp = MagicMock()
        decision_resp.read.return_value = json.dumps(
            {
                "answers": {
                    "org-02-security-and-secrets": {
                        "type": "score",
                        "score": 0.05,
                        "probabilities": {"0": 0.95, "1": 0.03, "2": 0.01, "3": 0.01, "4": 0.0},
                        "confidence": 0.95,
                    }
                }
            }
        ).encode("utf-8")
        decision_resp.__enter__.return_value = decision_resp
        mock_urlopen.return_value = decision_resp

        rule = Rule(
            id="org-02-security-and-secrets",
            title="Security Policy & Secret Sanitization",
            severity_default="CRITICAL",
            tier="org",
            applies_to=["**/*"],
            content="Never commit hardcoded credentials.",
        )

        cfg = GeminiClientConfig(backend="google_ai", api_key="test-key", review_mode="hybrid")
        client = GeminiReviewClient(config=cfg, cache_dir=self.cache_dir)
        res = client.review_diff(
            diff_text="+ x = 42",
            rules_text="rule text",
            staged_files=["app.py"],
            matched_rules=[rule],
        )

        self.assertTrue(res.passed)
        self.assertEqual(len(res.violations), 0)
        self.assertIn("gemini-decision-1", res.summary)
        # Only 1 HTTP call (to /v1beta/decisions), 0 calls to :generateContent
        self.assertEqual(mock_urlopen.call_count, 1)
        req_arg = mock_urlopen.call_args[0][0]
        self.assertIn("/v1beta/decisions", req_arg.full_url)

    @patch("urllib.request.urlopen")
    def test_hybrid_stage1_warn_only_returns_immediately_without_stage2(self, mock_urlopen) -> None:
        """When gemini-decision-1 flags only WARN/INFO advisories in hybrid mode, it returns immediately."""
        decision_resp = MagicMock()
        decision_resp.read.return_value = json.dumps(
            {
                "answers": {
                    "team-02-code-quality": {
                        "type": "score",
                        "score": 2.0,
                        "probabilities": {"0": 0.10, "1": 0.05, "2": 0.80, "3": 0.05, "4": 0.0},
                        "confidence": 0.82,
                    }
                }
            }
        ).encode("utf-8")
        decision_resp.__enter__.return_value = decision_resp
        mock_urlopen.return_value = decision_resp

        rule = Rule(
            id="team-02-code-quality",
            title="Code Quality & Typing",
            severity_default="WARN",
            tier="team",
            applies_to=["**/*.py"],
            content="- Add type annotations to functions.",
        )

        cfg = GeminiClientConfig(backend="google_ai", api_key="test-key", review_mode="hybrid")
        client = GeminiReviewClient(config=cfg, cache_dir=self.cache_dir)
        res = client.review_diff(
            diff_text="+ def helper(x): return x",
            rules_text="rule text",
            staged_files=["utils.py"],
            matched_rules=[rule],
        )

        self.assertTrue(res.passed)
        self.assertEqual(len(res.violations), 1)
        self.assertEqual(res.violations[0].severity, "WARN")
        self.assertEqual(res.violations[0].rule_id, "team-02-code-quality")
        self.assertAlmostEqual(res.violations[0].confidence or 0.0, 0.82)
        self.assertEqual(mock_urlopen.call_count, 1)

    @patch("urllib.request.urlopen")
    def test_hybrid_stage1_critical_escalates_to_stage2_flash_for_remediation(self, mock_urlopen) -> None:
        """When gemini-decision-1 flags CRITICAL/ERROR, Stage 2 (gemini-3.8-flash) generates code diffs."""
        decision_resp = MagicMock()
        decision_resp.read.return_value = json.dumps(
            {
                "answers": {
                    "org-02-security-and-secrets": {
                        "type": "score",
                        "score": 3.85,
                        "probabilities": {"0": 0.02, "1": 0.0, "2": 0.03, "3": 0.05, "4": 0.90},
                        "confidence": 0.93,
                    },
                    "team-02-code-quality": {
                        "type": "score",
                        "score": 0.02,
                        "probabilities": {"0": 0.98, "1": 0.02, "2": 0.0, "3": 0.0, "4": 0.0},
                        "confidence": 0.98,
                    },
                }
            }
        ).encode("utf-8")
        decision_resp.__enter__.return_value = decision_resp

        flash_inner = json.dumps(
            {
                "passed": False,
                "summary": "Hardcoded API secret detected.",
                "violations": [
                    {
                        "file": "config.py",
                        "line_number": 12,
                        "rule_id": "org-02-security-and-secrets",
                        "rule_title": "Security Policy & Secret Sanitization",
                        "severity": "CRITICAL",
                        "issue": "Plaintext API token assigned to API_SECRET.",
                        "suggested_fix": "Load secret from environment variable via os.getenv('API_SECRET').",
                        "suggested_code_diff": "-API_SECRET = 'sk-live-123'\n+API_SECRET = os.getenv('API_SECRET')",
                    }
                ],
            }
        )
        flash_resp = MagicMock()
        flash_resp.read.return_value = json.dumps(
            {"candidates": [{"content": {"parts": [{"text": flash_inner}]}}]}
        ).encode("utf-8")
        flash_resp.__enter__.return_value = flash_resp

        mock_urlopen.side_effect = [decision_resp, flash_resp]

        org_rule = Rule(
            id="org-02-security-and-secrets",
            title="Security Policy & Secret Sanitization",
            severity_default="CRITICAL",
            tier="org",
            applies_to=["**/*"],
            content="Never commit hardcoded credentials.",
        )
        team_rule = Rule(
            id="team-02-code-quality",
            title="Code Quality & Typing",
            severity_default="WARN",
            tier="team",
            applies_to=["**/*.py"],
            content="Use type annotations.",
        )

        cfg = GeminiClientConfig(backend="google_ai", api_key="test-key", review_mode="hybrid")
        client = GeminiReviewClient(config=cfg, cache_dir=self.cache_dir)
        res = client.review_diff(
            diff_text="+ API_SECRET = 'sk-live-123'",
            rules_text="all rules text",
            staged_files=["config.py"],
            matched_rules=[org_rule, team_rule],
        )

        self.assertFalse(res.passed)
        self.assertEqual(len(res.violations), 1)
        v = res.violations[0]
        self.assertEqual(v.severity, "CRITICAL")
        self.assertEqual(v.line_number, 12)
        self.assertIn("os.getenv", v.suggested_code_diff)
        self.assertAlmostEqual(v.confidence or 0.0, 0.93)
        self.assertEqual(mock_urlopen.call_count, 2)

        # Verify Stage 2 prompt only included the flagged org-02 rule, not the compliant team-02 rule
        stage2_req = mock_urlopen.call_args_list[1][0][0]
        stage2_body = json.loads(stage2_req.data.decode("utf-8"))
        stage2_prompt = stage2_body["contents"][0]["parts"][0]["text"]
        self.assertIn("org-02-security-and-secrets", stage2_prompt)
        self.assertNotIn("team-02-code-quality", stage2_prompt)

    @patch("urllib.request.urlopen")
    def test_hybrid_stage2_failure_falls_back_to_stage1_blocking_violations(self, mock_urlopen) -> None:
        """If Stage 2 (gemini-3.8-flash) errors out, Stage 1's CRITICAL violation still blocks the commit."""
        decision_resp = MagicMock()
        decision_resp.read.return_value = json.dumps(
            {
                "answers": {
                    "org-02-security-and-secrets": {
                        "type": "score",
                        "score": 3.9,
                        "probabilities": {"0": 0.01, "1": 0.0, "2": 0.0, "3": 0.09, "4": 0.90},
                        "confidence": 0.95,
                    }
                }
            }
        ).encode("utf-8")
        decision_resp.__enter__.return_value = decision_resp

        mock_urlopen.side_effect = [decision_resp, TimeoutError("Stage 2 timed out")]

        org_rule = Rule(
            id="org-02-security-and-secrets",
            title="Security Policy & Secret Sanitization",
            severity_default="CRITICAL",
            tier="org",
            applies_to=["**/*"],
            content="- Plaintext API tokens are strictly prohibited.",
        )

        cfg = GeminiClientConfig(backend="google_ai", api_key="test-key", review_mode="hybrid")
        client = GeminiReviewClient(config=cfg, cache_dir=self.cache_dir)
        res = client.review_diff(
            diff_text="+ API_SECRET = 'sk-live-123'",
            rules_text="all rules text",
            staged_files=["config.py"],
            matched_rules=[org_rule],
        )

        self.assertFalse(res.passed)
        self.assertEqual(len(res.violations), 1)
        self.assertEqual(res.violations[0].severity, "CRITICAL")
        self.assertEqual(res.violations[0].rule_id, "org-02-security-and-secrets")

    @patch("urllib.request.urlopen")
    def test_check_rule_collision_with_decision_noul_fast_pass(self, mock_urlopen) -> None:
        """When higher_rules are provided, check_rule_collision uses parallel noul questions and returns in 1 call if clean."""
        decision_resp = MagicMock()
        decision_resp.read.return_value = json.dumps(
            {
                "answers": {
                    "org-01-meta-guidelines": {"type": "noul", "noul": 0.03, "confidence": 0.97},
                    "org-02-security-and-secrets": {"type": "noul", "noul": 0.02, "confidence": 0.98},
                }
            }
        ).encode("utf-8")
        decision_resp.__enter__.return_value = decision_resp
        mock_urlopen.return_value = decision_resp

        higher_rules = [
            Rule(
                id="org-01-meta-guidelines",
                title="Branch Protection",
                severity_default="CRITICAL",
                tier="org",
                content="Never commit directly to main.",
            ),
            Rule(
                id="org-02-security-and-secrets",
                title="Zero Hardcoded Secrets",
                severity_default="CRITICAL",
                tier="org",
                content="Never hardcode API keys.",
            ),
        ]

        cfg = GeminiClientConfig(backend="google_ai", api_key="test-key", review_mode="hybrid")
        client = GeminiReviewClient(config=cfg, cache_dir=self.cache_dir)
        res = client.check_rule_collision(
            proposed_rule="Prefer pathlib.Path over os.path",
            higher_tier_rules_text="rules summary",
            higher_rules=higher_rules,
        )

        self.assertFalse(res.has_collision)
        self.assertEqual(len(res.conflicts), 0)
        self.assertEqual(mock_urlopen.call_count, 1)

    @patch("urllib.request.urlopen")
    def test_default_review_mode_is_decision_only_no_explain(self, mock_urlopen) -> None:
        """By default (decision_only), even CRITICAL violations return immediately from Stage 1 without calling Stage 2."""
        cfg = GeminiClientConfig()
        self.assertEqual(cfg.review_mode, "decision_only")

        decision_resp = MagicMock()
        decision_resp.read.return_value = json.dumps(
            {
                "answers": {
                    "org-02-security-and-secrets": {
                        "type": "score",
                        "score": 3.9,
                        "probabilities": {"0": 0.01, "1": 0.0, "2": 0.0, "3": 0.09, "4": 0.90},
                        "confidence": 0.95,
                    }
                }
            }
        ).encode("utf-8")
        decision_resp.__enter__.return_value = decision_resp
        mock_urlopen.return_value = decision_resp

        org_rule = Rule(
            id="org-02-security-and-secrets",
            title="Security Policy & Secret Sanitization",
            severity_default="CRITICAL",
            tier="org",
            applies_to=["**/*"],
            content="- Plaintext API tokens are strictly prohibited.",
        )

        client = GeminiReviewClient(
            config=GeminiClientConfig(backend="google_ai", api_key="test-key"),
            cache_dir=self.cache_dir,
        )
        res = client.review_diff(
            diff_text="+ API_SECRET = 'sk-live-123'",
            rules_text="all rules text",
            staged_files=["config.py"],
            matched_rules=[org_rule],
        )

        self.assertFalse(res.passed)
        self.assertEqual(len(res.violations), 1)
        self.assertEqual(res.violations[0].severity, "CRITICAL")
        self.assertEqual(res.evaluation_path, "stage1_decision_only(gemini-decision-1)")
        # Only 1 HTTP call (Stage 1 decision model); Stage 2 generative explain is NOT called
        self.assertEqual(mock_urlopen.call_count, 1)


if __name__ == "__main__":
    unittest.main()

