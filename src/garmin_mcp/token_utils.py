"""Token management utilities for Garmin MCP authentication."""

import json
import os
import stat
from pathlib import Path
from typing import Tuple

from garminconnect import (
    Garmin,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

# The one file garminconnect reads from the token directory.
TOKEN_FILE_NAME = "garmin_tokens.json"


def resolve_token_path(path: str) -> str:
    """Resolve environment variables and the user-home marker in a token path.

    Some MCP clients leave ``${HOME}`` unresolved when it comes from a nested
    user-config default. The explicit replacement also covers Windows, where
    ``HOME`` may be unset but Python can still resolve ``~`` via ``USERPROFILE``.
    """
    expanded = os.path.expandvars(path)
    expanded = expanded.replace("${HOME}", os.path.expanduser("~"))
    return os.path.expanduser(expanded)


def secure_token_dir(path: str) -> None:
    """Set owner-only permissions on a token directory and the files inside it.

    OAuth tokens are ~6-month bearer credentials to the full Garmin account, so
    they must not be left world-readable on multi-user hosts. Safe to call on a
    path that is a single file rather than a directory.
    """
    expanded = resolve_token_path(path)
    if not os.path.exists(expanded):
        return
    if os.path.isdir(expanded):
        os.chmod(expanded, 0o700)
        for entry in os.scandir(expanded):
            if entry.is_file():
                os.chmod(entry.path, 0o600)
    else:
        os.chmod(expanded, 0o600)


class TokenStoreError(Exception):
    """The token store itself cannot be trusted (it is a symlink)."""


_MAX_STORE_DEPTH = 8


def _tighten(path: str, mode: int, problems: list) -> None:
    """chmod one entry, never through a symlink; a failure is collected, not raised."""
    try:
        if stat.S_ISLNK(os.lstat(path).st_mode):
            return
        os.chmod(path, mode)
    except OSError as exc:
        problems.append(f"{path}: {exc.strerror or exc}")


def _tighten_tree(directory: str, problems: list, depth: int = 0) -> None:
    _tighten(directory, 0o700, problems)
    try:
        with os.scandir(directory) as scan:
            entries = list(scan)
    except OSError as exc:
        problems.append(f"{directory}: {exc.strerror or exc}")
        return
    # The token file first: it is the one that matters if anything fails.
    entries.sort(key=lambda entry: (entry.name != TOKEN_FILE_NAME, entry.name))
    for entry in entries:
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                if depth < _MAX_STORE_DEPTH:
                    _tighten_tree(entry.path, problems, depth + 1)
            elif entry.is_file(follow_symlinks=False):
                _tighten(entry.path, 0o600, problems)
        except OSError as exc:
            problems.append(f"{entry.path}: {exc.strerror or exc}")


def secure_token_store(path: str, extra_files=()) -> list:
    """Tighten a token store to 0700 (directories) and 0600 (files), entry by entry.

    Every entry has its own try, so one that cannot be changed (another owner,
    a read-only mount) does not stop the others. Symlinks are skipped, so a
    link inside the store cannot be used to change the mode of a file outside
    it, and a store path that is itself a symlink is refused with
    ``TokenStoreError``. ``extra_files`` are single files (the legacy base64
    export) tightened the same way. Returns the list of entries that failed.
    """
    expanded = resolve_token_path(path)
    if os.path.islink(expanded):
        raise TokenStoreError(f"token store '{expanded}' is a symlink; refusing to use it")
    problems: list = []
    if os.path.isdir(expanded):
        _tighten_tree(expanded, problems)
    elif os.path.exists(expanded):
        _tighten(expanded, 0o600, problems)
    for extra in extra_files:
        extra = resolve_token_path(extra)
        if os.path.lexists(extra):
            _tighten(extra, 0o600, problems)
    return problems


def verify_token_store(path: str) -> list:
    """Check, with lstat, that the store and its token file are private and ours.

    Returns one message per problem: a mode that grants anything to group or
    others, or an owner other than this process's effective user.
    """
    expanded = resolve_token_path(path)
    if not os.path.lexists(expanded):
        return []
    targets = [expanded]
    token_file = os.path.join(expanded, TOKEN_FILE_NAME)
    if os.path.isdir(expanded) and os.path.lexists(token_file):
        targets.append(token_file)
    euid = os.geteuid() if hasattr(os, "geteuid") else None
    problems = []
    for target in targets:
        info = os.lstat(target)
        if info.st_mode & 0o077:
            problems.append(
                f"{target} has mode {oct(stat.S_IMODE(info.st_mode))}, open to other users"
            )
        if euid is not None and info.st_uid != euid:
            problems.append(f"{target} is owned by uid {info.st_uid}, not by this process")
    return problems


def get_token_path() -> str:
    """Get token path from environment or default.

    Returns:
        str: Path to token storage directory
    """
    return resolve_token_path(os.getenv("GARMINTOKENS") or "~/.garminconnect")


def get_token_base64_path() -> str:
    """Get base64 token file path from environment or default.

    Returns:
        str: Path to base64 token file
    """
    return resolve_token_path(
        os.getenv("GARMINTOKENS_BASE64") or "~/.garminconnect_base64"
    )


def token_exists(token_path: str = None) -> bool:
    """Check if token directory or file exists.

    Args:
        token_path: Optional custom token path. Uses default if not provided.

    Returns:
        bool: True if tokens exist, False otherwise
    """
    if token_path is None:
        token_path = get_token_path()

    expanded_path = Path(resolve_token_path(token_path))
    return expanded_path.exists()


def validate_tokens(token_path: str = None, is_cn: bool = False) -> Tuple[bool, str]:
    """Validate tokens by attempting to use them.

    Args:
        token_path: Optional custom token path. Uses default if not provided.
        is_cn: Use Garmin Connect China (garmin.cn) instead of international.

    Returns:
        Tuple of (is_valid, error_message). error_message is empty string if valid.
    """
    import sys
    import io

    if token_path is None:
        token_path = get_token_path()
    token_path = resolve_token_path(token_path)

    # Check if tokens exist
    if not token_exists(token_path):
        return False, f"Token directory not found: {token_path}"

    # Suppress stderr during validation to avoid confusing library error messages
    old_stderr = sys.stderr
    sys.stderr = io.StringIO()

    try:
        garmin = Garmin(is_cn=is_cn)
        garmin.login(token_path)

        # Try a simple API call to verify tokens work
        try:
            # Use get_full_name() as it doesn't require parameters
            garmin.get_full_name()
            return True, ""
        except Exception as e:
            # Extract clean error message
            error_msg = str(e)
            if "401" in error_msg or "Unauthorized" in error_msg:
                return False, "Tokens expired or invalid"
            elif "403" in error_msg or "Forbidden" in error_msg:
                return False, "Access denied with current tokens"
            else:
                return False, f"Authentication failed: {error_msg.split(':')[0]}"

    except FileNotFoundError:
        return False, f"Token files not found in: {token_path}"
    except GarminConnectConnectionError as e:
        error_msg = str(e)
        if "401" in error_msg or "Unauthorized" in error_msg:
            return False, "Tokens expired or invalid"
        elif "403" in error_msg or "Forbidden" in error_msg:
            return False, "Access denied with current tokens"
        else:
            return False, f"Authentication error: {error_msg.split(':')[0]}"
    except Exception as e:
        error_msg = str(e)
        # Clean up error message
        if "401" in error_msg:
            return False, "Tokens expired or invalid"
        else:
            return False, f"Validation error: {error_msg.split(':')[0]}"
    finally:
        # Restore stderr
        sys.stderr = old_stderr


def remove_tokens(token_path: str = None, base64_path: str = None) -> None:
    """Safely remove stored tokens.

    Args:
        token_path: Optional custom token directory path. Uses default if not provided.
        base64_path: Optional custom base64 token file path. Uses default if not provided.
    """
    import shutil

    if token_path is None:
        token_path = get_token_path()
    if base64_path is None:
        base64_path = get_token_base64_path()
    token_path = resolve_token_path(token_path)
    base64_path = resolve_token_path(base64_path)

    # Remove token directory
    expanded_token_path = Path(token_path)
    if expanded_token_path.exists():
        if expanded_token_path.is_dir():
            shutil.rmtree(expanded_token_path)
        else:
            expanded_token_path.unlink()

    # Remove base64 token file
    expanded_base64_path = Path(base64_path)
    if expanded_base64_path.exists():
        expanded_base64_path.unlink()


def get_token_info(token_path: str = None, is_cn: bool = False) -> dict:
    """Get information about stored tokens.

    Args:
        token_path: Optional custom token path. Uses default if not provided.
        is_cn: Use Garmin Connect China (garmin.cn) instead of international.

    Returns:
        dict: Token information including existence, validity, and path
    """
    if token_path is None:
        token_path = get_token_path()
    token_path = resolve_token_path(token_path)

    exists = token_exists(token_path)
    is_valid = False
    error_msg = ""

    if exists:
        is_valid, error_msg = validate_tokens(token_path, is_cn=is_cn)

    return {
        "path": token_path,
        "expanded_path": token_path,
        "exists": exists,
        "valid": is_valid,
        "error": error_msg
    }


def inspect_token_store(token_path: str = None) -> dict:
    """Describe the token store on disk without contacting Garmin.

    Args:
        token_path: Explicit token directory (or garmin_tokens.json file).
            None resolves it the way the server does: $GARMINTOKENS, else
            ~/.garminconnect.

    Returns:
        dict with "path" (resolved), "source" (how the path was chosen, and
        whether $GARMINTOKENS points somewhere else), "exists", "is_dir",
        "token_file" (the garmin_tokens.json path), "has_token_file",
        "token_file_error" ("" when the file parses and holds tokens),
        "files" (names in the directory) and "mode" (permission bits, octal,
        or None when the path is missing).
    """
    raw_env = os.getenv("GARMINTOKENS")
    default_path = resolve_token_path("~/.garminconnect")
    env_path = resolve_token_path(raw_env) if raw_env else None
    if token_path:
        path = resolve_token_path(token_path)
    else:
        path = env_path or default_path

    if env_path and path == env_path:
        source = f"from GARMINTOKENS={raw_env}"
    elif path == default_path and not raw_env:
        source = "the default; GARMINTOKENS is not set"
    elif raw_env:
        source = f"given explicitly; GARMINTOKENS={raw_env} points elsewhere"
    else:
        source = "given explicitly"

    store = Path(path)
    token_file = store if store.is_file() else store / TOKEN_FILE_NAME
    info = {
        "path": path,
        "source": source,
        "exists": store.exists(),
        "is_dir": store.is_dir(),
        "token_file": str(token_file),
        "has_token_file": token_file.is_file(),
        "token_file_error": "",
        "files": [],
        "mode": None,
    }
    if store.is_dir():
        try:
            info["files"] = sorted(child.name for child in store.iterdir())
        except OSError:
            pass
    try:
        info["mode"] = oct(store.stat().st_mode & 0o777)
    except OSError:
        pass
    if info["has_token_file"]:
        try:
            data = json.loads(token_file.read_text())
        except (OSError, ValueError) as exc:
            info["token_file_error"] = f"unreadable or not JSON ({exc})"
        else:
            if (
                not isinstance(data, dict)
                or not data.get("di_token")
                or not data.get("di_refresh_token")
            ):
                info["token_file_error"] = "no di_token/di_refresh_token inside"
    return info


def _looks_like_rejection(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in ("401", "403", "unauthorized", "forbidden"))


def describe_token_failure(token_path: str, error: BaseException) -> str:
    """Explain why a login from saved tokens failed, with the resolved path.

    garminconnect swallows the token load error and raises the same
    GarminConnectAuthenticationError ("Username and password are required")
    whether the directory is missing, the file is corrupt, or garmin-mcp-auth
    wrote to a different path. So the explanation comes from the directory
    state first and from the exception second.
    """
    info = inspect_token_store(token_path)
    where = f"'{info['path']}' ({info['source']})"
    text = str(error).strip()
    detail = text.splitlines()[0] if text else error.__class__.__name__

    if isinstance(error, GarminConnectTooManyRequestsError):
        return (
            f"Garmin rate-limited the login (429) while loading tokens from {where}. "
            "Wait a few minutes and retry."
        )
    if not info["exists"]:
        return (
            f"No token directory at {where}. Run garmin-mcp-auth; it saves tokens "
            "there unless GARMINTOKENS or --token-path points elsewhere."
        )
    if not info["has_token_file"]:
        found = ", ".join(info["files"]) or "nothing"
        return (
            f"Token directory {where} exists but has no {TOKEN_FILE_NAME} "
            f"(found: {found}). Run garmin-mcp-auth, or point GARMINTOKENS at the "
            "directory it wrote to."
        )
    if info["token_file_error"]:
        return (
            f"{info['token_file']} could not be loaded: {info['token_file_error']}. "
            "Run garmin-mcp-auth --force-reauth."
        )
    if isinstance(error, GarminConnectConnectionError) and not _looks_like_rejection(detail):
        return f"Could not reach Garmin Connect to check the tokens in {where}: {detail}"
    return (
        f"Tokens in {where} loaded but Garmin rejected them (expired, revoked, or "
        f"for the other region): {detail}. Run garmin-mcp-auth --force-reauth."
    )
