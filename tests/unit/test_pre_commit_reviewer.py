#!/usr/bin/env python3
"""Unit tests for pre-commit reviewer and Gemini ADC client."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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
        log_audit_entry("PASSED", commit_hash="abcdef123456", audit_file=audit_file)
        self.assertTrue(audit_file.exists())
        content = audit_file.read_text(encoding="utf-8")
        self.assertIn("commit: abcdef123456", content)
        self.assertIn("decision: PASSED", content)

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


if __name__ == "__main__":
    unittest.main()

