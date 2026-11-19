#!/usr/bin/env python3
"""Credential and authentication manager for `agentic-dev`.

Manages user-global credentials (`~/.config/agentic-dev/credentials.env` by default)
and optional repository `.env` overrides without baking secrets into binaries or
version control.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from scripts.bundled_assets import ENV_FILENAME, merge_env_file
    from scripts.constants import (
        DEFAULT_CACHE_DIR_PATH,
        ensure_utf8_stdio,
        get_global_credentials_file,
    )
    from scripts.gemini_client import ENV_API_KEY, get_adc_access_token, sanitize_api_key
    from scripts.sync_org_rules import parse_env_file
except ImportError:
    from bundled_assets import ENV_FILENAME, merge_env_file
    from constants import (
        DEFAULT_CACHE_DIR_PATH,
        ensure_utf8_stdio,
        get_global_credentials_file,
    )
    from gemini_client import ENV_API_KEY, get_adc_access_token, sanitize_api_key
    from sync_org_rules import parse_env_file


@dataclass(frozen=True)
class AuthProviderSpec:
    """Contract describing a configurable LLM credential provider."""

    name: str
    env_var: str
    display_name: str


AUTH_PROVIDERS: dict[str, AuthProviderSpec] = {
    "google_ai": AuthProviderSpec(
        name="google_ai",
        env_var=ENV_API_KEY,
        display_name="Google AI Studio",
    ),
}
DEFAULT_AUTH_PROVIDER = "google_ai"


@dataclass(frozen=True)
class AuthStatus:
    """Resolved credential status across environment, repo .env, global config, and ADC."""

    provider: str
    env_var: str
    display_name: str
    configured: bool
    source: str
    source_label: str
    masked_key: str | None
    repo_env_file: str
    global_credentials_file: str
    has_repo_override: bool
    has_global_credential: bool
    has_adc: bool = False


def resolve_provider_spec(provider: str = DEFAULT_AUTH_PROVIDER) -> AuthProviderSpec:
    """Return provider metadata or raise ValueError for unknown providers."""
    normalized = provider.strip().lower()
    if normalized not in AUTH_PROVIDERS:
        supported = ", ".join(sorted(AUTH_PROVIDERS))
        raise ValueError(f"Unsupported provider '{provider}'. Supported: {supported}")
    return AUTH_PROVIDERS[normalized]


def mask_secret(secret: str) -> str:
    """Return a redacted preview of a secret credential for safe terminal display."""
    cleaned = secret.strip()
    if len(cleaned) <= 8:
        return "*" * len(cleaned)
    return f"{cleaned[:4]}...{cleaned[-4:]}"


def _write_secure_file(target_file: Path, content: str) -> None:
    """Write a sensitive file with owner-only permissions (0600) on POSIX systems."""
    target_file.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(target_file.parent, stat.S_IRWXU)
        except OSError:
            pass
        fd = os.open(
            str(target_file),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            stat.S_IRUSR | stat.S_IWUSR,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        try:
            os.chmod(target_file, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        return

    target_file.write_text(content, encoding="utf-8")


def _update_dotenv_key(
    env_file: Path,
    env_var: str,
    value: str | None,
    secure: bool = False,
) -> bool:
    """Set or remove `env_var` in a dotenv file, preserving unrelated lines.

    Returns True when setting a value or when removing an existing key.
    """
    existing_lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.is_file() else []
    key_pattern = re.compile(rf"^(?:export\s+)?{re.escape(env_var)}\s*=")

    new_lines: list[str] = []
    found = False
    for line in existing_lines:
        if key_pattern.match(line.strip()):
            found = True
            if value is not None:
                new_lines.append(f"{env_var}={value}")
        else:
            new_lines.append(line)

    if value is None:
        if not found:
            return False
        updated_text = ("\n".join(new_lines).rstrip() + "\n") if new_lines else ""
    else:
        if not found:
            if not existing_lines:
                new_lines.append("# User-global agentic-dev credentials (managed by `agentic-dev auth`)")
            new_lines.append(f"{env_var}={value}")
        updated_text = "\n".join(new_lines).rstrip() + "\n"

    if secure:
        _write_secure_file(env_file, updated_text)
    else:
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text(updated_text, encoding="utf-8")
    return True


def is_env_file_gitignored(root_dir: Path, filename: str = ENV_FILENAME) -> bool | None:
    """Check whether `filename` is ignored by Git inside `root_dir`.

    Returns True if ignored, False if inside a Git repo and NOT ignored (or tracked),
    or None if `root_dir` is not a Git repository or `git` is unavailable.
    """
    if not (root_dir / ".git").exists() or shutil.which("git") is None:
        return None
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", filename],
        cwd=root_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if tracked.returncode == 0:
        return False
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", filename],
        cwd=root_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    return ignored.returncode == 0


def inspect_auth_status(
    root_dir: Path | str = ".",
    provider: str = DEFAULT_AUTH_PROVIDER,
    include_adc: bool = True,
) -> AuthStatus:
    """Inspect active credential source and status without modifying os.environ."""
    spec = resolve_provider_spec(provider)
    resolved_root = Path(root_dir).resolve()
    repo_env_file = resolved_root / ENV_FILENAME
    global_creds_file = get_global_credentials_file()

    env_key = sanitize_api_key(os.environ.get(spec.env_var))
    repo_key = sanitize_api_key(parse_env_file(repo_env_file).get(spec.env_var))
    global_key = sanitize_api_key(parse_env_file(global_creds_file).get(spec.env_var))

    if env_key:
        effective_key = env_key
        if env_key == repo_key:
            source = "repo_env"
            source_label = f"repo {ENV_FILENAME} ({repo_env_file})"
        elif env_key == global_key and not repo_key:
            source = "global_config"
            source_label = f"user-global ({global_creds_file})"
        else:
            source = "environment"
            source_label = f"environment variable ({spec.env_var})"
    elif repo_key:
        effective_key = repo_key
        source = "repo_env"
        source_label = f"repo {ENV_FILENAME} ({repo_env_file})"
    elif global_key:
        effective_key = global_key
        source = "global_config"
        source_label = f"user-global ({global_creds_file})"
    else:
        effective_key = None
        source = "none"
        source_label = "not configured"

    has_adc = False
    if include_adc:
        has_adc = bool(get_adc_access_token(resolved_root / DEFAULT_CACHE_DIR_PATH))

    return AuthStatus(
        provider=spec.name,
        env_var=spec.env_var,
        display_name=spec.display_name,
        configured=bool(effective_key),
        source=source,
        source_label=source_label,
        masked_key=mask_secret(effective_key) if effective_key else None,
        repo_env_file=str(repo_env_file),
        global_credentials_file=str(global_creds_file),
        has_repo_override=bool(repo_key),
        has_global_credential=bool(global_key),
        has_adc=has_adc,
    )


def _validate_secret_input(raw_key: str) -> str:
    """Validate and clean user-supplied API key input."""
    if "\n" in raw_key or "\r" in raw_key:
        raise ValueError("API key must be a single line without newline characters.")
    cleaned = sanitize_api_key(raw_key)
    if not cleaned:
        raise ValueError("API key cannot be empty or a template placeholder.")
    return cleaned


def set_credential(
    raw_key: str,
    root_dir: Path | str = ".",
    provider: str = DEFAULT_AUTH_PROVIDER,
    local: bool = False,
) -> tuple[Path, str, list[str]]:
    """Persist an API key to user-global config (default) or repo `.env`.

    Returns `(target_path, masked_preview, warnings)`.
    """
    spec = resolve_provider_spec(provider)
    cleaned_key = _validate_secret_input(raw_key)
    resolved_root = Path(root_dir).resolve()
    warnings: list[str] = []

    if local:
        target_file = resolved_root / ENV_FILENAME
        merge_env_file(target_file, overrides={spec.env_var: cleaned_key})
        if is_env_file_gitignored(resolved_root, ENV_FILENAME) is False:
            warnings.append(
                f"{ENV_FILENAME} is not ignored by Git in {resolved_root}; "
                "add `.env` to `.gitignore` before committing."
            )
    else:
        target_file = get_global_credentials_file()
        _update_dotenv_key(target_file, spec.env_var, cleaned_key, secure=True)
        if sanitize_api_key(parse_env_file(resolved_root / ENV_FILENAME).get(spec.env_var)):
            warnings.append(
                f"This workspace's {ENV_FILENAME} defines {spec.env_var} and overrides the global key."
            )

    if sanitize_api_key(os.environ.get(spec.env_var)):
        warnings.append(
            f"Shell environment variable {spec.env_var} is currently set and takes precedence."
        )

    return target_file, mask_secret(cleaned_key), warnings


def clear_credential(
    root_dir: Path | str = ".",
    provider: str = DEFAULT_AUTH_PROVIDER,
    local: bool = False,
) -> tuple[Path, bool]:
    """Remove a stored credential from user-global config or repo `.env`."""
    spec = resolve_provider_spec(provider)
    resolved_root = Path(root_dir).resolve()
    target_file = (resolved_root / ENV_FILENAME) if local else get_global_credentials_file()
    removed = _update_dotenv_key(target_file, spec.env_var, None, secure=not local)
    return target_file, removed


def read_secret_input(spec: AuthProviderSpec, use_stdin: bool = False) -> str:
    """Read credential interactively without echo, or from stdin when `--stdin` is set."""
    if use_stdin:
        return sys.stdin.read().strip()
    return getpass.getpass(f"Enter {spec.display_name} API key ({spec.env_var}): ").strip()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentic-dev auth",
        description="Manage LLM provider credentials without embedding secrets in repositories.",
    )
    subparsers = parser.add_subparsers(dest="action")

    def add_common_args(sub: argparse.ArgumentParser, include_local: bool = False) -> None:
        sub.add_argument(
            "--root-dir",
            default=".",
            help="Repository root directory (default: .)",
        )
        sub.add_argument(
            "--provider",
            default=DEFAULT_AUTH_PROVIDER,
            choices=sorted(AUTH_PROVIDERS),
            help=f"Credential provider (default: {DEFAULT_AUTH_PROVIDER})",
        )
        if include_local:
            sub.add_argument(
                "--local",
                action="store_true",
                help="Target repository-local .env instead of user-global credentials",
            )

    status_parser = subparsers.add_parser(
        "status",
        help="Show resolved credential status and source (default action)",
    )
    add_common_args(status_parser)
    status_parser.add_argument(
        "--json",
        action="store_true",
        help="Output auth status as JSON",
    )

    set_parser = subparsers.add_parser(
        "set",
        help="Store or update an API key securely (interactive prompt or --stdin)",
    )
    add_common_args(set_parser, include_local=True)
    set_parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read the API key from standard input (for non-interactive scripts)",
    )

    clear_parser = subparsers.add_parser(
        "clear",
        help="Remove a stored API key from user-global config or repo .env",
    )
    add_common_args(clear_parser, include_local=True)

    parser.add_argument(
        "--root-dir",
        default=".",
        help="Repository root directory when checking status (default: .)",
    )
    parser.add_argument(
        "--provider",
        default=DEFAULT_AUTH_PROVIDER,
        choices=sorted(AUTH_PROVIDERS),
        help=f"Credential provider when checking status (default: {DEFAULT_AUTH_PROVIDER})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output auth status as JSON",
    )
    return parser


def _print_status(status: AuthStatus, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(asdict(status), indent=2))
        return

    print(f"=== agentic-dev auth ({status.display_name}) ===")
    if status.configured:
        print(f"API Key ({status.env_var}): ✅ Configured ({status.masked_key})")
        print(f"Active Source:  {status.source_label}")
    elif status.has_adc:
        print(f"API Key ({status.env_var}): Not set")
        print("Fallback Auth:  ✅ Google Cloud ADC token available")
    else:
        print(f"API Key ({status.env_var}): ⚠️  Not configured")
        print("Next Step:      Run `agentic-dev auth set` to store a personal key globally.")
    print(f"Global Store:   {status.global_credentials_file}")
    print(f"Repo .env:      {status.repo_env_file}")


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for `agentic-dev auth`."""
    ensure_utf8_stdio()
    parser = _build_parser()
    args = parser.parse_args(argv)
    action = args.action or "status"

    if action == "status":
        status = inspect_auth_status(
            root_dir=args.root_dir,
            provider=args.provider,
            include_adc=True,
        )
        _print_status(status, as_json=getattr(args, "json", False))
        return 0

    if action == "set":
        spec = resolve_provider_spec(args.provider)
        try:
            raw_key = read_secret_input(spec, use_stdin=args.stdin)
            target_file, masked, warnings = set_credential(
                raw_key=raw_key,
                root_dir=args.root_dir,
                provider=args.provider,
                local=args.local,
            )
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.", file=sys.stderr)
            return 1
        except ValueError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            return 1

        scope = "repository .env" if args.local else "user-global config"
        print(f"✅ Saved {spec.env_var} ({masked}) to {scope}: {target_file}")
        for warning in warnings:
            print(f"⚠️  {warning}")
        return 0

    if action == "clear":
        spec = resolve_provider_spec(args.provider)
        target_file, removed = clear_credential(
            root_dir=args.root_dir,
            provider=args.provider,
            local=args.local,
        )
        if removed:
            print(f"🗑️  Removed {spec.env_var} from {target_file}")
        else:
            print(f"ℹ️  {spec.env_var} was not present in {target_file}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
