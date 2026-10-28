#!/usr/bin/env python3
"""Unit tests for N-degree bidirectional local dependency scanner."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.dependency_scanner import (
    is_test_file,
    resolve_dep_depth,
    resolve_max_dep_chars,
    scan_dependencies,
)


class TestDependencyScanner(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_is_test_file_filtering(self) -> None:
        self.assertTrue(is_test_file("tests/unit/test_foo.py"))
        self.assertTrue(is_test_file("src/auth/user_test.py"))
        self.assertTrue(is_test_file("src/ui/button.spec.ts"))
        self.assertTrue(is_test_file("pkg/server_test.go"))
        self.assertTrue(is_test_file("src/test/java/com/acme/CheckoutServiceTest.java"))
        self.assertTrue(is_test_file("com/acme/OrderIT.java"))
        self.assertFalse(is_test_file("scripts/rule_loader.py"))
        self.assertFalse(is_test_file("src/services/contest_manager.py"))
        self.assertFalse(is_test_file("src/main/java/com/acme/CheckoutService.java"))

    def test_env_and_explicit_resolution(self) -> None:
        with patch.dict(os.environ, {"GEMINI_DEP_DEPTH": "3", "GEMINI_MAX_DEP_CHARS": "12000"}):
            self.assertEqual(resolve_dep_depth(None), 3)
            self.assertEqual(resolve_max_dep_chars(None), 12000)
            self.assertEqual(resolve_dep_depth(1), 1)
            self.assertEqual(resolve_max_dep_chars(5000), 5000)

    def test_depth_zero_disables_scanning(self) -> None:
        (self.root / "a.py").write_text("def foo() -> int:\n    return 1\n", encoding="utf-8")
        diff = "+++ b/a.py\n@@ -1,2 +1,2 @@\n+def foo() -> int:\n"
        ctx = scan_dependencies(["a.py"], diff, root_dir=self.root, max_depth=0)
        self.assertEqual(ctx.max_depth, 0)
        self.assertEqual(len(ctx.snippets), 0)
        self.assertEqual(ctx.format_for_prompt(), "")

    def test_one_degree_bidirectional_python_and_test_exclusion(self) -> None:
        """Verifies 1-degree upstream & downstream resolution while excluding test files downstream."""
        (self.root / "pkg").mkdir(parents=True, exist_ok=True)
        (self.root / "tests" / "unit").mkdir(parents=True, exist_ok=True)

        # Upstream contract (1st degree upstream of service.py)
        (self.root / "pkg" / "contracts.py").write_text(
            "class UserContract:\n    user_id: str\n    role: str\n",
            encoding="utf-8",
        )
        # 2nd degree upstream (imported by contracts.py)
        (self.root / "pkg" / "base_types.py").write_text(
            "class RoleEnum:\n    ADMIN = 'admin'\n",
            encoding="utf-8",
        )
        # Re-write contracts.py to reference RoleEnum
        (self.root / "pkg" / "contracts.py").write_text(
            "from pkg.base_types import RoleEnum\n\nclass UserContract:\n    user_id: str\n    role: RoleEnum\n",
            encoding="utf-8",
        )

        # Staged file: pkg/service.py
        (self.root / "pkg" / "service.py").write_text(
            "from pkg.contracts import UserContract\n\n"
            "def validate_user(u: UserContract, strict: bool = True) -> bool:\n"
            "    return bool(u.user_id) and strict\n",
            encoding="utf-8",
        )

        # 1st degree downstream production caller: pkg/api.py
        (self.root / "pkg" / "api.py").write_text(
            "from pkg.service import validate_user\n\n"
            "def handle_request(payload) -> bool:\n"
            "    return validate_user(payload)\n",
            encoding="utf-8",
        )

        # 2nd degree downstream production caller: pkg/router.py
        (self.root / "pkg" / "router.py").write_text(
            "from pkg.api import handle_request\n\n"
            "def dispatch(req) -> bool:\n"
            "    return handle_request(req)\n",
            encoding="utf-8",
        )

        # Downstream unit test (should be excluded from downstream expansion)
        (self.root / "tests" / "unit" / "test_service.py").write_text(
            "from pkg.service import validate_user\n\n"
            "def test_validate() -> None:\n"
            "    assert validate_user(None)\n",
            encoding="utf-8",
        )

        diff_text = (
            "diff --git a/pkg/service.py b/pkg/service.py\n"
            "--- a/pkg/service.py\n"
            "+++ b/pkg/service.py\n"
            "@@ -1,4 +1,4 @@\n"
            " from pkg.contracts import UserContract\n\n"
            "-def validate_user(u: UserContract) -> bool:\n"
            "+def validate_user(u: UserContract, strict: bool = True) -> bool:\n"
            "     return bool(u.user_id) and strict\n"
        )

        # Scan with default max_depth=1
        ctx_d1 = scan_dependencies(
            staged_files=["pkg/service.py"],
            diff_text=diff_text,
            root_dir=self.root,
            max_depth=1,
        )
        files_d1 = ctx_d1.touched_files()
        self.assertIn("pkg/contracts.py", files_d1)
        self.assertIn("pkg/api.py", files_d1)
        self.assertNotIn("pkg/base_types.py", files_d1)
        self.assertNotIn("pkg/router.py", files_d1)
        self.assertNotIn("tests/unit/test_service.py", files_d1)

        # Scan with max_depth=2 -> should also pull in 2nd-degree upstream (base_types.py) and downstream (router.py)
        ctx_d2 = scan_dependencies(
            staged_files=["pkg/service.py"],
            diff_text=diff_text,
            root_dir=self.root,
            max_depth=2,
        )
        files_d2 = ctx_d2.touched_files()
        self.assertIn("pkg/contracts.py", files_d2)
        self.assertIn("pkg/api.py", files_d2)
        self.assertIn("pkg/base_types.py", files_d2)
        self.assertIn("pkg/router.py", files_d2)
        self.assertNotIn("tests/unit/test_service.py", files_d2)

    def test_git_index_vs_head_and_unstaged_neighbor_detection(self) -> None:
        """Verifies staged files are read from Git index, neighbors from HEAD, and unstaged neighbor edits are flagged."""
        subprocess.run(["git", "init"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=self.root, check=True, capture_output=True)

        core_file = self.root / "core.py"
        caller_file = self.root / "caller.py"

        core_file.write_text("def compute(x: int) -> int:\n    return x + 1\n", encoding="utf-8")
        caller_file.write_text(
            "from core import compute\n\ndef run() -> int:\n    return compute(10)\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "core.py", "caller.py"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.root, check=True, capture_output=True)

        # Modify core.py and stage it
        core_file.write_text("def compute(x: int, factor: int) -> int:\n    return (x + 1) * factor\n", encoding="utf-8")
        subprocess.run(["git", "add", "core.py"], cwd=self.root, check=True)

        # Modify caller.py in working tree WITHOUT staging it
        caller_file.write_text(
            "from core import compute\n\ndef run() -> int:\n    return compute(10, 2)\n",
            encoding="utf-8",
        )

        diff_res = subprocess.run(
            ["git", "diff", "--cached", "--unified=3"],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=True,
        )

        ctx = scan_dependencies(
            staged_files=["core.py"],
            diff_text=diff_res.stdout,
            root_dir=self.root,
            max_depth=1,
        )

        # Should flag caller.py as having unstaged working-tree edits, and snippet should reflect HEAD (`compute(10)`)
        self.assertIn("caller.py", ctx.unstaged_neighbor_files)
        caller_snippets = [s for s in ctx.snippets if s.file == "caller.py"]
        self.assertEqual(len(caller_snippets), 1)
        self.assertTrue(caller_snippets[0].has_unstaged_edits)
        self.assertIn("compute(10)", caller_snippets[0].snippet)
        prompt_text = ctx.format_for_prompt()
        self.assertIn("WARNING: HAS UNSTAGED WORKING-TREE EDITS", prompt_text)

    def test_typescript_bidirectional_and_multiline_imports(self) -> None:
        (self.root / "src" / "types").mkdir(parents=True, exist_ok=True)
        (self.root / "src" / "services").mkdir(parents=True, exist_ok=True)
        (self.root / "src" / "controllers").mkdir(parents=True, exist_ok=True)

        (self.root / "src" / "types" / "config.ts").write_text(
            "export interface ServerConfig {\n  port: number;\n  host: string;\n}\n",
            encoding="utf-8",
        )
        (self.root / "src" / "services" / "server.ts").write_text(
            "import {\n  type ServerConfig,\n} from '../types/config';\n\n"
            "export async function startServer(cfg: ServerConfig, strict: boolean = true): Promise<number> {\n"
            "  return strict ? cfg.port : 8080;\n"
            "}\n",
            encoding="utf-8",
        )
        (self.root / "src" / "controllers" / "app.ts").write_text(
            "import { startServer } from '@/services/server';\n\n"
            "export async function bootApp(): Promise<number> {\n"
            "  return await startServer({ port: 3000, host: 'localhost' });\n"
            "}\n",
            encoding="utf-8",
        )
        (self.root / "src" / "controllers" / "main.ts").write_text(
            "import { bootApp } from './app';\n\n"
            "export function runCli(): void {\n"
            "  bootApp();\n"
            "}\n",
            encoding="utf-8",
        )
        (self.root / "src" / "services" / "server.spec.ts").write_text(
            "import { startServer } from './server';\nstartServer({ port: 80, host: '0.0.0.0' });\n",
            encoding="utf-8",
        )

        diff = (
            "+++ b/src/services/server.ts\n"
            "@@ -4,3 +4,3 @@\n"
            "-export async function startServer(cfg: ServerConfig): Promise<number> {\n"
            "+export async function startServer(cfg: ServerConfig, strict: boolean = true): Promise<number> {\n"
        )
        ctx_d1 = scan_dependencies(["src/services/server.ts"], diff, root_dir=self.root, max_depth=1)
        files_d1 = ctx_d1.touched_files()
        self.assertIn("src/types/config.ts", files_d1)
        self.assertIn("src/controllers/app.ts", files_d1)
        self.assertNotIn("src/controllers/main.ts", files_d1)
        self.assertNotIn("src/services/server.spec.ts", files_d1)

        ctx_d2 = scan_dependencies(["src/services/server.ts"], diff, root_dir=self.root, max_depth=2)
        files_d2 = ctx_d2.touched_files()
        self.assertIn("src/controllers/main.ts", files_d2)

    def test_go_bidirectional_package_and_sibling_resolution(self) -> None:
        (self.root / "internal" / "config").mkdir(parents=True, exist_ok=True)
        (self.root / "internal" / "auth").mkdir(parents=True, exist_ok=True)
        (self.root / "cmd" / "api").mkdir(parents=True, exist_ok=True)

        # Upstream package `internal/config`
        (self.root / "internal" / "config" / "settings.go").write_text(
            "package config\n\ntype TokenConfig struct {\n\tSecret string\n\tExpiry int\n}\n",
            encoding="utf-8",
        )
        # Staged file `internal/auth/jwt.go` (note: file stem `jwt` != package `auth`)
        (self.root / "internal" / "auth" / "jwt.go").write_text(
            "package auth\n\nimport (\n\t\"github.com/acme/app/internal/config\"\n)\n\n"
            "func VerifyToken(raw string, cfg config.TokenConfig) bool {\n"
            "\treturn len(raw) > 0 && len(cfg.Secret) > 0\n"
            "}\n",
            encoding="utf-8",
        )
        # Same-package sibling downstream caller `internal/auth/middleware.go`
        (self.root / "internal" / "auth" / "middleware.go").write_text(
            "package auth\n\nimport \"github.com/acme/app/internal/config\"\n\n"
            "func AuthenticateRequest(header string, c config.TokenConfig) bool {\n"
            "\treturn VerifyToken(header, c)\n"
            "}\n",
            encoding="utf-8",
        )
        # Cross-package 2nd-degree downstream caller `cmd/api/router.go`
        (self.root / "cmd" / "api" / "router.go").write_text(
            "package api\n\nimport (\n\t\"github.com/acme/app/internal/auth\"\n\t\"github.com/acme/app/internal/config\"\n)\n\n"
            "func HandleLogin(h string) bool {\n"
            "\treturn auth.AuthenticateRequest(h, config.TokenConfig{})\n"
            "}\n",
            encoding="utf-8",
        )
        # Go test file (should be excluded)
        (self.root / "internal" / "auth" / "jwt_test.go").write_text(
            "package auth\n\nfunc TestVerifyToken() {\n\tVerifyToken(\"abc\", nil)\n}\n",
            encoding="utf-8",
        )

        diff = (
            "+++ b/internal/auth/jwt.go\n"
            "@@ -6,3 +6,3 @@\n"
            "-func VerifyToken(raw string) bool {\n"
            "+func VerifyToken(raw string, cfg config.TokenConfig) bool {\n"
        )
        ctx_d1 = scan_dependencies(["internal/auth/jwt.go"], diff, root_dir=self.root, max_depth=1)
        files_d1 = ctx_d1.touched_files()
        self.assertIn("internal/config/settings.go", files_d1)
        self.assertIn("internal/auth/middleware.go", files_d1)
        self.assertNotIn("cmd/api/router.go", files_d1)
        self.assertNotIn("internal/auth/jwt_test.go", files_d1)

        ctx_d2 = scan_dependencies(["internal/auth/jwt.go"], diff, root_dir=self.root, max_depth=2)
        files_d2 = ctx_d2.touched_files()
        self.assertIn("cmd/api/router.go", files_d2)

    def test_java_bidirectional_cross_package_and_same_package_resolution(self) -> None:
        contracts_dir = self.root / "src" / "main" / "java" / "com" / "acme" / "contracts"
        service_dir = self.root / "src" / "main" / "java" / "com" / "acme" / "service"
        api_dir = self.root / "src" / "main" / "java" / "com" / "acme" / "api"
        test_dir = self.root / "src" / "test" / "java" / "com" / "acme" / "service"
        for d in (contracts_dir, service_dir, api_dir, test_dir):
            d.mkdir(parents=True, exist_ok=True)

        # Upstream interface
        (contracts_dir / "PaymentGateway.java").write_text(
            "package com.acme.contracts;\n\n"
            "public interface PaymentGateway {\n"
            "    boolean chargeCents(int amountCents, String currency);\n"
            "}\n",
            encoding="utf-8",
        )
        # Staged Java service class
        (service_dir / "CheckoutService.java").write_text(
            "package com.acme.service;\n\n"
            "import com.acme.contracts.PaymentGateway;\n\n"
            "public class CheckoutService {\n"
            "    private final PaymentGateway gateway;\n\n"
            "    public CheckoutService(PaymentGateway gateway) {\n"
            "        this.gateway = gateway;\n"
            "    }\n\n"
            "    public boolean processOrder(int totalCents, String currency) {\n"
            "        return this.gateway.chargeCents(totalCents, currency);\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        # Same-package downstream caller (no explicit import required in Java)
        (service_dir / "OrderFacade.java").write_text(
            "package com.acme.service;\n\n"
            "public class OrderFacade {\n"
            "    public boolean submitOrder(CheckoutService checkout, int cents) {\n"
            "        return checkout.processOrder(cents, \"USD\");\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        # Cross-package downstream caller
        (api_dir / "CheckoutController.java").write_text(
            "package com.acme.api;\n\n"
            "import com.acme.service.CheckoutService;\n\n"
            "public class CheckoutController {\n"
            "    public String handlePost(CheckoutService service) {\n"
            "        boolean ok = service.processOrder(5000, \"USD\");\n"
            "        return ok ? \"200\" : \"400\";\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        # Java unit test (should be excluded)
        (test_dir / "CheckoutServiceTest.java").write_text(
            "package com.acme.service;\n\n"
            "public class CheckoutServiceTest {\n"
            "    public void testProcessOrder() {\n"
            "        new CheckoutService(null).processOrder(100, \"USD\");\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )

        staged_rel = "src/main/java/com/acme/service/CheckoutService.java"
        diff = (
            f"+++ b/{staged_rel}\n"
            "@@ -11,3 +11,3 @@\n"
            "-    public boolean processOrder(int totalCents) {\n"
            "+    public boolean processOrder(int totalCents, String currency) {\n"
            "         return this.gateway.chargeCents(totalCents, currency);\n"
        )
        ctx = scan_dependencies([staged_rel], diff, root_dir=self.root, max_depth=1)
        touched = ctx.touched_files()
        self.assertIn("src/main/java/com/acme/contracts/PaymentGateway.java", touched)
        self.assertIn("src/main/java/com/acme/service/OrderFacade.java", touched)
        self.assertIn("src/main/java/com/acme/api/CheckoutController.java", touched)
        self.assertNotIn("src/test/java/com/acme/service/CheckoutServiceTest.java", touched)

    def test_context_budget_truncation(self) -> None:
        (self.root / "a.py").write_text(
            "from b import helper_one, helper_two\n\ndef main() -> None:\n    helper_one()\n    helper_two()\n",
            encoding="utf-8",
        )
        long_body = "\n".join(f"    x_{i} = {i}" for i in range(40))
        (self.root / "b.py").write_text(
            f"def helper_one() -> None:\n{long_body}\n\ndef helper_two() -> None:\n{long_body}\n",
            encoding="utf-8",
        )
        diff = "+++ b/a.py\n@@ -1,5 +1,5 @@\n+    helper_one()\n+    helper_two()\n"
        ctx = scan_dependencies(["a.py"], diff, root_dir=self.root, max_depth=1, max_chars=900)
        self.assertEqual(len(ctx.snippets), 1)
        self.assertTrue(ctx.truncated)
        self.assertGreater(ctx.truncated_chars, 0)
        prompt_out = ctx.format_for_prompt()
        self.assertIn("additional dependency snippets omitted", prompt_out)
        self.assertIn(f"{ctx.truncated_chars:,} chars removed", prompt_out)


if __name__ == "__main__":
    unittest.main()
