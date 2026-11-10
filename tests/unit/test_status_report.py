"""GARMIN_STATUS_FILE: Garmin's status for a failed request comes from the response object."""

import json
import os
import stat

import pytest
import requests
from garminconnect import GarminConnectConnectionError
from garminconnect.client import Client
from mcp.server.fastmcp import FastMCP

from garmin_mcp import _ToolFilter
from garmin_mcp import status_report
from garmin_mcp.write_errors import WriteTracker


class _Canned(requests.adapters.HTTPAdapter):
    """Answers each request with the next canned (status, content type, body)."""

    def __init__(self, answers):
        super().__init__()
        self.answers = list(answers)
        self.sent = 0

    def send(self, request, **kwargs):
        status, content_type, body = self.answers[min(self.sent, len(self.answers) - 1)]
        self.sent += 1
        response = requests.Response()
        response.status_code = status
        response.url = request.url
        response.request = request
        if content_type:
            response.headers["Content-Type"] = content_type
        response._content = body
        return response


def _client(answers):
    client = Client()
    client.di_token = "not-a-jwt"  # no expiry to read, so no refresh before the request
    client.di_refresh_token = "refresh"
    client.di_client_id = "id"
    client._refresh_di_token = lambda: None  # the refresh itself is not under test
    client._api_session.mount("https://", _Canned(answers))
    return client


@pytest.fixture
def status_path(tmp_path, monkeypatch):
    path = tmp_path / "status.json"
    monkeypatch.setenv("GARMIN_STATUS_FILE", str(path))
    return path


def _fail(client):
    with pytest.raises(GarminConnectConnectionError):
        client.connectapi("/some/path")


def _read(path):
    return json.loads(path.read_text())


@pytest.mark.parametrize(
    "status,content_type,body,blocked",
    [
        (401, "application/json", b"{}", False),
        (429, "application/json", b"{}", False),
        (403, "application/json", b'{"message": "no"}', False),
        (403, "text/html", b"<html>blocked</html>", True),
        (500, "application/json", b"{}", False),
    ],
)
def test_a_failed_garmin_call_writes_its_status(
    status_path, status, content_type, body, blocked
):
    _fail(_client([(status, content_type, body)]))
    assert _read(status_path) == {"status": status, "blocked": blocked}
    assert stat.S_IMODE(status_path.stat().st_mode) == 0o600


def test_exception_text_never_sets_a_status(status_path):
    client = _client([(200, "application/json", b"{}")])

    def raising(*args, **kwargs):
        raise GarminConnectConnectionError("API Error 429 - Too many requests")

    client._api_session.request = raising
    _fail(client)
    assert not status_path.exists()


def test_a_plain_text_403_is_a_block(status_path):
    _fail(_client([(403, "text/plain", b"error code: 1020")]))
    assert _read(status_path) == {"status": 403, "blocked": True}


def test_the_most_severe_status_is_kept(status_path):
    _fail(_client([(429, "application/json", b"{}")]))
    _fail(_client([(404, "application/json", b"{}")]))
    assert _read(status_path) == {"status": 429, "blocked": False}


def test_a_401_does_not_replace_a_blocked_403_and_replaces_a_500(status_path):
    _fail(_client([(500, "application/json", b"{}")]))
    _fail(_client([(401, "application/json", b"{}")]))
    assert _read(status_path)["status"] == 401
    _fail(_client([(403, "text/plain", b"error code: 1020")]))
    _fail(_client([(401, "application/json", b"{}")]))
    assert _read(status_path) == {"status": 403, "blocked": True}


def test_a_recovered_401_writes_nothing(status_path):
    client = _client([(401, "application/json", b"{}"), (200, "application/json", b"{}")])
    assert client.connectapi("/some/path") == {}
    assert not status_path.exists()


def test_a_401_that_stays_after_the_refresh_is_reported_once(status_path):
    _fail(_client([(401, "application/json", b"{}"), (401, "application/json", b"{}")]))
    assert _read(status_path) == {"status": 401, "blocked": False}


def test_without_the_switch_nothing_is_written(tmp_path, monkeypatch):
    monkeypatch.delenv("GARMIN_STATUS_FILE", raising=False)
    with pytest.raises(GarminConnectConnectionError, match="API Error 429"):
        _client([(429, "application/json", b"{}")]).connectapi("/some/path")
    assert list(tmp_path.iterdir()) == []


def test_a_relative_path_is_ignored(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GARMIN_STATUS_FILE", "status.json")
    _fail(_client([(429, "application/json", b"{}")]))
    assert list(tmp_path.iterdir()) == []


def test_a_link_at_the_status_path_is_not_followed(tmp_path, monkeypatch):
    other = tmp_path / "other.txt"
    other.write_text("untouched")
    link = tmp_path / "status.json"
    link.symlink_to(other)
    monkeypatch.setenv("GARMIN_STATUS_FILE", str(link))
    _fail(_client([(429, "application/json", b"{}")]))
    assert other.read_text() == "untouched"
    assert not link.is_symlink()
    assert _read(link) == {"status": 429, "blocked": False}


def test_a_failure_to_write_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("GARMIN_STATUS_FILE", str(tmp_path / "missing" / "status.json"))
    _fail(_client([(429, "application/json", b"{}")]))


def test_an_unreadable_old_status_file_is_replaced(status_path):
    status_path.write_text("not json")
    _fail(_client([(500, "application/json", b"{}")]))
    assert _read(status_path) == {"status": 500, "blocked": False}


def test_an_oversized_old_status_file_is_replaced(status_path):
    status_path.write_text(" " * 300)
    _fail(_client([(500, "application/json", b"{}")]))
    assert _read(status_path)["status"] == 500


def test_install_is_idempotent():
    before = Client._run_request
    status_report.install()
    assert Client._run_request is before
    assert getattr(before, "__wrapped__") is not before


def test_a_json_looking_body_without_a_content_type_is_not_a_block(status_path):
    _fail(_client([(403, None, b'{"message": "forbidden"}')]))
    assert _read(status_path) == {"status": 403, "blocked": False}


def test_a_failed_write_is_marked_as_an_error():
    """A writing tool's failure reaches the client as an isError result with the structured text."""
    import anyio

    app = _ToolFilter(FastMCP("test"), set(), set())

    @app.tool()
    async def log_something() -> str:
        tracker = WriteTracker("log_something", "Error logging")
        try:
            tracker.sending()
            raise GarminConnectConnectionError("API Error 400 - nope")
        except Exception as exc:
            return tracker.error(exc)

    @app.tool()
    async def fine() -> str:
        return "fine"

    async def run():
        failed = await app.call_tool("log_something", {})
        ok = await app.call_tool("fine", {})
        return failed, ok

    failed, ok = anyio.run(run)
    assert failed.isError is True
    payload = json.loads(failed.content[0].text)
    assert payload["status"] == "error" and payload["upstream_status"] == 400
    assert not isinstance(ok, type(failed))
