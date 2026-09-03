#!/usr/bin/env python3
"""Unit tests for pre-commit reviewer and Gemini client."""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from scripts.gemini_client import GeminiReviewClient, ReviewResult, ReviewViolation
from scripts.pre_commit_reviewer import is_git_rebasing, run_review
from scripts.rule_loader import Rule


class TestGeminiClientAndReviewer(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.temp_dir.name) / "cache"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_client_degraded_when_no_api_key(self) -> None:
        client = GeminiReviewClient(api_key="", cache_dir=self.cache_dir)
        res = client.review_diff(
            diff_text="+ print('hello')",
            rules_text="No plaintext secrets",
            staged_files=["main.py"],
        )
        self.assertTrue(res.passed)
        self.assertTrue(res.degraded)
        self.assertIn("GEMINI_API_KEY is unset", res.summary)

    def test_client_caching(self) -> None:
        client = GeminiReviewClient(api_key="fake-key", cache_dir=self.cache_dir)
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

    @patch("scripts.pre_commit_reviewer.GeminiReviewClient")
    @patch("scripts.pre_commit_reviewer.load_rules")
    @patch("scripts.pre_commit_reviewer.get_staged_diff")
    @patch("scripts.pre_commit_reviewer.get_staged_files")
    def test_run_review_pass(self, mock_files, mock_diff, mock_load, mock_client_cls) -> None:
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

    @patch("scripts.pre_commit_reviewer.GeminiReviewClient")
    @patch("scripts.pre_commit_reviewer.load_rules")
    @patch("scripts.pre_commit_reviewer.get_staged_diff")
    @patch("scripts.pre_commit_reviewer.get_staged_files")
    def test_run_review_warn_is_non_blocking(self, mock_files, mock_diff, mock_load, mock_client_cls) -> None:
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
        # Non-blocking -> exit 0
        self.assertEqual(exit_code, 0)

    @patch("scripts.pre_commit_reviewer.GeminiReviewClient")
    @patch("scripts.pre_commit_reviewer.load_rules")
    @patch("scripts.pre_commit_reviewer.get_staged_diff")
    @patch("scripts.pre_commit_reviewer.get_staged_files")
    def test_run_review_error_and_critical_block_commit(self, mock_files, mock_diff, mock_load, mock_client_cls) -> None:
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
        # Blocking -> exit 1
        self.assertEqual(exit_code, 1)

    def test_rebase_detection_and_bypass_env(self) -> None:
        with patch.dict(os.environ, {"SKIP_LLM_HOOK": "1"}):
            self.assertEqual(run_review(skip_llm=False), 0)

        with patch.dict(os.environ, {"GIT_REFLOG_ACTION": "rebase (finish)"}):
            self.assertTrue(is_git_rebasing())
            self.assertEqual(run_review(), 0)


if __name__ == "__main__":
    unittest.main()
