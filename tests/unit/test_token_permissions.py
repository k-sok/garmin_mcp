"""Token files are readable only by their owner (#8, L48, PYSEC-2026-3467).

garminconnect <= 0.3.4 writes the token store with ``write_text``, so the file
mode follows the umask: 0644 under 022, and again on every refresh. The server
and the seeding therefore set umask 077 first and tighten an existing store.
"""

import json
import os
import stat
import sys
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp import FastMCP

import garmin_mcp
from garmin_mcp import seed_tokens

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")

TOKENS = {"di_token": "access-1", "di_refresh_token": "refresh-1"}


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _start_server(monkeypatch, init_api):
    """Run main() up to app.run(), waiting for the background login to finish."""
    finished = threading.Event()

    def login(email, password):
        try:
            return init_api()
        finally:
            finished.set()

    monkeypatch.setattr(garmin_mcp, "init_api", login)
    monkeypatch.setattr(sys, "stdout", sys.stdout)  # main() swaps it; restore after

    def capture_run(self, **_kwargs):
        assert finished.wait(10), "the login thread never ran"

    monkeypatch.setattr(FastMCP, "run", capture_run)
    garmin_mcp.main()


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "tokens"
    monkeypatch.setenv("GARMINTOKENS", str(path))
    for name in ("GARMIN_FILE_TOOLS", "GARMIN_TOOL_PROFILE", "GARMIN_ENABLED_TOOLS",
                 "GARMIN_DISABLED_TOOLS", "GARMIN_MCP_TRANSPORT", "GARMIN_TOKENS_JSON"):
        monkeypatch.delenv(name, raising=False)
    return path


def _library_writes_tokens(store: Path):
    """What garminconnect <= 0.3.4 does: mkdir and write_text, umask decides."""
    store.mkdir(parents=True, exist_ok=True)
    (store / "garmin_tokens.json").write_text(json.dumps(TOKENS))
    return Mock()


def test_tokens_written_under_a_loose_umask_are_private(store, monkeypatch):
    os.umask(0o022)
    token_file = store / "garmin_tokens.json"

    _start_server(monkeypatch, lambda: _library_writes_tokens(store))

    assert _mode(token_file) == 0o600
    assert _mode(store) == 0o700

    # a later refresh replaces the file; the process umask must still hold
    token_file.unlink()
    token_file.write_text(json.dumps(dict(TOKENS, di_token="access-2")))
    assert _mode(token_file) == 0o600


def test_an_existing_open_token_store_is_tightened_at_start(store, monkeypatch):
    os.umask(0o022)
    store.mkdir()
    token_file = store / "garmin_tokens.json"
    token_file.write_text(json.dumps(TOKENS))
    os.chmod(store, 0o755)
    os.chmod(token_file, 0o644)

    _start_server(monkeypatch, Mock)

    assert _mode(token_file) == 0o600
    assert _mode(store) == 0o700


def test_a_token_file_path_is_tightened_too(tmp_path, monkeypatch):
    token_file = tmp_path / "garmin_tokens.json"
    token_file.write_text(json.dumps(TOKENS))
    os.chmod(token_file, 0o644)
    monkeypatch.setenv("GARMINTOKENS", str(token_file))
    for name in ("GARMIN_FILE_TOOLS", "GARMIN_TOOL_PROFILE", "GARMIN_ENABLED_TOOLS",
                 "GARMIN_DISABLED_TOOLS", "GARMIN_MCP_TRANSPORT"):
        monkeypatch.delenv(name, raising=False)

    _start_server(monkeypatch, Mock)

    assert _mode(token_file) == 0o600


def _failing_chmod(real_chmod, refuse):
    """os.chmod that raises PermissionError for the named paths only."""

    def chmod(path, mode, *args, **kwargs):
        if str(path) in {str(r) for r in refuse}:
            raise PermissionError(1, "Operation not permitted", str(path))
        return real_chmod(path, mode, *args, **kwargs)

    return chmod


def test_tightening_skips_symlinks_and_refuses_a_symlinked_store(
    store, tmp_path, monkeypatch, capsys
):
    outside = tmp_path / "outside.txt"
    outside.write_text("not a token")
    os.chmod(outside, 0o644)
    store.mkdir()
    (store / "garmin_tokens.json").write_text(json.dumps(TOKENS))
    (store / "link-to-outside").symlink_to(outside)

    _start_server(monkeypatch, Mock)

    assert _mode(outside) == 0o644
    assert _mode(store / "garmin_tokens.json") == 0o600

    # second run: the store path itself is a symlink
    real_store = tmp_path / "real-store"
    real_store.mkdir()
    os.chmod(real_store, 0o755)
    linked = tmp_path / "linked-store"
    linked.symlink_to(real_store)
    monkeypatch.setenv("GARMINTOKENS", str(linked))

    with pytest.raises(SystemExit) as exc_info:
        _start_server(monkeypatch, Mock)

    assert exc_info.value.code == 1
    assert "symlink" in capsys.readouterr().err
    assert _mode(real_store) == 0o755


def test_a_token_store_that_stays_open_stops_a_hosted_start(
    store, monkeypatch, capsys
):
    store.mkdir()
    token_file = store / "garmin_tokens.json"
    token_file.write_text(json.dumps(TOKENS))
    os.chmod(token_file, 0o644)
    monkeypatch.setattr(
        garmin_mcp.token_utils.os, "chmod", _failing_chmod(os.chmod, [token_file])
    )
    monkeypatch.setenv("GARMIN_FILE_TOOLS", "off")

    with pytest.raises(SystemExit) as exc_info:
        _start_server(monkeypatch, Mock)

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert str(store) in err and "not private" in err


def test_a_token_store_owned_by_someone_else_stops_a_hosted_start(
    store, monkeypatch, capsys
):
    store.mkdir()
    (store / "garmin_tokens.json").write_text(json.dumps(TOKENS))
    monkeypatch.setattr(garmin_mcp.token_utils.os, "geteuid", lambda: os.getuid() + 1)
    monkeypatch.setenv("GARMIN_FILE_TOOLS", "off")

    with pytest.raises(SystemExit) as exc_info:
        _start_server(monkeypatch, Mock)

    assert exc_info.value.code == 1
    assert "owned by uid" in capsys.readouterr().err


def test_with_file_tools_on_an_open_token_store_only_warns(
    store, monkeypatch, capsys
):
    store.mkdir()
    token_file = store / "garmin_tokens.json"
    token_file.write_text(json.dumps(TOKENS))
    os.chmod(token_file, 0o644)
    monkeypatch.setattr(
        garmin_mcp.token_utils.os, "chmod", _failing_chmod(os.chmod, [token_file])
    )

    _start_server(monkeypatch, Mock)

    err = capsys.readouterr().err
    assert "WARNING" in err and "not private" in err


def test_one_failing_entry_does_not_stop_the_tightening_of_the_others(
    store, monkeypatch
):
    store.mkdir()
    (store / "garmin_tokens.json").write_text(json.dumps(TOKENS))
    stuck = store / "a-stuck-entry"
    stuck.write_text("x")
    other = store / "z-other"
    other.write_text("x")
    sub = store / "sub"
    sub.mkdir()
    nested = sub / "nested.json"
    nested.write_text("x")
    for path in (stuck, other, nested):
        os.chmod(path, 0o644)
    os.chmod(sub, 0o755)
    monkeypatch.setattr(
        garmin_mcp.token_utils.os, "chmod", _failing_chmod(os.chmod, [stuck])
    )

    _start_server(monkeypatch, Mock)

    assert _mode(store / "garmin_tokens.json") == 0o600
    assert _mode(other) == 0o600
    assert _mode(sub) == 0o700
    assert _mode(nested) == 0o600
    assert _mode(stuck) == 0o644


def test_the_token_file_is_tightened_before_the_other_entries(store, monkeypatch):
    store.mkdir()
    (store / "garmin_tokens.json").write_text(json.dumps(TOKENS))
    (store / "a-first-by-name").write_text("x")
    order = []
    real_chmod = os.chmod

    def recording_chmod(path, mode, *args, **kwargs):
        order.append(os.path.basename(str(path)))
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(garmin_mcp.token_utils.os, "chmod", recording_chmod)

    garmin_mcp.token_utils.secure_token_store(str(store))

    assert order.index("garmin_tokens.json") < order.index("a-first-by-name")


def test_the_legacy_base64_file_is_tightened_too(store, tmp_path, monkeypatch):
    legacy = tmp_path / "home" / ".garminconnect_base64"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("abc")
    os.chmod(legacy, 0o644)
    monkeypatch.setenv("HOME", str(legacy.parent))

    _start_server(monkeypatch, Mock)

    assert _mode(legacy) == 0o600


def test_seeding_reports_a_write_error_cleanly(store, monkeypatch, capsys):
    monkeypatch.setenv(seed_tokens.ENV_VAR, json.dumps(TOKENS))

    def fail(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied", str(store))

    monkeypatch.setattr(seed_tokens, "_write_private", fail)

    assert seed_tokens.main() == 1

    err = capsys.readouterr().err
    assert "cannot write the token store" in err and "Traceback" not in err
    assert "access-1" not in err


def test_seeding_into_a_symlinked_store_is_refused(store, tmp_path, monkeypatch, capsys):
    real = tmp_path / "real"
    real.mkdir()
    store.symlink_to(real)
    monkeypatch.setenv(seed_tokens.ENV_VAR, json.dumps(TOKENS))

    assert seed_tokens.main() == 1

    assert "symlink" in capsys.readouterr().err
    assert list(real.iterdir()) == []


def test_the_start_sets_umask_077_before_anything_else(store, monkeypatch):
    os.umask(0o022)

    _start_server(monkeypatch, Mock)

    current = os.umask(0)
    os.umask(current)
    assert current == 0o077


def test_seeded_tokens_are_private(store, monkeypatch, capsys):
    os.umask(0o022)
    monkeypatch.setenv(seed_tokens.ENV_VAR, json.dumps(TOKENS))

    assert seed_tokens.main() == 0

    assert _mode(store / "garmin_tokens.json") == 0o600
    assert _mode(store) == 0o700
    assert _mode(store / seed_tokens.MARKER_NAME) == 0o600


def test_seeding_tightens_an_open_store_it_finds(store, monkeypatch, capsys):
    os.umask(0o022)
    store.mkdir()
    os.chmod(store, 0o755)
    other = store / "other.json"
    other.write_text("{}")
    os.chmod(other, 0o644)
    monkeypatch.setenv(seed_tokens.ENV_VAR, json.dumps(TOKENS))

    assert seed_tokens.main() == 0

    assert _mode(store) == 0o700
    assert _mode(other) == 0o600
