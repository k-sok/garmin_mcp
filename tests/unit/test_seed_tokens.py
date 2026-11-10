"""GARMIN_TOKENS_JSON seeds the token store for container deployments."""

import base64
import json
import os
import stat

import pytest

from garmin_mcp import seed_tokens

TOKENS = {"di_token": "access-1", "di_refresh_token": "refresh-1", "di_client_id": "client"}
TOKENS_JSON = json.dumps(TOKENS)


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "tokens"
    monkeypatch.setenv("GARMINTOKENS", str(path))
    monkeypatch.delenv(seed_tokens.ENV_VAR, raising=False)
    return path


def _env(value):
    return {seed_tokens.ENV_VAR: value}


def test_unset_or_blank_variable_does_nothing(store):
    assert seed_tokens.seed({}) == "unset"
    assert seed_tokens.seed(_env("   ")) == "unset"
    assert not store.exists()


def test_raw_json_is_written_to_the_token_file(store):
    assert seed_tokens.seed(_env(TOKENS_JSON)) == "seeded"
    assert json.loads((store / "garmin_tokens.json").read_text()) == TOKENS


def test_base64_json_is_accepted(store):
    encoded = base64.b64encode(TOKENS_JSON.encode()).decode()
    assert seed_tokens.seed(_env(encoded)) == "seeded"
    assert json.loads((store / "garmin_tokens.json").read_text()) == TOKENS


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_token_file_is_owner_only(store):
    seed_tokens.seed(_env(TOKENS_JSON))
    assert stat.S_IMODE((store / "garmin_tokens.json").stat().st_mode) == 0o600
    assert stat.S_IMODE(store.stat().st_mode) == 0o700


def test_unchanged_value_keeps_tokens_refreshed_by_the_client(store):
    seed_tokens.seed(_env(TOKENS_JSON))
    token_file = store / "garmin_tokens.json"
    refreshed = dict(TOKENS, di_token="access-2", di_refresh_token="refresh-2")
    token_file.write_text(json.dumps(refreshed))

    assert seed_tokens.seed(_env(TOKENS_JSON)) == "unchanged"
    assert json.loads(token_file.read_text()) == refreshed


def test_changed_value_replaces_the_store(store):
    seed_tokens.seed(_env(TOKENS_JSON))
    token_file = store / "garmin_tokens.json"
    token_file.write_text(json.dumps(dict(TOKENS, di_refresh_token="refresh-2")))

    new_tokens = dict(TOKENS, di_token="access-3", di_refresh_token="refresh-3")
    assert seed_tokens.seed(_env(json.dumps(new_tokens))) == "seeded"
    assert json.loads(token_file.read_text()) == new_tokens


def test_value_equal_to_seed_but_missing_file_is_reseeded(store):
    seed_tokens.seed(_env(TOKENS_JSON))
    (store / "garmin_tokens.json").unlink()
    assert seed_tokens.seed(_env(TOKENS_JSON)) == "seeded"


def test_json_file_path_is_used_as_the_token_file(tmp_path, monkeypatch):
    target = tmp_path / "custom" / "my_tokens.json"
    monkeypatch.setenv("GARMINTOKENS", str(target))
    assert seed_tokens.seed(_env(TOKENS_JSON)) == "seeded"
    assert json.loads(target.read_text()) == TOKENS


@pytest.mark.parametrize(
    "value",
    [
        "not json and not base64 !!!",
        "{broken json",
        json.dumps({"di_token": "only-access"}),
        json.dumps(["list"]),
        base64.b64encode(b"plain text").decode(),
    ],
)
def test_unusable_value_raises_and_writes_nothing(store, value):
    with pytest.raises(ValueError):
        seed_tokens.seed(_env(value))
    assert not store.exists()


def test_error_messages_never_contain_the_value(store):
    secret = "super-secret-refresh-token"
    value = json.dumps({"di_token": secret})
    with pytest.raises(ValueError) as excinfo:
        seed_tokens.seed(_env(value))
    assert secret not in str(excinfo.value)


def test_main_reports_failure_without_leaking_the_value(store, monkeypatch, capsys):
    monkeypatch.setenv(seed_tokens.ENV_VAR, "{broken super-secret")
    assert seed_tokens.main() == 1
    err = capsys.readouterr().err
    assert seed_tokens.ENV_VAR in err
    assert "super-secret" not in err


def test_main_is_silent_when_variable_is_unset(store, capsys):
    assert seed_tokens.main() == 0
    assert capsys.readouterr().err == ""
