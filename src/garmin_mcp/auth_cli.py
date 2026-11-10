"""Pre-authentication CLI tool for Garmin MCP server.

This tool allows users to authenticate with Garmin Connect and save OAuth tokens
before running the MCP server in non-interactive environments like Claude Desktop.
"""

import argparse
import contextlib
import json
import logging
import os
import stat
import sys
import getpass
import base64

import requests
from garminconnect import Garmin, GarminConnectAuthenticationError, GarminConnectConnectionError, GarminConnectTooManyRequestsError

from garmin_mcp.token_utils import (
    TOKEN_FILE_NAME,
    inspect_token_store,
    get_token_path,
    get_token_base64_path,
    token_exists,
    validate_tokens,
    get_token_info,
    resolve_token_path,
    secure_token_dir as _secure_token_dir,
)


def get_mfa() -> str:
    """Get MFA code from user input."""
    print("\nGarmin Connect MFA required. Please check your email/phone for the code.")
    return input("Enter MFA code: ")


def get_credentials(
    heading: str = "Garmin Connect Credentials",
    email_prompt: str = "Email: ",
    password_prompt: str = "Password: ",
) -> tuple[str, str]:
    """Get credentials from environment variables or user input.

    The prompts are parameters so that ``--export`` can ask in German.

    Returns:
        Tuple of (email, password)

    Raises:
        ValueError: If credentials cannot be obtained
    """
    # Try environment variables first
    email = os.environ.get("GARMIN_EMAIL")
    email_file = os.environ.get("GARMIN_EMAIL_FILE")

    if email and email_file:
        raise ValueError(
            "Must only provide one of GARMIN_EMAIL and GARMIN_EMAIL_FILE, got both"
        )
    elif email_file:
        with open(email_file, "r") as f:
            email = f.read().rstrip()

    password = os.environ.get("GARMIN_PASSWORD")
    password_file = os.environ.get("GARMIN_PASSWORD_FILE")

    if password and password_file:
        raise ValueError(
            "Must only provide one of GARMIN_PASSWORD and GARMIN_PASSWORD_FILE, got both"
        )
    elif password_file:
        with open(password_file, "r") as f:
            password = f.read().rstrip()

    # Prompt for missing credentials
    if not email:
        print(f"\n{heading}")
        print("-" * 40)
        email = input(email_prompt).strip()
        if not email:
            raise ValueError("Email is required")

    if not password:
        password = getpass.getpass(password_prompt)
        if not password:
            raise ValueError("Password is required")

    return email, password


def _verify_saved_tokens(token_path: str, is_cn: bool = False) -> tuple[bool, str]:
    """Independently confirm the freshly saved tokens actually authenticate.

    Performs a clean token-based login (which loads the social profile and
    raises on an unauthenticated session — unlike the ``return_on_mfa`` login
    used to obtain the tokens, which skips that check). This is what turns a
    silent "logged in as None" into a real failure.

    Returns:
        (True, account_name) on success, or (False, error_summary) on failure.
    """
    import io

    print("\nVerifying tokens...")

    old_stderr = sys.stderr
    sys.stderr = io.StringIO()
    try:
        garmin = Garmin(is_cn=is_cn)
        # login() raises unless it can fetch the profile, so getting past it
        # means the tokens work. The profile's fullName may still be empty.
        garmin.login(token_path)
        name = garmin.get_full_name() or garmin.display_name or "your Garmin account"
        return True, name
    except Exception as e:
        return False, str(e).split(":")[0].strip() or e.__class__.__name__
    finally:
        sys.stderr = old_stderr


def authenticate(token_path: str, token_base64_path: str, force_reauth: bool = False, is_cn: bool = False) -> bool:
    """Authenticate with Garmin Connect and save tokens.

    Args:
        token_path: Path to save token directory
        token_base64_path: Path to save base64 token file
        force_reauth: Force re-authentication even if tokens exist
        is_cn: Use Garmin Connect China (garmin.cn) instead of international

    Returns:
        bool: True if authentication succeeded, False otherwise
    """
    import io

    token_path = resolve_token_path(token_path)
    token_base64_path = resolve_token_path(token_base64_path)

    # Check if tokens already exist and are valid
    if not force_reauth and token_exists(token_path):
        print(f"\nChecking existing tokens in '{token_path}'...")

        # Suppress stderr during validation
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()

        try:
            is_valid, error_msg = validate_tokens(token_path, is_cn=is_cn)
        finally:
            sys.stderr = old_stderr

        if is_valid:
            print("✓ Existing tokens are valid. Authentication not needed.")
            print(f"  Use --force-reauth to generate new tokens.")
            return True
        else:
            print(f"✗ Existing tokens are invalid: {error_msg}")
            print("  Proceeding with re-authentication...\n")

    # Get credentials
    try:
        email, password = get_credentials()
    except ValueError as e:
        print(f"\nError: {e}", file=sys.stderr)
        return False

    # Authenticate with Garmin Connect
    region = "Garmin Connect CN (garmin.cn)" if is_cn else "Garmin Connect"
    print(f"\nAuthenticating with {region}...")
    print(f"Email: {email}")

    try:
        garmin = Garmin(email=email, password=password, is_cn=is_cn, prompt_mfa=get_mfa, return_on_mfa=True)
        # Exchange the credentials on the client directly. Garmin.login() with
        # no tokenstore falls back to $GARMINTOKENS and, if tokens load from
        # there, uses them instead of these credentials.
        result1, result2 = garmin.client.login(email, password, return_on_mfa=True)

        if result1 == "needs_mfa":
            mfa_code = get_mfa()
            garmin.resume_login(result2, mfa_code)

        # Save tokens to directory
        garmin.client.dump(token_path)
        _secure_token_dir(token_path)
        print(f"\n✓ OAuth tokens saved to: {token_path}")

        # The directory is the login source. An optional base64 export failure
        # must not skip verification or invalidate the saved directory tokens.
        try:
            token_json_path = os.path.join(token_path, "garmin_tokens.json")
            with open(token_json_path, "r") as f:
                token_data = f.read()
            token_base64 = base64.b64encode(token_data.encode()).decode()
            with open(token_base64_path, "w") as token_file:
                # A permission failure must occur before secrets are written.
                os.chmod(token_base64_path, 0o600)
                token_file.write(token_base64)
        except OSError as e:
            print(
                f"WARNING: OAuth tokens were saved to '{token_path}', but the optional "
                f"base64 export to '{token_base64_path}' failed: {e}",
                file=sys.stderr,
            )
        else:
            print(f"✓ OAuth tokens (base64) saved to: {token_base64_path}")

        # Verify tokens work with an independent token-based login. The login
        # above runs with return_on_mfa=True, which skips profile loading, so a
        # rate-limited run can "succeed" with an unauthenticated session. Check
        # rather than trust the dump, so a bad login fails loudly instead of
        # printing "Logged in as: None" and exiting 0.
        is_valid, name_or_err = _verify_saved_tokens(token_path, is_cn)
        if not is_valid:
            print(
                f"\n✗ OAuth tokens were saved to '{token_path}', but loading the "
                "profile/settings from the saved tokens failed.",
                file=sys.stderr,
            )
            print(f"  {name_or_err}", file=sys.stderr)
            print("  Token files were retained. Resolve the reported error and retry loading them.", file=sys.stderr)
            return False

        print(f"✓ Authentication successful!")
        print(f"  Logged in as: {name_or_err}")

        print("\n" + "=" * 60)
        print("SUCCESS: You can now use the Garmin MCP server!")
        print("=" * 60)
        print("\nNext steps:")
        print("1. Add the server to your MCP client (e.g., Claude Desktop)")
        print("2. No need to include GARMIN_EMAIL or GARMIN_PASSWORD in config")
        print("3. The server will use your saved OAuth tokens")
        print("\nTokens are valid for approximately 6 months.")

        return True

    except GarminConnectAuthenticationError as e:
        error_msg = str(e)
        print(f"\n✗ Authentication failed", file=sys.stderr)

        # Provide helpful hints based on error type
        if "MFA" in error_msg or "code" in error_msg.lower():
            print("  MFA code may be incorrect or expired.", file=sys.stderr)
            print("  Please request a new code and try again.", file=sys.stderr)
        elif "password" in error_msg.lower() or "credentials" in error_msg.lower():
            print("  Invalid email or password.", file=sys.stderr)
            print("  Please check your Garmin Connect credentials.", file=sys.stderr)
        else:
            print(f"  {error_msg}", file=sys.stderr)

        return False

    except GarminConnectTooManyRequestsError:
        print(f"\n✗ Too many requests. Please wait a few minutes and try again.", file=sys.stderr)
        return False

    except GarminConnectConnectionError as e:
        # 0.3.2 stops with AuthenticationError for rejected credentials;
        # connection errors preserve the exhausted login strategy's details.
        print("\n✗ Authentication request failed", file=sys.stderr)
        print(f"  {e}", file=sys.stderr)
        return False

    except requests.exceptions.HTTPError as e:
        print(f"\n✗ Network error", file=sys.stderr)

        if e.response is not None:
            if e.response.status_code == 429:
                print("  Rate limited. Please wait a few minutes and try again.", file=sys.stderr)
            elif e.response.status_code >= 500:
                print("  Garmin Connect is experiencing issues. Please try again later.", file=sys.stderr)
            else:
                print(f"  HTTP {e.response.status_code} error", file=sys.stderr)
        else:
            print("  Please check your internet connection.", file=sys.stderr)

        return False

    except Exception as e:
        error_msg = str(e)
        print(f"\n✗ Unexpected error", file=sys.stderr)

        # Only show detailed error in debug scenarios
        if "timeout" in error_msg.lower():
            print("  Connection timeout. Please check your internet connection.", file=sys.stderr)
        elif "connection" in error_msg.lower():
            print("  Network connection issue. Please check your internet.", file=sys.stderr)
        else:
            print(f"  {error_msg.split(':')[0]}", file=sys.stderr)

        return False

EXPORT_FILE_NAME = "garmin-zugang.json"
_EXPORT_KEYS = ("di_token", "di_refresh_token", "di_client_id")
_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: symlinks and junctions on Windows

# The texts of ``--export``. English is the default and lives here; ``--lang de`` selects the German texts of
# the fork-only module ``garmin_mcp.export_texts_de``. Placeholders: {path}, {parent}, {name}.
EXPORT_LANGUAGES = ("en", "de")
EXPORT_TEXTS_EN = {
    "banner": "Garmin MCP Pre-Authentication Tool",
    "heading": "Garmin credentials",
    "email_prompt": "Garmin email: ",
    "password_prompt": "Garmin password (not shown): ",
    "logging_in": "Logging in to Garmin Connect...",
    "mfa_hint": (
        "Garmin just sent you a code by e-mail. Look in your inbox, including the spam folder (or in your "
        "authenticator app, if you set one up). The code is valid for only a few minutes."
    ),
    "mfa_prompt": "Code from Garmin: ",
    "folder_missing": "The folder '{parent}' does not exist.",
    "is_link": "'{path}' is a link; refusing to write through it.",
    "not_regular": "'{path}' exists and is not a regular file.",
    "exists": "'{path}' already exists. Use --force to replace it.",
    "not_regular_replace": "'{path}' is not a regular file; refusing to replace it.",
    "no_profile": "Garmin returned no profile; the login cannot be used.",
    "no_tokens": "Garmin did not issue the expected credentials.",
    "creds_missing": "Email or password is missing (or given twice).",
    "login_failed": "Login failed. Check email, password and the code.",
    "too_many": "Too many requests. Wait a few minutes and try again.",
    "connection": "Garmin could not be reached or answered unexpectedly.",
    "write_failed": "The file '{path}' could not be written.",
    "unexpected": "Unexpected error ({name}).",
    "done": "Done. The file is here: {path}. Upload it to fatgu.ai and delete it afterwards.",
}


def export_texts(lang: str) -> dict[str, str]:
    """The texts of ``--export`` in ``lang``; an unknown language is an error."""
    if lang == "en":
        return EXPORT_TEXTS_EN
    if lang == "de":
        from garmin_mcp.export_texts_de import TEXTS

        return TEXTS
    raise ValueError(f"unknown language {lang!r}")


class ExportError(Exception):
    """The export cannot proceed; the message is safe to print."""


def get_export_mfa(texts: dict[str, str] = EXPORT_TEXTS_EN) -> str:
    """Asks for the Garmin code, after saying where it comes from."""
    print("\n" + texts["mfa_hint"])
    return input(texts["mfa_prompt"])


def default_export_path() -> str:
    """``~/Downloads/garmin-zugang.json``, with ``~`` taken from ``HOME`` (or the profile)."""
    return os.path.join(os.path.expanduser("~"), "Downloads", EXPORT_FILE_NAME)


def _is_link(st: os.stat_result) -> bool:
    """A symlink, or on Windows any reparse point (junction, mount point)."""
    return stat.S_ISLNK(st.st_mode) or bool(
        getattr(st, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _check_export_target(path: str, force: bool, texts: dict[str, str]) -> None:
    """Refuse a bad target before the member types a password.

    Never follows a link, never replaces anything without ``force``, and with
    ``force`` replaces only a regular file.
    """
    parent = os.path.dirname(path) or "."
    if not os.path.isdir(parent):
        raise ExportError(texts["folder_missing"].format(parent=parent))
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if _is_link(st):
        raise ExportError(texts["is_link"].format(path=path))
    if not stat.S_ISREG(st.st_mode):
        raise ExportError(texts["not_regular"].format(path=path))
    if not force:
        raise ExportError(texts["exists"].format(path=path))


def _write_export(
    path: str, content: str, force: bool, texts: dict[str, str] = EXPORT_TEXTS_EN
) -> None:
    """Write ``content`` to a new file readable by the owner only (POSIX)."""
    if force:
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            pass
        else:
            if _is_link(st) or not stat.S_ISREG(st.st_mode):
                raise ExportError(texts["not_regular_replace"].format(path=path))
            os.unlink(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content.encode("utf-8"))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(path)
        raise
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def export_token_file(
    path: str, force: bool = False, is_cn: bool = False, lang: str = "en"
) -> bool:
    """Log in afresh and write only the token JSON to ``path`` (L64).

    Always a password login in memory: saved tokens (``GARMINTOKENS``) are never
    read, no token store, temporary folder or base64 copy is written, and the
    folder's mode is left alone. The login is verified by reading the profile
    with the same client, and that client's ``dumps()`` is what is written, so
    no second refresh can invalidate the exported tokens. Prints the path,
    never a token, a password or a library error text; the library's own log
    lines (they carry Garmin's error text) are switched off for the run.
    """
    texts = export_texts(lang)
    path = os.path.abspath(os.path.expanduser(path))
    previously_disabled = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        return _export(path, force, is_cn, texts)
    finally:
        logging.disable(previously_disabled)


def _export(path: str, force: bool, is_cn: bool, texts: dict[str, str]) -> bool:
    try:
        _check_export_target(path, force, texts)
        email, password = get_credentials(
            texts["heading"], texts["email_prompt"], texts["password_prompt"]
        )
        garmin = Garmin(email=email, password=password, is_cn=is_cn)
        client = garmin.client
        print("\n" + texts["logging_in"])
        result, state = client.login(email, password, return_on_mfa=True)
        if result == "needs_mfa":
            client.resume_login(state, get_export_mfa(texts))
        profile = client.connectapi("/userprofile-service/socialProfile")
        if not isinstance(profile, dict) or not profile:
            raise ExportError(texts["no_profile"])
        data = json.loads(client.dumps())
        if not all(isinstance(data.get(key), str) and data[key] for key in _EXPORT_KEYS):
            raise ExportError(texts["no_tokens"])
        _write_export(
            path, json.dumps({key: data[key] for key in _EXPORT_KEYS}), force, texts
        )
    except ExportError as e:
        print(f"\n✗ {e}", file=sys.stderr)
        return False
    except ValueError:
        print("\n✗ " + texts["creds_missing"], file=sys.stderr)
        return False
    except GarminConnectAuthenticationError:
        print("\n✗ " + texts["login_failed"], file=sys.stderr)
        return False
    except GarminConnectTooManyRequestsError:
        print("\n✗ " + texts["too_many"], file=sys.stderr)
        return False
    except GarminConnectConnectionError:
        print("\n✗ " + texts["connection"], file=sys.stderr)
        return False
    except OSError:
        print("\n✗ " + texts["write_failed"].format(path=path), file=sys.stderr)
        return False
    except Exception as e:
        print("\n✗ " + texts["unexpected"].format(name=e.__class__.__name__), file=sys.stderr)
        return False
    print("\n" + texts["done"].format(path=path))
    return True


def verify_tokens(token_path: str, is_cn: bool = False) -> bool:
    """Verify existing tokens are valid.

    Args:
        token_path: Path to token directory
        is_cn: Use Garmin Connect China (garmin.cn) instead of international

    Returns:
        bool: True if tokens are valid, False otherwise
    """
    print(f"\nVerifying tokens in '{token_path}'...")

    info = get_token_info(token_path, is_cn=is_cn)

    if not info["exists"]:
        print(f"✗ Tokens not found at: {info['expanded_path']}")
        print("\nRun 'garmin-mcp-auth' without --verify to authenticate.")
        return False

    if info["valid"]:
        print(f"✓ Tokens are valid!")
        print(f"  Location: {info['expanded_path']}")
        print("\nYou can use the Garmin MCP server without re-authenticating.")
        return True
    else:
        print(f"✗ Tokens are invalid: {info['error']}")
        print("\nRun 'garmin-mcp-auth --force-reauth' to re-authenticate.")
        return False


def diagnose_tokens(token_path: str | None, is_cn: bool) -> bool:
    """Print where tokens are looked up, what is on disk, and whether they work.

    For "garmin-mcp-auth succeeded but the server still cannot log in": the
    usual cause is the MCP client starting the server with a different
    GARMINTOKENS (or HOME) than the shell that ran garmin-mcp-auth, so the
    two resolve to different directories.

    Returns:
        bool: True when a token file is present and authenticates.
    """
    info = inspect_token_store(token_path)
    base64_path = get_token_base64_path()

    print("\nToken diagnostics")
    print(f"  GARMINTOKENS         : {os.getenv('GARMINTOKENS') or '(not set)'}")
    print(f"  --token-path         : {token_path or '(not given)'}")
    print(f"  Token directory      : {info['path']}")
    print(f"    chosen             : {info['source']}")
    if not info["exists"]:
        print("    state              : MISSING")
    elif not info["is_dir"]:
        print(f"    state              : a file, not a directory (mode {info['mode']})")
    else:
        print(f"    state              : exists (mode {info['mode']})")
    if info["has_token_file"]:
        token_state = "present"
        if info["token_file_error"]:
            token_state += f", but {info['token_file_error']}"
    else:
        token_state = "MISSING"
    print(f"  {TOKEN_FILE_NAME:<21}: {token_state}")
    others = [name for name in info["files"] if name != TOKEN_FILE_NAME]
    if others:
        print(f"  Other files          : {', '.join(others)}")
    base64_state = "present" if os.path.exists(base64_path) else "missing"
    print(
        f"  Base64 copy          : {base64_path} ({base64_state}; written for other "
        "garminconnect tools, the server does not read it)"
    )
    region = "garmin.cn" if is_cn else "garmin.com"
    print(f"  Region               : {region} (GARMIN_IS_CN={os.getenv('GARMIN_IS_CN') or '(not set)'})")
    print(f"  The MCP server loads tokens only from {TOKEN_FILE_NAME} in the directory above.")

    if not info["has_token_file"] or info["token_file_error"]:
        print("\n✗ The server cannot load tokens from this directory.")
        print("  Run 'garmin-mcp-auth' (or --force-reauth), then start the server with the same")
        print("  GARMINTOKENS, or none, so both resolve to the directory above.")
        return False

    print("\nChecking the tokens against Garmin Connect...")
    is_valid, error_msg = validate_tokens(info["path"], is_cn=is_cn)
    if is_valid:
        print("✓ Tokens authenticate. If the server still fails, it is started with a")
        print("  different GARMINTOKENS or HOME than this shell; compare with the path above.")
        return True

    print(f"✗ Tokens are present but do not authenticate: {error_msg}")
    print("  Run 'garmin-mcp-auth --force-reauth'.")
    return False


def main():
    """Main entry point for the authentication CLI tool."""
    parser = argparse.ArgumentParser(
        description="Pre-authenticate with Garmin Connect for MCP server use",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Authenticate and save tokens (interactive)
  garmin-mcp-auth

  # Use environment variables for credentials
  GARMIN_EMAIL=you@example.com GARMIN_PASSWORD=secret garmin-mcp-auth

  # Verify existing tokens
  garmin-mcp-auth --verify

  # Show where tokens are looked up, what is there, and whether they work
  garmin-mcp-auth --diagnose

  # Force re-authentication
  garmin-mcp-auth --force-reauth

  # Use custom token location
  garmin-mcp-auth --token-path ~/.garmin_tokens

  # Write only a token file for uploading to a website (no token store is kept)
  garmin-mcp-auth --export
        """
    )

    parser.add_argument(
        "--token-path",
        type=str,
        default=None,
        help="Custom token storage directory (default: ~/.garminconnect or $GARMINTOKENS)"
    )

    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify existing tokens without re-authenticating"
    )

    parser.add_argument(
        "--diagnose",
        action="store_true",
        help=(
            "Print the resolved token paths, what is on disk, and whether the "
            "tokens authenticate; use when the server cannot find or use tokens"
        ),
    )

    parser.add_argument(
        "--force-reauth",
        action="store_true",
        help="Force re-authentication even if valid tokens exist"
    )

    parser.add_argument(
        "--is-cn",
        action="store_true",
        default=None,
        help="Use Garmin Connect China (garmin.cn) instead of the international version"
    )

    parser.add_argument(
        "--export",
        nargs="?",
        const=default_export_path,
        default=None,
        metavar="PATH",
        help=(
            "Log in afresh and write only the token file to PATH (default "
            f"~/Downloads/{EXPORT_FILE_NAME}), keeping no token store; for uploading to the website"
        ),
    )

    parser.add_argument(
        "--lang",
        choices=EXPORT_LANGUAGES,
        default="en",
        help="With --export: the language of its prompts and messages (default: en)",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="With --export: replace an existing regular file at PATH",
    )

    args = parser.parse_args()

    # Get token paths
    token_path = args.token_path or get_token_path()
    token_base64_path = get_token_base64_path()

    # Resolve is_cn: CLI flag takes priority, then env var, then default False
    if args.is_cn:
        is_cn = True
    else:
        is_cn = os.getenv("GARMIN_IS_CN", "false").lower() in ("true", "1", "yes")

    print("\n" + "=" * 60)
    print(
        export_texts(args.lang)["banner"]
        if args.export is not None
        else "Garmin MCP Pre-Authentication Tool"
    )
    if is_cn:
        print("Region: China (garmin.cn)")
    print("=" * 60)

    # Export mode: a fresh login, only the token file, no store
    if args.export is not None:
        target = args.export() if callable(args.export) else args.export
        sys.exit(0 if export_token_file(target, args.force, is_cn, args.lang) else 1)

    # Diagnose mode: explain what the server will find at the token path
    if args.diagnose:
        success = diagnose_tokens(args.token_path, is_cn)
        sys.exit(0 if success else 1)

    # Verify mode
    if args.verify:
        success = verify_tokens(token_path, is_cn)
        sys.exit(0 if success else 1)

    # Authenticate mode
    success = authenticate(token_path, token_base64_path, args.force_reauth, is_cn)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
