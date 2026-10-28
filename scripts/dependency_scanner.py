#!/usr/bin/env python3
"""Local dependency context scanner for pre-commit code reviews.

Traverses N degrees of bidirectional local code dependencies (upstream imported
definitions/contracts and downstream callers/importers) around staged diff hunks
using Python stdlib `ast` and multi-language regex heuristics.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

try:
    from scripts.constants import (
        DEFAULT_DEP_DEPTH,
        DEFAULT_MAX_DEP_CHARS,
        ENV_DEP_DEPTH,
        ENV_MAX_DEP_CHARS,
    )
except ImportError:
    from constants import (
        DEFAULT_DEP_DEPTH,
        DEFAULT_MAX_DEP_CHARS,
        ENV_DEP_DEPTH,
        ENV_MAX_DEP_CHARS,
    )

SUPPORTED_SOURCE_EXTENSIONS: tuple[str, ...] = (
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".java",
    ".kt",
    ".kts",
    ".go",
    ".rs",
    ".cs",
    ".c",
    ".cpp",
    ".cc",
    ".h",
    ".hpp",
)

IGNORED_DIRS: set[str] = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "target",
    "out",
    "bin",
    "obj",
    ".gradle",
    ".idea",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".llm_cache",
}

MAX_SNIPPET_LINES = 45
IDENT_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\b")
HUNK_HEADER_PATTERN = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

COMMON_KEYWORDS: set[str] = {
    # Python
    "False",
    "None",
    "True",
    "and",
    "as",
    "assert",
    "async",
    "await",
    "break",
    "class",
    "continue",
    "def",
    "del",
    "elif",
    "else",
    "except",
    "finally",
    "for",
    "from",
    "global",
    "if",
    "import",
    "in",
    "is",
    "lambda",
    "nonlocal",
    "not",
    "or",
    "pass",
    "raise",
    "return",
    "try",
    "while",
    "with",
    "yield",
    "self",
    "cls",
    "str",
    "int",
    "float",
    "bool",
    "list",
    "dict",
    "set",
    "tuple",
    "Path",
    "Any",
    "Sequence",
    "Optional",
    "Union",
    # Java / Kotlin / C# / C++
    "package",
    "public",
    "private",
    "protected",
    "internal",
    "static",
    "final",
    "abstract",
    "synchronized",
    "volatile",
    "transient",
    "native",
    "void",
    "long",
    "short",
    "byte",
    "double",
    "char",
    "boolean",
    "String",
    "Object",
    "Integer",
    "Boolean",
    "List",
    "Map",
    "Set",
    "Optional",
    "extends",
    "implements",
    "throws",
    "throw",
    "new",
    "this",
    "super",
    "null",
    "true",
    "false",
    "switch",
    "case",
    "default",
    "catch",
    "interface",
    "enum",
    "record",
    "namespace",
    "using",
    "virtual",
    "override",
    "readonly",
    "fun",
    "val",
    "var",
    "data",
    "sealed",
    # Go / Rust / TS / JS
    "func",
    "type",
    "struct",
    "const",
    "let",
    "export",
    "function",
    "typeof",
    "instanceof",
    "undefined",
    "number",
    "string",
    "any",
    "unknown",
    "never",
    "Promise",
    "Record",
    "defer",
    "chan",
    "map",
    "range",
    "select",
    "fallthrough",
    "nil",
    "error",
    "err",
    "ctx",
    "pub",
    "crate",
    "mod",
    "use",
    "impl",
    "trait",
    "fn",
    "mut",
    "match",
    "where",
    "Self",
    "Some",
    "Ok",
    "Err",
    "Result",
    "Option",
    "Vec",
    "Box",
}

PYTHON_KEYWORDS = COMMON_KEYWORDS


@dataclass(frozen=True)
class DependencySnippet:
    """Extracted symbol definition or call-site snippet from a local dependency."""

    file: str
    direction: str  # "upstream" or "downstream"
    degree: int
    symbol: str
    source_file: str
    line_start: int
    line_end: int
    snippet: str
    enclosing_symbol: str = ""
    has_unstaged_edits: bool = False


@dataclass
class DependencyContext:
    """Aggregated multi-degree dependency context for a staged commit."""

    max_depth: int = DEFAULT_DEP_DEPTH
    snippets: list[DependencySnippet] = field(default_factory=list)
    unstaged_neighbor_files: list[str] = field(default_factory=list)
    truncated: bool = False
    truncated_chars: int = 0

    def touched_files(self) -> list[str]:
        """Return unique sorted list of dependency file paths."""
        seen: set[str] = set()
        result: list[str] = []
        for s in self.snippets:
            if s.file not in seen:
                seen.add(s.file)
                result.append(s.file)
        return result

    def format_for_prompt(self) -> str:
        """Render dependency snippets and unstaged-neighbor hints for LLM evaluation."""
        if not self.snippets and not self.unstaged_neighbor_files and not self.truncated:
            return ""

        lines: list[str] = []
        if self.unstaged_neighbor_files:
            lines.append(
                "NOTE ON UNSTAGED WORKING-TREE EDITS: The following related local files have "
                "unstaged modifications in the working tree that are NOT included in this commit: "
                + ", ".join(self.unstaged_neighbor_files)
            )
            lines.append("")

        for s in self.snippets:
            dir_label = (
                "UPSTREAM DEFINITION/CONTRACT"
                if s.direction == "upstream"
                else "DOWNSTREAM CALLER/IMPORTER"
            )
            unstaged_tag = " [WARNING: HAS UNSTAGED WORKING-TREE EDITS]" if s.has_unstaged_edits else ""
            header = (
                f"--- [{dir_label} | Degree: {s.degree} | Symbol: {s.symbol} | "
                f"File: {s.file}:{s.line_start}-{s.line_end} (linked from {s.source_file}){unstaged_tag}] ---"
            )
            lines.append(header)
            lines.append(s.snippet)
            lines.append("")

        if self.truncated:
            lines.append(
                f"[... additional dependency snippets omitted due to context budget "
                f"({self.truncated_chars:,} chars removed) ...]"
            )

        return "\n".join(lines).strip()


def resolve_dep_depth(explicit_depth: int | None = None) -> int:
    """Resolve dependency traversal depth from argument or environment."""
    if explicit_depth is not None:
        return max(0, explicit_depth)
    env_val = os.getenv(ENV_DEP_DEPTH)
    if env_val is not None:
        try:
            return max(0, int(env_val.strip()))
        except ValueError:
            pass
    return DEFAULT_DEP_DEPTH


def resolve_max_dep_chars(explicit_max_chars: int | None = None) -> int:
    """Resolve maximum character budget for dependency snippets."""
    if explicit_max_chars is not None:
        return max(500, explicit_max_chars)
    env_val = os.getenv(ENV_MAX_DEP_CHARS)
    if env_val is not None:
        try:
            return max(500, int(env_val.strip()))
        except ValueError:
            pass
    return DEFAULT_MAX_DEP_CHARS


def is_test_file(rel_path: str) -> bool:
    """Determine if a repository-relative path is a unit/integration test file across languages."""
    norm = rel_path.replace("\\", "/").lstrip("/")
    raw_parts = norm.split("/")
    parts = [p.lower() for p in raw_parts]
    if any(p in ("tests", "test", "__tests__", "testing") for p in parts[:-1]):
        return True
    raw_fname = raw_parts[-1] if raw_parts else ""
    fname = raw_fname.lower()
    if (
        fname.startswith("test_")
        or fname.endswith("_test.py")
        or fname.endswith("_test.go")
        or fname.endswith("_test.rs")
        or fname.endswith(".spec.ts")
        or fname.endswith(".test.ts")
        or fname.endswith(".spec.tsx")
        or fname.endswith(".test.tsx")
        or fname.endswith(".spec.js")
        or fname.endswith(".test.js")
        or fname.endswith(".spec.jsx")
        or fname.endswith(".test.jsx")
        or fname == "conftest.py"
        or raw_fname.endswith(("Test.java", "Tests.java", "IT.java", "TestCase.java"))
        or raw_fname.endswith(("Test.kt", "Tests.kt", "Test.cs", "Tests.cs"))
    ):
        return True
    return False


def get_unstaged_modified_files(root_dir: Path) -> set[str]:
    """Return set of files with unstaged working-tree changes relative to the Git index."""
    try:
        res = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=root_dir,
            capture_output=True,
            text=True,
            check=True,
            timeout=3.0,
        )
        return {
            line.strip().replace("\\", "/")
            for line in res.stdout.splitlines()
            if line.strip()
        }
    except Exception:
        return set()


def read_repo_file(
    rel_path: str,
    root_dir: Path,
    staged_set: set[str],
) -> str | None:
    """Read file content from Git index (for staged files) or HEAD (for untouched neighbors).

    Falls back to reading the file on disk when Git index/HEAD is unavailable (e.g. in isolated unit tests).
    """
    norm_path = rel_path.replace("\\", "/").lstrip("/")
    git_dir = root_dir / ".git"
    if git_dir.exists():
        ref = f":{norm_path}" if norm_path in staged_set else f"HEAD:{norm_path}"
        try:
            res = subprocess.run(
                ["git", "show", ref],
                cwd=root_dir,
                capture_output=True,
                text=True,
                check=True,
                timeout=3.0,
            )
            return res.stdout
        except Exception:
            pass

    disk_path = root_dir / norm_path
    if disk_path.is_file():
        try:
            return disk_path.read_text(encoding="utf-8")
        except Exception:
            return None
    return None


def list_production_files(root_dir: Path, staged_set: set[str]) -> list[str]:
    """Discover all tracked (or local) production source files excluding test files."""
    candidates: set[str] = set()
    git_dir = root_dir / ".git"
    used_git = False
    if git_dir.exists():
        try:
            res = subprocess.run(
                ["git", "ls-files"],
                cwd=root_dir,
                capture_output=True,
                text=True,
                check=True,
                timeout=3.0,
            )
            for line in res.stdout.splitlines():
                clean = line.strip().replace("\\", "/")
                if clean:
                    candidates.add(clean)
            candidates.update(staged_set)
            used_git = True
        except Exception:
            pass

    if not used_git:
        for item in root_dir.rglob("*"):
            if any(part in IGNORED_DIRS for part in item.parts):
                continue
            if item.is_file():
                rel = str(item.relative_to(root_dir)).replace("\\", "/")
                candidates.add(rel)

    production_files: list[str] = []
    for rel in sorted(candidates):
        if Path(rel).suffix.lower() not in SUPPORTED_SOURCE_EXTENSIONS:
            continue
        if is_test_file(rel):
            continue
        production_files.append(rel)
    return production_files


def parse_diff_changed_ranges_and_tokens(
    diff_text: str,
) -> tuple[dict[str, list[tuple[int, int]]], dict[str, set[str]]]:
    """Parse unified diff to extract modified line ranges and changed identifier tokens per file."""
    ranges_by_file: dict[str, list[tuple[int, int]]] = {}
    tokens_by_file: dict[str, set[str]] = {}

    current_file: str | None = None
    for raw_line in diff_text.splitlines():
        if raw_line.startswith("+++ b/"):
            current_file = raw_line[6:].strip().replace("\\", "/")
            ranges_by_file.setdefault(current_file, [])
            tokens_by_file.setdefault(current_file, set())
            continue

        if current_file is None:
            continue

        hunk_match = HUNK_HEADER_PATTERN.match(raw_line)
        if hunk_match:
            start_line = int(hunk_match.group(1))
            count = int(hunk_match.group(2)) if hunk_match.group(2) is not None else 1
            end_line = start_line + max(0, count - 1)
            ranges_by_file[current_file].append((start_line, end_line))
            continue

        if (raw_line.startswith("+") and not raw_line.startswith("+++")) or (
            raw_line.startswith("-") and not raw_line.startswith("---")
        ):
            code_line = raw_line[1:]
            for match in IDENT_PATTERN.finditer(code_line):
                ident = match.group(1)
                if ident not in PYTHON_KEYWORDS:
                    tokens_by_file[current_file].add(ident)

    return ranges_by_file, tokens_by_file


def _format_numbered_lines(lines: Sequence[str], start_line: int, max_lines: int = MAX_SNIPPET_LINES) -> tuple[str, int]:
    """Format a slice of source lines with 1-based line numbers, truncating if needed."""
    sliced = list(lines[:max_lines])
    end_line = start_line + len(sliced) - 1
    formatted = [f"{start_line + idx}: {line}" for idx, line in enumerate(sliced)]
    if len(lines) > max_lines:
        omitted_lines = lines[max_lines:]
        omitted_chars = sum(len(line) + 1 for line in omitted_lines)
        formatted.append(f"... ({len(omitted_lines)} more lines / {omitted_chars:,} chars removed)")
    return "\n".join(formatted), max(start_line, end_line)


# ==============================================================================
# Python AST Dependency & Symbol Resolution
# ==============================================================================


def _resolve_python_module_to_file(
    module_name: str,
    level: int,
    importer_rel_path: str,
    root_dir: Path,
    known_files: set[str],
) -> str | None:
    """Resolve a Python import module name to a local repository file path if available."""
    importer_dir = Path(importer_rel_path).parent

    search_AutoPaths: list[Path] = []
    if level > 0:
        base_dir = importer_dir
        for _ in range(level - 1):
            base_dir = base_dir.parent
        if module_name:
            mod_rel = Path(*module_name.split("."))
            search_AutoPaths.append(base_dir / mod_rel)
        else:
            search_AutoPaths.append(base_dir)
    elif module_name:
        mod_rel = Path(*module_name.split("."))
        # 1. Root-relative (e.g. `from scripts.rule_loader import ...`)
        search_AutoPaths.append(mod_rel)
        # 2. Same-directory sibling (e.g. `from rule_loader import ...` inside `scripts/`)
        search_AutoPaths.append(importer_dir / mod_rel)
        # 3. `src/` layout fallback
        search_AutoPaths.append(Path("src") / mod_rel)

    for cand in search_AutoPaths:
        py_file = str(cand.with_suffix(".py")).replace("\\", "/").lstrip("./")
        if py_file in known_files or (root_dir / py_file).is_file():
            return py_file
        init_file = str(cand / "__init__.py").replace("\\", "/").lstrip("./")
        if init_file in known_files or (root_dir / init_file).is_file():
            return init_file

    return None


def _ranges_overlap(a_start: int, a_end: int, ranges: Sequence[tuple[int, int]]) -> bool:
    """Return True if [a_start, a_end] overlaps with any (r_start, r_end) in ranges."""
    if not ranges:
        return True
    for r_start, r_end in ranges:
        if a_start <= r_end + 3 and a_end >= r_start - 3:
            return True
    return False


@dataclass
class FileAnalysis:
    """Language-agnostic analysis result for a single source file."""

    rel_path: str
    defined_symbols: dict[str, tuple[int, int, str]]  # symbol -> (start, end, snippet)
    modified_symbols: set[str]
    referenced_symbols: set[str]
    imported_symbol_map: dict[str, tuple[str, str]]  # local_alias -> (target_rel_file, original_symbol)
    imported_module_map: dict[str, str]  # local_module_alias -> target_rel_file
    imported_files: set[str] = field(default_factory=set)  # package/wildcard/sibling file dependencies
    symbol_block_ranges: dict[str, tuple[int, int]] = field(default_factory=dict)  # symbol -> (full_start, full_end)


PythonFileAnalysis = FileAnalysis


def analyze_python_file(
    rel_path: str,
    source: str,
    diff_ranges: Sequence[tuple[int, int]],
    diff_tokens: set[str],
    root_dir: Path,
    known_files: set[str],
) -> FileAnalysis | None:
    """Parse a Python file via AST to extract definitions, modified symbols, and local imports."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    lines = source.splitlines()
    defined_symbols: dict[str, tuple[int, int, str]] = {}
    symbol_block_ranges: dict[str, tuple[int, int]] = {}
    modified_symbols: set[str] = set()
    referenced_symbols: set[str] = set(diff_tokens)
    imported_symbol_map: dict[str, tuple[str, str]] = {}
    imported_module_map: dict[str, str] = {}
    imported_files: set[str] = set()

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = node.lineno
            end = getattr(node, "end_lineno", start) or start
            snippet_text, actual_end = _format_numbered_lines(lines[start - 1 : end], start)
            defined_symbols[node.name] = (start, actual_end, snippet_text)
            symbol_block_ranges[node.name] = (start, end)
            if _ranges_overlap(start, end, diff_ranges):
                modified_symbols.add(node.name)
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Name):
                        referenced_symbols.add(sub.id)
                    elif isinstance(sub, ast.Attribute):
                        referenced_symbols.add(sub.attr)

        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            start = node.lineno
            end = getattr(node, "end_lineno", start) or start
            targets: list[str] = []
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        targets.append(t.id)
            elif isinstance(node.target, ast.Name):
                targets.append(node.target.id)

            for tname in targets:
                snippet_text, actual_end = _format_numbered_lines(lines[start - 1 : end], start, max_lines=15)
                defined_symbols[tname] = (start, actual_end, snippet_text)
                symbol_block_ranges[tname] = (start, end)
                if _ranges_overlap(start, end, diff_ranges) or tname in diff_tokens:
                    modified_symbols.add(tname)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            target_file = _resolve_python_module_to_file(
                node.module or "",
                node.level or 0,
                rel_path,
                root_dir,
                known_files,
            )
            if target_file and target_file != rel_path:
                imported_files.add(target_file)
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    local_name = alias.asname or alias.name
                    imported_symbol_map[local_name] = (target_file, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                target_file = _resolve_python_module_to_file(
                    alias.name,
                    0,
                    rel_path,
                    root_dir,
                    known_files,
                )
                if target_file and target_file != rel_path:
                    imported_files.add(target_file)
                    local_mod = alias.asname or alias.name.split(".")[-1]
                    imported_module_map[local_mod] = target_file

    # Check attribute accesses on imported modules (e.g. `rule_loader.load_rules`)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            mod_alias = node.value.id
            if mod_alias in imported_module_map:
                target_file = imported_module_map[mod_alias]
                imported_symbol_map[node.attr] = (target_file, node.attr)

    return FileAnalysis(
        rel_path=rel_path,
        defined_symbols=defined_symbols,
        modified_symbols=modified_symbols,
        referenced_symbols=referenced_symbols,
        imported_symbol_map=imported_symbol_map,
        imported_module_map=imported_module_map,
        imported_files=imported_files,
        symbol_block_ranges=symbol_block_ranges,
    )


def find_python_downstream_callsites(
    candidate_rel_path: str,
    candidate_source: str,
    target_rel_path: str,
    target_symbols: set[str],
    root_dir: Path,
    known_files: set[str],
) -> list[tuple[str, str, int, int, str]]:
    """Find call sites or usages of `target_symbols` from `target_rel_path` inside `candidate_rel_path`.

    Returns list of (used_target_symbol, enclosing_symbol, line_start, line_end, snippet).
    """
    try:
        tree = ast.parse(candidate_source)
    except SyntaxError:
        return []

    # First verify whether candidate_rel_path imports from target_rel_path
    local_to_target_symbol: dict[str, str] = {}
    imported_module_aliases: set[str] = set()
    import_line_for_symbol: dict[str, int] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            resolved = _resolve_python_module_to_file(
                node.module or "",
                node.level or 0,
                candidate_rel_path,
                root_dir,
                known_files,
            )
            if resolved == target_rel_path:
                for alias in node.names:
                    local_name = alias.asname or alias.name
                    if not target_symbols or alias.name in target_symbols or alias.name == "*":
                        local_to_target_symbol[local_name] = alias.name
                        import_line_for_symbol[local_name] = node.lineno
        elif isinstance(node, ast.Import):
            for alias in node.names:
                resolved = _resolve_python_module_to_file(
                    alias.name,
                    0,
                    candidate_rel_path,
                    root_dir,
                    known_files,
                )
                if resolved == target_rel_path:
                    local_mod = alias.asname or alias.name.split(".")[-1]
                    imported_module_aliases.add(local_mod)

    if not local_to_target_symbol and not imported_module_aliases:
        return []

    lines = candidate_source.splitlines()
    results: list[tuple[str, str, int, int, str]] = []
    recorded_nodes: set[tuple[str, int]] = set()

    # Check top-level functions and classes for usages of the imported symbols
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            enclosing_name = node.name
            start = node.lineno
            end = getattr(node, "end_lineno", start) or start

            matched_target_sym: str | None = None
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and sub.id in local_to_target_symbol:
                    matched_target_sym = local_to_target_symbol[sub.id]
                    break
                if (
                    isinstance(sub, ast.Attribute)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id in imported_module_aliases
                ):
                    if not target_symbols or sub.attr in target_symbols:
                        matched_target_sym = sub.attr
                        break

            if matched_target_sym and (matched_target_sym, start) not in recorded_nodes:
                recorded_nodes.add((matched_target_sym, start))
                snippet_text, actual_end = _format_numbered_lines(lines[start - 1 : end], start)
                results.append((matched_target_sym, enclosing_name, start, actual_end, snippet_text))

    # Also check module-level usage outside functions/classes (or if imported symbol is used at module scope)
    if not results and local_to_target_symbol:
        for local_name, orig_sym in local_to_target_symbol.items():
            lineno = import_line_for_symbol.get(local_name, 1)
            win_start = max(1, lineno - 2)
            win_end = min(len(lines), lineno + 12)
            snippet_text, actual_end = _format_numbered_lines(
                lines[win_start - 1 : win_end], win_start, max_lines=15
            )
            results.append((orig_sym, "", win_start, actual_end, snippet_text))
            break

    return results


# ==============================================================================
# Multi-Language Dependency & Symbol Resolution (TS/JS, Java, Go, Rust, Kotlin, C#, C/C++)
# ==============================================================================

# TypeScript / JavaScript single-line & multi-line imports/exports and require()
JS_IMPORT_FROM_PATTERN = re.compile(
    r"""(?:import|export)\s+(?:type\s+)?([\s\S]*?)\s+from\s+['"]([^'"]+)['"]""",
)
JS_REQUIRE_PATTERN = re.compile(
    r"""(?:const|let|var)\s+(?:\{([^}]+)\}|([A-Za-z0-9_$]+))\s*=\s*require\(\s*['"]([^'"]+)['"]\s*\)""",
)

# Java / Kotlin imports
JAVA_IMPORT_PATTERN = re.compile(
    r"""^\s*import\s+(static\s+)?([A-Za-z0-9_.*]+)\s*;?""",
    re.MULTILINE,
)

# Go single-line and block imports
GO_SINGLE_IMPORT_PATTERN = re.compile(
    r"""^\s*import\s+(?:([A-Za-z0-9_.]+)\s+)?"([^"]+)"\s*$""",
    re.MULTILINE,
)
GO_BLOCK_IMPORT_PATTERN = re.compile(
    r"""^\s*import\s*\(([\s\S]*?)\)""",
    re.MULTILINE,
)
GO_SPEC_LINE_PATTERN = re.compile(
    r"""^\s*(?:([A-Za-z0-9_.]+)\s+)?"([^"]+)"\s*(?://.*)?$""",
)

# Rust `use` and `mod` statements
RUST_USE_PATTERN = re.compile(
    r"""use\s+(?:crate|super|self)::([A-Za-z0-9_:]+)(?:::\{([^}]+)\})?;""",
)
RUST_MOD_PATTERN = re.compile(
    r"""^\s*(?:pub\s+)?mod\s+([A-Za-z_][A-Za-z0-9_]*)\s*;""",
    re.MULTILINE,
)

# C / C++ local header includes
C_INCLUDE_PATTERN = re.compile(
    r"""^\s*#include\s+"([^"]+)"\s*$""",
    re.MULTILINE,
)

CONTROL_FLOW_KEYWORDS: set[str] = {
    "if",
    "else",
    "for",
    "while",
    "switch",
    "case",
    "catch",
    "try",
    "return",
    "throw",
    "new",
    "delete",
    "typeof",
    "sizeof",
    "synchronized",
}

MULTI_DEF_PATTERNS: tuple[re.Pattern[str], ...] = (
    # TypeScript / JavaScript functions
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\b"),
    # Classes, interfaces, types, enums, records, structs, traits (TS/JS/Java/Kotlin/C#/Rust/C++)
    re.compile(
        r"^\s*(?:export\s+)?(?:default\s+)?"
        r"(?:(?:public|private|protected|internal|abstract|final|static|sealed|data|open)\s+)*"
        r"(?:class|interface|type|enum|record|struct|trait)\s+([A-Za-z_][A-Za-z0-9_]*)\b"
    ),
    # TypeScript / JavaScript const/let/var declarations (arrow functions, exported constants)
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=]+)?="),
    # Go functions & methods (`func Foo(...)` or `func (s *Srv) Foo(...)`)
    re.compile(r"^\s*func\s+(?:\([^)]+\)\s*)?([A-Za-z_][A-Za-z0-9_]*)\b"),
    # Go type definitions (`type TokenConfig struct`, `type Handler interface`, `type ID string`)
    re.compile(r"^\s*type\s+([A-Za-z_][A-Za-z0-9_]*)\b"),
    # Rust functions (`pub async fn foo(...)`)
    re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:(?:async|const|unsafe)\s+)*fn\s+([A-Za-z_][A-Za-z0-9_]*)\b"),
    # Kotlin functions (`suspend fun foo(...)`)
    re.compile(
        r"^\s*(?:(?:public|private|protected|internal|override|open|suspend|inline)\s+)*"
        r"fun\s+(?:<[^>]+>\s*)?([A-Za-z_][A-Za-z0-9_]*)\b"
    ),
    # Java / C# / TypeScript methods with explicit visibility/modifiers (`public Receipt processOrder(...)`)
    re.compile(
        r"^\s*(?:@[A-Za-z0-9_]+(?:\([^)]*\))?\s+)*"
        r"(?:(?:public|private|protected|internal|static|final|synchronized|abstract|default|virtual|override|async)\s+)+"
        r"(?:<[^>]+>\s*)?(?:[A-Za-z_][A-Za-z0-9_<>,.\[\]\s?]*\s+)?"
        r"([A-Za-z_][A-Za-z0-9_]*)\s*\("
    ),
    # Indented TypeScript / JavaScript / Java package-private class methods (`  async handleRequest(req: Req) {`)
    re.compile(
        r"^\s{2,12}(?:async\s+)?(?:[A-Za-z_][A-Za-z0-9_<>,.\[\]]*\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\{\s*$"
    ),
)


def _find_brace_block_end(lines: Sequence[str], start_line: int, max_scan: int = 200) -> int:
    """Find the 1-based ending line of a brace-delimited block `{ ... }` starting at `start_line`."""
    depth = 0
    found_open = False
    limit = min(len(lines), start_line + max_scan - 1)
    for line_no in range(start_line, limit + 1):
        line = lines[line_no - 1]
        # Strip line comments to avoid counting braces in comments
        code_part = line.split("//", 1)[0]
        for ch in code_part:
            if ch == "{":
                depth += 1
                found_open = True
            elif ch == "}":
                depth -= 1
                if found_open and depth <= 0:
                    return line_no
        if not found_open and code_part.rstrip().endswith(";"):
            return line_no
    return min(len(lines), start_line + 20)


def _resolve_relative_import_to_file(
    import_spec: str,
    importer_rel_path: str,
    root_dir: Path,
    known_files: set[str],
) -> str | None:
    """Resolve a JS/TS/Rust/C relative or alias import specifier to a repository file path."""
    importer_dir = Path(importer_rel_path).parent
    candidate_bases: list[str] = []

    if import_spec.startswith("."):
        raw_base = str(importer_dir / import_spec).replace("\\", "/")
        norm = os.path.normpath(raw_base).replace("\\", "/").lstrip("./")
        candidate_bases.append(norm)
    elif import_spec.startswith(("@/", "~/")):
        stripped = import_spec[2:].lstrip("/")
        candidate_bases.append(stripped)
        candidate_bases.append(f"src/{stripped}")
    else:
        clean_spec = import_spec.lstrip("/")
        candidate_bases.append(clean_spec)
        candidate_bases.append(f"src/{clean_spec}")
        raw_rel = str(importer_dir / clean_spec).replace("\\", "/")
        candidate_bases.append(os.path.normpath(raw_rel).replace("\\", "/").lstrip("./"))

    exts = (
        "",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".rs",
        ".h",
        ".hpp",
        ".c",
        ".cpp",
        "/index.ts",
        "/index.tsx",
        "/index.js",
        "/index.jsx",
        "/mod.rs",
    )
    for base in candidate_bases:
        if not base or base.startswith(".."):
            continue
        for ext in exts:
            candidate = f"{base}{ext}"
            if candidate in known_files or (root_dir / candidate).is_file():
                return candidate
    return None


def _resolve_java_import_to_files(
    import_path: str,
    is_static: bool,
    known_files: set[str],
) -> tuple[list[str], str | None]:
    """Resolve a Java/Kotlin import path (e.g. `com.acme.pkg.ClassName`) to matching repository files."""
    if import_path.endswith(".*"):
        pkg_suffix = import_path[:-2].replace(".", "/")
        matched = [
            f
            for f in sorted(known_files)
            if f.endswith((".java", ".kt", ".kts"))
            and (Path(f).parent.as_posix().endswith(pkg_suffix) or Path(f).parent.as_posix() == pkg_suffix)
        ]
        return matched, None

    parts = import_path.split(".")
    if is_static and len(parts) >= 2:
        # `import static com.acme.pkg.ClassName.methodName`
        member_name = parts[-1]
        class_rel = "/".join(parts[:-1])
        matched = [
            f
            for f in sorted(known_files)
            if any(f.endswith(f"{class_rel}{ext}") for ext in (".java", ".kt", ".kts"))
        ]
        if matched:
            return matched, member_name

    symbol_name = parts[-1]
    class_rel = "/".join(parts)
    matched = [
        f
        for f in sorted(known_files)
        if any(f.endswith(f"{class_rel}{ext}") for ext in (".java", ".kt", ".kts"))
    ]
    return matched, symbol_name


def _resolve_go_import_to_files(
    import_path: str,
    known_files: set[str],
) -> list[str]:
    """Resolve a Go package import path (e.g. `github.com/org/repo/internal/auth`) to local `.go` files."""
    clean = import_path.strip("/")
    segments = clean.split("/")
    go_files = [f for f in sorted(known_files) if f.endswith(".go")]

    # Try matching progressively shorter suffixes of the import path against repo directory paths
    for i in range(len(segments)):
        suffix_dir = "/".join(segments[i:])
        exact_matches = [f for f in go_files if Path(f).parent.as_posix() == suffix_dir]
        if exact_matches:
            return exact_matches
        ending_matches = [f for f in go_files if Path(f).parent.as_posix().endswith(f"/{suffix_dir}")]
        if ending_matches:
            return ending_matches
    return []


def _parse_js_import_clause(
    clause: str,
    target_file: str,
    imported_symbol_map: dict[str, tuple[str, str]],
    imported_module_map: dict[str, str],
) -> None:
    """Parse a JS/TS import/export clause (`Foo, { bar, baz as qux }, * as ns`) into symbol/module maps."""
    cleaned = clause.strip()
    if not cleaned:
        return

    # Namespace import: `* as ns`
    ns_match = re.search(r"\*\s+as\s+([A-Za-z0-9_$]+)", cleaned)
    if ns_match:
        ns_alias = ns_match.group(1)
        imported_module_map[ns_alias] = target_file

    # Named block: `{ a, b as c, type d }`
    brace_match = re.search(r"\{([\s\S]*?)\}", cleaned)
    if brace_match:
        named_body = brace_match.group(1)
        for part in named_body.split(","):
            item = re.sub(r"^\s*type\s+", "", part.strip()).strip()
            if not item:
                continue
            if " as " in item:
                orig, alias = [x.strip() for x in item.split(" as ", 1)]
                if orig and alias:
                    imported_symbol_map[alias] = (target_file, orig)
            else:
                imported_symbol_map[item] = (target_file, item)

    # Default import before `{` or `*`
    before_brace = cleaned.split("{", 1)[0].split("*", 1)[0].strip().rstrip(",").strip()
    before_brace = re.sub(r"^type\s+", "", before_brace).strip()
    if before_brace and re.match(r"^[A-Za-z_$][A-Za-z0-9_$]*$", before_brace):
        imported_symbol_map[before_brace] = (target_file, before_brace)


def analyze_generic_source_file(
    rel_path: str,
    source: str,
    diff_ranges: Sequence[tuple[int, int]],
    diff_tokens: set[str],
    root_dir: Path,
    known_files: set[str],
) -> FileAnalysis:
    """Multi-language import and symbol definition analyzer (TS/JS, Java, Go, Rust, Kotlin, C#, C/C++)."""
    lines = source.splitlines()
    defined_symbols: dict[str, tuple[int, int, str]] = {}
    symbol_block_ranges: dict[str, tuple[int, int]] = {}
    modified_symbols: set[str] = set()
    referenced_symbols: set[str] = set(diff_tokens)
    imported_symbol_map: dict[str, tuple[str, str]] = {}
    imported_module_map: dict[str, str] = {}
    imported_files: set[str] = set()

    ext = Path(rel_path).suffix.lower()
    file_dir = Path(rel_path).parent.as_posix()

    # 1. Extract symbol definitions and brace-balanced block ranges
    for idx, line in enumerate(lines, start=1):
        for pat in MULTI_DEF_PATTERNS:
            m = pat.match(line)
            if m:
                sym = m.group(1)
                if sym in CONTROL_FLOW_KEYWORDS or sym in COMMON_KEYWORDS:
                    continue
                block_end = _find_brace_block_end(lines, idx)
                snippet_end_limit = min(block_end, idx + MAX_SNIPPET_LINES - 1)
                snippet_text, actual_end = _format_numbered_lines(
                    lines[idx - 1 : snippet_end_limit],
                    idx,
                    max_lines=MAX_SNIPPET_LINES,
                )
                defined_symbols[sym] = (idx, actual_end, snippet_text)
                symbol_block_ranges[sym] = (idx, block_end)
                if _ranges_overlap(idx, block_end, diff_ranges) or sym in diff_tokens:
                    modified_symbols.add(sym)
                    # Collect identifiers referenced within the modified symbol block
                    for blk_line in lines[idx - 1 : min(block_end, idx + 80)]:
                        for id_match in IDENT_PATTERN.finditer(blk_line):
                            ident = id_match.group(1)
                            if ident not in COMMON_KEYWORDS:
                                referenced_symbols.add(ident)
                break

    # 2. Language-specific import & package resolution
    if ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        for m in JS_IMPORT_FROM_PATTERN.finditer(source):
            clause, spec = m.group(1), m.group(2)
            target_file = _resolve_relative_import_to_file(spec, rel_path, root_dir, known_files)
            if target_file and target_file != rel_path:
                imported_files.add(target_file)
                _parse_js_import_clause(clause, target_file, imported_symbol_map, imported_module_map)

        for m in JS_REQUIRE_PATTERN.finditer(source):
            named_block, default_ident, spec = m.group(1), m.group(2), m.group(3)
            target_file = _resolve_relative_import_to_file(spec, rel_path, root_dir, known_files)
            if target_file and target_file != rel_path:
                imported_files.add(target_file)
                if named_block:
                    _parse_js_import_clause(f"{{{named_block}}}", target_file, imported_symbol_map, imported_module_map)
                elif default_ident:
                    imported_symbol_map[default_ident.strip()] = (target_file, default_ident.strip())

    elif ext in (".java", ".kt", ".kts"):
        # Explicit Java/Kotlin imports
        for m in JAVA_IMPORT_PATTERN.finditer(source):
            is_static = bool(m.group(1))
            imp_path = m.group(2)
            matched_files, sym_name = _resolve_java_import_to_files(imp_path, is_static, known_files)
            for tfile in matched_files:
                if tfile != rel_path:
                    imported_files.add(tfile)
                    if sym_name:
                        imported_symbol_map[sym_name] = (tfile, sym_name)

        # Same-package sibling files in the same directory share scope without explicit import
        for kfile in known_files:
            if kfile != rel_path and Path(kfile).suffix.lower() == ext and Path(kfile).parent.as_posix() == file_dir:
                imported_files.add(kfile)
                sibling_class = Path(kfile).stem
                imported_symbol_map.setdefault(sibling_class, (kfile, sibling_class))

    elif ext == ".go":
        go_specs: list[tuple[str, str]] = []
        for m in GO_SINGLE_IMPORT_PATTERN.finditer(source):
            go_specs.append((m.group(1) or "", m.group(2)))
        for block_m in GO_BLOCK_IMPORT_PATTERN.finditer(source):
            for raw_line in block_m.group(1).splitlines():
                lm = GO_SPEC_LINE_PATTERN.match(raw_line)
                if lm:
                    go_specs.append((lm.group(1) or "", lm.group(2)))

        for alias, imp_path in go_specs:
            matched_files = _resolve_go_import_to_files(imp_path, known_files)
            pkg_alias = alias if alias and alias not in ("_", ".") else imp_path.split("/")[-1]
            for tfile in matched_files:
                if tfile != rel_path:
                    imported_files.add(tfile)
                    imported_module_map.setdefault(pkg_alias, tfile)

        # Same-directory `.go` files belong to the same Go package automatically
        for kfile in known_files:
            if kfile != rel_path and kfile.endswith(".go") and Path(kfile).parent.as_posix() == file_dir:
                imported_files.add(kfile)

    elif ext == ".rs":
        for m in RUST_USE_PATTERN.finditer(source):
            path_part, group_part = m.group(1), m.group(2)
            parts = path_part.split("::")
            mod_spec = "/".join(parts[:-1]) if len(parts) > 1 else parts[0]
            target_file = _resolve_relative_import_to_file(mod_spec, rel_path, root_dir, known_files)
            if target_file and target_file != rel_path:
                imported_files.add(target_file)
                if group_part:
                    for item in group_part.split(","):
                        sym = item.strip()
                        if sym:
                            imported_symbol_map[sym] = (target_file, sym)
                else:
                    sym = parts[-1]
                    imported_symbol_map[sym] = (target_file, sym)

        for m in RUST_MOD_PATTERN.finditer(source):
            mod_name = m.group(1)
            target_file = _resolve_relative_import_to_file(mod_name, rel_path, root_dir, known_files)
            if target_file and target_file != rel_path:
                imported_files.add(target_file)

    elif ext in (".c", ".cpp", ".cc", ".h", ".hpp", ".cs"):
        for m in C_INCLUDE_PATTERN.finditer(source):
            inc_spec = m.group(1)
            target_file = _resolve_relative_import_to_file(inc_spec, rel_path, root_dir, known_files)
            if target_file and target_file != rel_path:
                imported_files.add(target_file)
                # Also link corresponding implementation file (e.g. `foo.h` -> `foo.cpp` / `foo.c`)
                stem_path = str(Path(target_file).with_suffix("")).replace("\\", "/")
                for impl_ext in (".c", ".cpp", ".cc"):
                    impl_cand = f"{stem_path}{impl_ext}"
                    if impl_cand != rel_path and impl_cand in known_files:
                        imported_files.add(impl_cand)
        if ext == ".cs":
            for kfile in known_files:
                if kfile != rel_path and kfile.endswith(".cs"):
                    imported_files.add(kfile)

    return FileAnalysis(
        rel_path=rel_path,
        defined_symbols=defined_symbols,
        modified_symbols=modified_symbols,
        referenced_symbols=referenced_symbols,
        imported_symbol_map=imported_symbol_map,
        imported_module_map=imported_module_map,
        imported_files=imported_files,
        symbol_block_ranges=symbol_block_ranges,
    )


def find_generic_downstream_callsites(
    cand_analysis: FileAnalysis,
    cand_source: str,
    target_rel_path: str,
    target_symbols: set[str],
) -> list[tuple[str, str, int, int, str]]:
    """Find downstream call sites or references to `target_rel_path` symbols in a non-Python file."""
    # Check if candidate file imports or shares package scope with target_rel_path
    explicit_aliases: dict[str, str] = {}  # local_alias -> orig_symbol
    for local_alias, (t_file, orig_sym) in cand_analysis.imported_symbol_map.items():
        if t_file == target_rel_path:
            if not target_symbols or orig_sym in target_symbols or orig_sym == Path(target_rel_path).stem:
                explicit_aliases[local_alias] = orig_sym

    is_file_linked = target_rel_path in cand_analysis.imported_files or bool(explicit_aliases)
    if not is_file_linked:
        return []

    # Build lookup of local identifier -> target symbol to search for in candidate_source
    search_map: dict[str, str] = {}
    for sym in target_symbols:
        search_map[sym] = sym
    for local_alias, orig_sym in explicit_aliases.items():
        if not target_symbols or orig_sym in target_symbols:
            search_map[local_alias] = orig_sym

    if not search_map:
        return []

    cand_lines = cand_source.splitlines()
    results: list[tuple[str, str, int, int, str]] = []
    recorded_enclosing: set[tuple[str, int]] = set()

    # Sort enclosing symbols from narrowest (methods/functions) to widest (classes)
    enclosing_defs = sorted(
        cand_analysis.symbol_block_ranges.items(),
        key=lambda kv: (kv[1][1] - kv[1][0], kv[1][0]),
    )

    for idx, line in enumerate(cand_lines, start=1):
        stripped = line.lstrip()
        if stripped.startswith(("import ", "package ", "//", "/*", "*", "use ", "#include")):
            continue

        matched_orig_sym: str | None = None
        for local_ident, orig_sym in search_map.items():
            if local_ident in line and re.search(rf"\b{re.escape(local_ident)}\b", line):
                matched_orig_sym = orig_sym
                break

        if not matched_orig_sym:
            continue

        # Find narrowest enclosing function/method/class containing line `idx`
        enclosing_name = ""
        enc_start = max(1, idx - 2)
        enc_end = min(len(cand_lines), idx + 12)
        for sym_name, (b_start, b_end) in enclosing_defs:
            if b_start <= idx <= b_end:
                enclosing_name = sym_name
                enc_start = b_start
                enc_end = min(b_end, b_start + MAX_SNIPPET_LINES - 1)
                break

        dedup_key = (matched_orig_sym, enc_start)
        if dedup_key in recorded_enclosing:
            continue
        recorded_enclosing.add(dedup_key)

        snip, actual_end = _format_numbered_lines(
            cand_lines[enc_start - 1 : enc_end],
            enc_start,
            max_lines=MAX_SNIPPET_LINES,
        )
        results.append((matched_orig_sym, enclosing_name, enc_start, actual_end, snip))

    return results


# ==============================================================================
# Main Bidirectional N-Degree Dependency Scanner
# ==============================================================================


def scan_dependencies(
    staged_files: Sequence[str],
    diff_text: str,
    root_dir: Path | str = ".",
    max_depth: int | None = None,
    max_chars: int | None = None,
) -> DependencyContext:
    """Scan up to `max_depth` degrees of bidirectional local dependencies around staged changes."""
    resolved_depth = resolve_dep_depth(max_depth)
    resolved_max_chars = resolve_max_dep_chars(max_chars)
    context = DependencyContext(max_depth=resolved_depth)

    if resolved_depth <= 0 or not staged_files or not diff_text.strip():
        return context

    root = Path(root_dir).resolve()
    staged_set = {f.replace("\\", "/").lstrip("/") for f in staged_files if f.strip()}
    unstaged_set = get_unstaged_modified_files(root)
    prod_files = list_production_files(root, staged_set)
    known_files = set(prod_files) | staged_set

    diff_ranges_by_file, diff_tokens_by_file = parse_diff_changed_ranges_and_tokens(diff_text)

    # Cache parsed file analyses on demand
    analysis_cache: dict[str, FileAnalysis | None] = {}
    source_cache: dict[str, str | None] = {}

    def get_source(rel_path: str) -> str | None:
        if rel_path not in source_cache:
            source_cache[rel_path] = read_repo_file(rel_path, root, staged_set)
        return source_cache[rel_path]

    def get_analysis(rel_path: str) -> FileAnalysis | None:
        if rel_path in analysis_cache:
            return analysis_cache[rel_path]
        src = get_source(rel_path)
        if src is None:
            analysis_cache[rel_path] = None
            return None
        ranges = diff_ranges_by_file.get(rel_path, [])
        tokens = diff_tokens_by_file.get(rel_path, set())
        if rel_path.endswith(".py"):
            res = analyze_python_file(rel_path, src, ranges, tokens, root, known_files)
        else:
            res = analyze_generic_source_file(rel_path, src, ranges, tokens, root, known_files)
        analysis_cache[rel_path] = res
        return res

    # Track total characters and seen snippets for deduplication
    total_chars = 0
    seen_snippets: set[tuple[str, str, str, int]] = set()
    unstaged_neighbors_found: set[str] = set()

    def add_snippet(item: DependencySnippet) -> bool:
        nonlocal total_chars
        # Avoid duplicating code regions that are already present in the staged diff hunks
        if item.file in staged_set:
            file_diff_ranges = diff_ranges_by_file.get(item.file, [])
            if file_diff_ranges and _ranges_overlap(item.line_start, item.line_end, file_diff_ranges):
                return True
        key = (item.file, item.direction, item.symbol, item.line_start)
        if key in seen_snippets:
            return True
        seen_snippets.add(key)
        cost = len(item.snippet) + 120
        if context.truncated or total_chars + cost > resolved_max_chars:
            context.truncated = True
            context.truncated_chars += len(item.snippet)
            return False
        total_chars += cost
        context.snippets.append(item)
        if item.has_unstaged_edits:
            unstaged_neighbors_found.add(item.file)
        return True

    # Queue entries: (file_rel_path, active_defined_symbols, active_referenced_symbols, current_degree)
    queue: deque[tuple[str, set[str], set[str], int]] = deque()

    for sfile in sorted(staged_set):
        if Path(sfile).suffix.lower() not in SUPPORTED_SOURCE_EXTENSIONS:
            continue
        analysis = get_analysis(sfile)
        if not analysis:
            continue
        mod_syms = set(analysis.modified_symbols) or set(analysis.defined_symbols.keys())
        ref_syms = set(analysis.referenced_symbols)
        queue.append((sfile, mod_syms, ref_syms, 1))

    visited_file_degrees: dict[tuple[str, str], int] = {}

    while queue:
        curr_file, mod_syms, ref_syms, degree = queue.popleft()
        if degree > resolved_depth:
            continue

        curr_analysis = get_analysis(curr_file)
        if not curr_analysis:
            continue

        # 1. UPSTREAM: Symbols imported by `curr_file` that are referenced in diff/active scope
        upstream_candidates: list[tuple[str, str]] = []  # (upstream_file, orig_symbol)
        seen_up_pairs: set[tuple[str, str]] = set()

        for local_name, (upstream_file, orig_symbol) in sorted(curr_analysis.imported_symbol_map.items()):
            if ref_syms and local_name not in ref_syms and orig_symbol not in ref_syms:
                continue
            pair = (upstream_file, orig_symbol)
            if pair not in seen_up_pairs:
                seen_up_pairs.add(pair)
                upstream_candidates.append(pair)

        # Also inspect package/wildcard/sibling `imported_files` (Go packages, Java packages, C headers, etc.)
        for upstream_file in sorted(curr_analysis.imported_files):
            up_analysis = get_analysis(upstream_file)
            if not up_analysis:
                continue
            for def_sym in sorted(up_analysis.defined_symbols.keys()):
                if ref_syms and def_sym not in ref_syms:
                    continue
                pair = (upstream_file, def_sym)
                if pair not in seen_up_pairs:
                    seen_up_pairs.add(pair)
                    upstream_candidates.append(pair)

        for upstream_file, orig_symbol in upstream_candidates:
            up_analysis = get_analysis(upstream_file)
            if not up_analysis:
                continue

            if orig_symbol in up_analysis.defined_symbols:
                l_start, l_end, snippet_text = up_analysis.defined_symbols[orig_symbol]
            else:
                # Fallback to top of upstream file if symbol is dynamically exported
                up_src = get_source(upstream_file) or ""
                if not up_src.strip():
                    continue
                snippet_text, l_end = _format_numbered_lines(up_src.splitlines()[:20], 1, max_lines=20)
                l_start = 1

            has_unstaged = upstream_file in unstaged_set and upstream_file not in staged_set
            snippet_obj = DependencySnippet(
                file=upstream_file,
                direction="upstream",
                degree=degree,
                symbol=orig_symbol,
                source_file=curr_file,
                line_start=l_start,
                line_end=l_end,
                snippet=snippet_text,
                enclosing_symbol=orig_symbol,
                has_unstaged_edits=has_unstaged,
            )
            added = add_snippet(snippet_obj)

            if added and degree < resolved_depth:
                hop_key = (upstream_file, "upstream")
                if visited_file_degrees.get(hop_key, 999) > degree:
                    visited_file_degrees[hop_key] = degree
                    next_refs = {
                        m.group(1)
                        for m in IDENT_PATTERN.finditer(snippet_text)
                        if m.group(1) not in COMMON_KEYWORDS
                    }
                    queue.append((upstream_file, {orig_symbol}, next_refs, degree + 1))

        # 2. DOWNSTREAM: Production files that import/call `curr_file` and its `mod_syms`
        curr_stem = Path(curr_file).stem
        for cand_file in prod_files:
            if cand_file == curr_file:
                continue
            cand_src = get_source(cand_file)
            if not cand_src:
                continue

            # Quick substring pre-filter before parsing: candidate must mention either file stem or a modified symbol
            if (
                curr_stem != "__init__"
                and curr_stem not in cand_src
                and (not mod_syms or not any(sym in cand_src for sym in mod_syms))
            ):
                continue

            if cand_file.endswith(".py") and curr_file.endswith(".py"):
                callsites = find_python_downstream_callsites(
                    candidate_rel_path=cand_file,
                    candidate_source=cand_src,
                    target_rel_path=curr_file,
                    target_symbols=mod_syms,
                    root_dir=root,
                    known_files=known_files,
                )
            else:
                cand_analysis = get_analysis(cand_file)
                callsites = (
                    find_generic_downstream_callsites(
                        cand_analysis=cand_analysis,
                        cand_source=cand_src,
                        target_rel_path=curr_file,
                        target_symbols=mod_syms,
                    )
                    if cand_analysis
                    else []
                )

            has_unstaged = cand_file in unstaged_set and cand_file not in staged_set
            enclosing_for_next_hop: set[str] = set()

            for used_sym, enclosing_sym, l_start, l_end, snippet_text in callsites:
                snippet_obj = DependencySnippet(
                    file=cand_file,
                    direction="downstream",
                    degree=degree,
                    symbol=used_sym,
                    source_file=curr_file,
                    line_start=l_start,
                    line_end=l_end,
                    snippet=snippet_text,
                    enclosing_symbol=enclosing_sym,
                    has_unstaged_edits=has_unstaged,
                )
                if add_snippet(snippet_obj) and enclosing_sym:
                    enclosing_for_next_hop.add(enclosing_sym)

            if not context.truncated and degree < resolved_depth and enclosing_for_next_hop:
                hop_key = (cand_file, "downstream")
                if visited_file_degrees.get(hop_key, 999) > degree:
                    visited_file_degrees[hop_key] = degree
                    queue.append((cand_file, enclosing_for_next_hop, set(), degree + 1))

    context.unstaged_neighbor_files = sorted(unstaged_neighbors_found)
    return context
