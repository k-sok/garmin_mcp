"""The container HEALTHCHECK probes /healthz on HTTP transports only."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "docker-healthcheck.py"
spec = importlib.util.spec_from_file_location("docker_healthcheck", SCRIPT)
healthcheck = importlib.util.module_from_spec(spec)
spec.loader.exec_module(healthcheck)


class _Response:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(status=200, calls=None):
    def opener(url, timeout):
        if calls is not None:
            calls.append((url, timeout))
        return _Response(status)

    return opener


@pytest.mark.parametrize("env", [{}, {"GARMIN_MCP_TRANSPORT": "stdio"}, {"GARMIN_MCP_TRANSPORT": " STDIO "}])
def test_stdio_passes_without_probing(env):
    calls = []
    assert healthcheck.main(env, opener=_opener(calls=calls)) == 0
    assert calls == []


def test_http_transport_probes_healthz_on_the_default_port():
    calls = []
    env = {"GARMIN_MCP_TRANSPORT": "streamable-http"}
    assert healthcheck.main(env, opener=_opener(calls=calls)) == 0
    assert calls == [("http://127.0.0.1:8000/healthz", 3)]


def test_wildcard_bind_host_is_probed_via_loopback_on_the_configured_port():
    env = {
        "GARMIN_MCP_TRANSPORT": "sse",
        "GARMIN_MCP_HOST": "0.0.0.0",
        "GARMIN_MCP_PORT": "9000",
    }
    assert healthcheck.health_url(env) == "http://127.0.0.1:9000/healthz"


def test_ipv6_host_is_bracketed():
    env = {"GARMIN_MCP_TRANSPORT": "streamable-http", "GARMIN_MCP_HOST": "::1"}
    assert healthcheck.health_url(env) == "http://[::1]:8000/healthz"


def test_non_200_fails():
    env = {"GARMIN_MCP_TRANSPORT": "streamable-http"}
    assert healthcheck.main(env, opener=_opener(status=503)) == 1


def test_unreachable_server_fails():
    def opener(url, timeout):
        raise OSError("connection refused")

    env = {"GARMIN_MCP_TRANSPORT": "streamable-http"}
    assert healthcheck.main(env, opener=opener) == 1
