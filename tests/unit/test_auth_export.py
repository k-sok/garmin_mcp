"""``garmin-mcp-auth --export``: a fresh in-memory login and a token file, nothing else (L64)."""

import json
import os
import stat
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from garmin_mcp import auth_cli

PASSWORD = "hunter2-very-secret"
TOKENS = {
    "di_token": "access.jwt.value",
    "di_refresh_token": "refresh-value-123",
    "di_client_id": "GARMIN_CONNECT_MOBILE_ANDROID_DI_2025Q2",
}
posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX modes and symlinks")


class FakeClient:
    def __init__(self, login_result=(None, None), profile=None, tokens=TOKENS):
        self.login = MagicMock(return_value=login_result)
        self.resume_login = MagicMock(return_value=(None, None))
        self.connectapi = MagicMock(
            return_value={"profileId": 1, "displayName": "anna"} if profile is None else profile
        )
        self.dumps = MagicMock(
            return_value=json.dumps({**tokens, "extra": "dropped"})
        )


@pytest.fixture
def garmin(monkeypatch, tmp_path):
    """A fake Garmin wrapper; saved tokens in GARMINTOKENS must never be used."""
    client = FakeClient()
    wrapper = SimpleNamespace(client=client, login=MagicMock(name="tokenstore_login"))
    factory = MagicMock(return_value=wrapper)
    monkeypatch.setattr(auth_cli, "Garmin", factory)
    monkeypatch.setenv("GARMIN_EMAIL", "anna@example.com")
    monkeypatch.setenv("GARMIN_PASSWORD", PASSWORD)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    saved = tmp_path / "saved"
    saved.mkdir()
    (saved / "garmin_tokens.json").write_text(json.dumps(TOKENS))
    monkeypatch.setenv("GARMINTOKENS", str(saved))
    wrapper.factory = factory
    return wrapper


def _names(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


@posix_only
def test_export_writes_the_token_file_and_keeps_no_store(garmin, tmp_path, capsys):
    target = tmp_path / "out" / "garmin-zugang.json"
    target.parent.mkdir(mode=0o755)
    os.chmod(target.parent, 0o755)

    assert auth_cli.export_token_file(str(target)) is True

    assert json.loads(target.read_text()) == TOKENS
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert _names(tmp_path / "out") == ["garmin-zugang.json"]
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o755
    assert not (tmp_path / "home").exists()
    garmin.client.dumps.assert_called_once()
    garmin.client.connectapi.assert_called_once_with("/userprofile-service/socialProfile")
    garmin.client.login.assert_called_once_with(
        "anna@example.com", PASSWORD, return_on_mfa=True
    )
    out = capsys.readouterr()
    for text in (out.out, out.err):
        assert PASSWORD not in text
        assert all(value not in text for value in TOKENS.values())
    assert str(target) in out.out


@posix_only
def test_export_without_a_path_writes_to_the_downloads_folder(garmin, tmp_path, monkeypatch):
    downloads = tmp_path / "home" / "Downloads"
    downloads.mkdir(parents=True)
    monkeypatch.setattr(sys, "argv", ["garmin-mcp-auth", "--export"])

    with pytest.raises(SystemExit) as stop:
        auth_cli.main()

    assert stop.value.code == 0
    assert json.loads((downloads / "garmin-zugang.json").read_text()) == TOKENS


def test_export_with_a_path_goes_through_the_main_entry_point(garmin, tmp_path, monkeypatch):
    target = tmp_path / "x.json"
    monkeypatch.setattr(sys, "argv", ["garmin-mcp-auth", "--export", str(target), "--force"])

    with pytest.raises(SystemExit) as stop:
        auth_cli.main()

    assert stop.value.code == 0
    assert json.loads(target.read_text()) == TOKENS


def test_export_logs_in_with_the_password_even_when_saved_tokens_exist(garmin, tmp_path):
    target = tmp_path / "t.json"

    assert auth_cli.export_token_file(str(target)) is True

    garmin.client.login.assert_called_once()
    garmin.login.assert_not_called()
    garmin.factory.assert_called_once()


class _Interrupted(BaseException):
    pass


@pytest.mark.parametrize(
    "break_it",
    [
        lambda c: setattr(c.login, "side_effect", GarminConnectAuthenticationError("bad")),
        lambda c: setattr(c.connectapi, "side_effect", GarminConnectConnectionError("401")),
        lambda c: setattr(c.connectapi, "return_value", None),
        lambda c: setattr(c.login, "side_effect", _Interrupted()),
        lambda c: setattr(c.dumps, "return_value", json.dumps({"di_token": None})),
    ],
    ids=["login fails", "verification fails", "empty profile", "interrupted", "no tokens issued"],
)
def test_export_leaves_nothing_when_a_step_fails(garmin, tmp_path, break_it):
    break_it(garmin.client)
    target = tmp_path / "t.json"
    before = _names(tmp_path)

    try:
        result = auth_cli.export_token_file(str(target))
    except _Interrupted:
        result = False

    assert result is False
    assert not target.exists()
    assert _names(tmp_path) == before


def test_export_reports_a_missing_downloads_folder_and_writes_nothing(garmin, tmp_path, capsys):
    (tmp_path / "home").mkdir()

    assert auth_cli.export_token_file(auth_cli.default_export_path()) is False

    assert "Downloads" in capsys.readouterr().err
    assert not (tmp_path / "home" / "Downloads").exists()
    garmin.client.login.assert_not_called()


@posix_only
def test_export_does_not_overwrite_or_follow_a_symlink(garmin, tmp_path, capsys):
    other = tmp_path / "other.json"
    other.write_text("precious")
    existing = tmp_path / "exists.json"
    existing.write_text("old")
    link = tmp_path / "link.json"
    link.symlink_to(other)

    assert auth_cli.export_token_file(str(existing)) is False
    assert auth_cli.export_token_file(str(link)) is False
    assert auth_cli.export_token_file(str(link), force=True) is False

    assert existing.read_text() == "old"
    assert other.read_text() == "precious"
    assert link.is_symlink()
    assert "--force" in capsys.readouterr().err

    assert auth_cli.export_token_file(str(existing), force=True) is True
    assert json.loads(existing.read_text()) == TOKENS
    assert stat.S_IMODE(existing.stat().st_mode) == 0o600
    assert other.read_text() == "precious"


def test_export_refuses_a_directory_as_the_target(garmin, tmp_path):
    (tmp_path / "dir").mkdir()

    assert auth_cli.export_token_file(str(tmp_path / "dir"), force=True) is False


def test_export_treats_a_windows_reparse_point_as_a_link():
    junction = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
    plain = SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x20)

    assert auth_cli._is_link(junction) is True
    assert auth_cli._is_link(plain) is False


def test_export_asks_for_the_mfa_code_with_the_same_client(garmin, tmp_path, monkeypatch):
    garmin.client.login.return_value = ("needs_mfa", {"state": 1})
    monkeypatch.setattr("builtins.input", lambda prompt="": "654321")

    assert auth_cli.export_token_file(str(tmp_path / "t.json")) is True

    garmin.client.resume_login.assert_called_once_with({"state": 1}, "654321")


@pytest.mark.parametrize(
    ("lang", "hint", "prompt", "done"),
    [
        ("en", "Garmin just sent you a code by e-mail", "Code from Garmin: ", "Done. The file is here:"),
        ("de", "Garmin hat dir gerade einen Code per E-Mail geschickt", "Code von Garmin: ", "Fertig. Die Datei liegt hier:"),
    ],
)
def test_export_tells_the_member_where_the_garmin_code_comes_from(
    garmin, tmp_path, monkeypatch, capsys, lang, hint, prompt, done
):
    garmin.client.login.return_value = ("needs_mfa", {})
    seen_before_input = []

    def stub_input(asked=""):
        # what the member could read at the moment the prompt appears
        seen_before_input.append((capsys.readouterr().out, asked))
        return "123456"

    monkeypatch.setattr("builtins.input", stub_input)

    assert auth_cli.export_token_file(str(tmp_path / "t.json"), lang=lang) is True

    printed_before, asked = seen_before_input[0]
    assert hint in printed_before
    assert "spam" in printed_before.lower() and "authenticator" in printed_before.lower()
    assert asked == prompt
    assert done in capsys.readouterr().out


def test_export_asks_and_reports_in_english_by_default_and_german_with_lang_de(
    garmin, tmp_path, monkeypatch, capsys
):
    for name in ("GARMIN_EMAIL", "GARMIN_PASSWORD"):
        monkeypatch.delenv(name)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "anna@example.com")
    monkeypatch.setattr(auth_cli.getpass, "getpass", lambda prompt="": prompts.append(prompt) or PASSWORD)
    target = tmp_path / "t.json"

    assert auth_cli.export_token_file(str(target)) is True
    english = capsys.readouterr().out
    english_prompts, prompts[:] = list(prompts), []
    assert auth_cli.export_token_file(str(target), force=True, lang="de") is True
    german = capsys.readouterr().out

    assert english_prompts == ["Garmin email: ", "Garmin password (not shown): "]
    assert f"Done. The file is here: {target}. Upload it to fatgu.ai and delete it afterwards." in english
    assert prompts == ["Garmin-E-Mail: ", "Garmin-Passwort (wird nicht angezeigt): "]
    assert f"Fertig. Die Datei liegt hier: {target}. Lade sie auf fatgu.ai hoch und lösch sie danach." in german
    prompts.clear()
    monkeypatch.delenv("GARMIN_EMAIL", raising=False)
    auth_cli.get_credentials()
    assert prompts == ["Email: ", "Password: "]


@pytest.mark.parametrize(("args", "banner"), [([], "Garmin MCP Pre-Authentication Tool"), (["--lang", "de"], "Garmin MCP Anmeldung für fatgu.ai")])
def test_the_header_of_export_follows_the_language(garmin, tmp_path, monkeypatch, capsys, args, banner):
    target = tmp_path / "t.json"
    monkeypatch.setattr(sys, "argv", ["garmin-mcp-auth", "--export", str(target), *args])

    with pytest.raises(SystemExit):
        auth_cli.main()

    out = capsys.readouterr().out
    assert banner in out
    assert ("Garmin MCP Anmeldung" in out) == bool(args)


def test_an_unknown_language_is_refused(garmin, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["garmin-mcp-auth", "--export", str(tmp_path / "t.json"), "--lang", "fr"])

    with pytest.raises(SystemExit) as stop:
        auth_cli.main()

    assert stop.value.code != 0
    assert not (tmp_path / "t.json").exists()
    with pytest.raises(ValueError):
        auth_cli.export_texts("fr")
    garmin.client.login.assert_not_called()


def test_the_german_texts_live_in_one_fork_only_module():
    import ast
    from pathlib import Path

    from garmin_mcp import export_texts_de

    src = Path(auth_cli.__file__).parent
    umlauts = [
        p.name
        for p in sorted(src.glob("*.py"))
        if p.name != "export_texts_de.py" and any(c in p.read_text(encoding="utf-8") for c in "äöüß")
    ]
    assert umlauts == []
    assert set(export_texts_de.TEXTS) == set(auth_cli.EXPORT_TEXTS_EN)
    tree = ast.parse(Path(auth_cli.__file__).read_text(encoding="utf-8"))
    imports = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "garmin_mcp.export_texts_de"]
    assert len(imports) == 1  # loaded only for --lang de, inside export_texts
    assert auth_cli.export_texts("de") is export_texts_de.TEXTS
    assert auth_cli.export_texts("en") is auth_cli.EXPORT_TEXTS_EN


def test_library_warnings_stay_off_the_members_terminal(garmin, tmp_path, capfd):
    import logging

    def noisy_login(*args, **kwargs):
        logging.getLogger("garminconnect.client").warning("mobile+cffi returned 429: secret garmin text")
        logging.getLogger("garminconnect").error("another secret garmin text")
        return (None, None)

    garmin.client.login.side_effect = noisy_login
    before = logging.root.manager.disable

    assert auth_cli.export_token_file(str(tmp_path / "t.json")) is True

    fd = capfd.readouterr()
    for text in (fd.out, fd.err):
        assert "secret garmin text" not in text
    assert logging.root.manager.disable == before  # the run leaves the process's logging as it found it


@posix_only
def test_export_removes_a_half_written_file(garmin, tmp_path, monkeypatch):
    target = tmp_path / "t.json"
    real_fdopen = os.fdopen

    def failing_fdopen(fd, *args, **kwargs):
        handle = real_fdopen(fd, *args, **kwargs)
        handle.write = MagicMock(side_effect=OSError("disk full"))
        return handle

    monkeypatch.setattr(auth_cli.os, "fdopen", failing_fdopen)

    assert auth_cli.export_token_file(str(target)) is False
    assert not target.exists()


@pytest.mark.parametrize(
    "error, text",
    [
        (GarminConnectAuthenticationError("secret detail"), "Login failed"),
        (GarminConnectTooManyRequestsError("secret detail"), "Too many requests"),
        (GarminConnectConnectionError("secret detail"), "could not be reached"),
        (RuntimeError("secret detail"), "Unexpected error (RuntimeError)"),
        (ValueError("Email is required"), "Email or password is missing"),
    ],
)
def test_export_prints_a_plain_reason_and_no_library_error_text(garmin, tmp_path, capsys, error, text):
    garmin.client.login.side_effect = error

    assert auth_cli.export_token_file(str(tmp_path / "t.json")) is False

    err = capsys.readouterr().err
    assert text in err
    assert "secret detail" not in err


def test_export_reports_a_write_failure_naming_the_file(garmin, tmp_path, capsys, monkeypatch):
    target = tmp_path / "t.json"
    monkeypatch.setattr(auth_cli.os, "open", MagicMock(side_effect=PermissionError("denied")))

    assert auth_cli.export_token_file(str(target)) is False

    assert str(target) in capsys.readouterr().err


@posix_only
def test_export_refuses_to_replace_a_file_that_became_a_link_before_the_write(garmin, tmp_path):
    other = tmp_path / "other.json"
    other.write_text("precious")
    link = tmp_path / "l.json"
    link.symlink_to(other)

    with pytest.raises(auth_cli.ExportError):
        auth_cli._write_export(str(link), "{}", force=True)

    assert other.read_text() == "precious"
