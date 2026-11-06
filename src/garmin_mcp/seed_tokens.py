"""Seed the Garmin token store from an environment variable.

Container platforms (Coolify, Kubernetes, ...) make secrets easy to set as
environment variables and awkward to copy into a volume. ``garmin-mcp-auth``
writes ``garmin_tokens.json``; paste its content into ``GARMIN_TOKENS_JSON``
(raw JSON, or the same JSON base64-encoded) and the container entrypoint writes
it to the token store on start.

The store itself stays the source of truth while the server runs: the Garmin
client refreshes its access token and writes the result back to the file. So the
file is only (re)written when the environment value is new or has changed since
the last seeding, which is recorded as a hash in a marker file next to it.
Restarting with an unchanged variable therefore keeps tokens refreshed in a
persistent volume; setting a new value (for example after ``--force-reauth``)
replaces them.
"""

import base64
import binascii
import hashlib
import json
import os
import sys
from pathlib import Path

from garmin_mcp.token_utils import TOKEN_FILE_NAME, get_token_path

ENV_VAR = "GARMIN_TOKENS_JSON"
MARKER_NAME = ".garmin_tokens_json.sha256"


def _decode(raw: str) -> str:
    """Return the token JSON text from raw JSON or base64-encoded JSON.

    Error messages never include the value: it is a long-lived credential.
    """
    text = raw.strip()
    if not text.startswith("{"):
        try:
            text = base64.b64decode(text, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            raise ValueError("value is neither JSON nor base64-encoded JSON") from None
    try:
        data = json.loads(text)
    except ValueError:
        raise ValueError("value is not valid JSON") from None
    if not isinstance(data, dict) or not data.get("di_token") or not data.get("di_refresh_token"):
        raise ValueError("JSON has no di_token/di_refresh_token (is it garmin_tokens.json?)")
    return text


def _token_file(store: Path) -> Path:
    """Mirror garminconnect: a ``*.json`` path is the file, anything else a directory."""
    if store.suffix.casefold() == ".json" and not store.is_dir():
        return store
    return store / TOKEN_FILE_NAME


def _write_private(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically, readable by the owner only."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(content)
    os.replace(tmp, path)


def seed(environ=None) -> str:
    """Write the token file from ``GARMIN_TOKENS_JSON`` when needed.

    Returns ``"unset"`` (variable absent or blank), ``"unchanged"`` (already
    seeded from this exact value; the store is left alone) or ``"seeded"``.
    Raises ``ValueError`` when the variable is set but unusable.
    """
    environ = os.environ if environ is None else environ
    raw = environ.get(ENV_VAR, "")
    if not raw.strip():
        return "unset"

    text = _decode(raw)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

    token_file = _token_file(Path(get_token_path()))
    marker = token_file.with_name(MARKER_NAME)
    if token_file.is_file() and marker.is_file() and marker.read_text().strip() == digest:
        return "unchanged"

    _write_private(token_file, text)
    _write_private(marker, digest)
    return "seeded"


def main() -> int:
    try:
        status = seed()
    except ValueError as exc:
        print(f"ERROR: {ENV_VAR}: {exc}", file=sys.stderr)
        return 1
    if status != "unset":
        print(f"{ENV_VAR}: token store {status} ({get_token_path()})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
