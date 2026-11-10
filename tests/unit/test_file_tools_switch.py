"""GARMIN_FILE_TOOLS=off removes every tool that touches local files (#8, K2)."""

import asyncio
import sys
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

import garmin_mcp
from garmin_mcp.tool_profiles import FILE_TOOLS, resolve_file_tools
from scripts.generate_dxt_tools import MODULES

FOUR_FILE_TOOLS = {
    "set_fit_download_dir",
    "download_activity_file",
    "download_course_gpx",
    "upload_course",
}

ENV_NAMES = (
    "GARMIN_FILE_TOOLS",
    "GARMIN_TOOL_PROFILE",
    "GARMIN_ENABLED_TOOLS",
    "GARMIN_DISABLED_TOOLS",
    "GARMIN_MCP_TRANSPORT",
)


@pytest.fixture(autouse=True)
def _private_home(tmp_path, monkeypatch):
    """main() must never see the real token store or FIT config."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GARMINTOKENS", str(tmp_path / "home" / "tokens"))
    monkeypatch.setenv("GARMIN_FIT_CONFIG", str(tmp_path / "home" / "fit.json"))


class _Server:
    """The FastMCP app main() built, kept alive because run() is replaced."""

    def __init__(self):
        self.app = None

    def tool_names(self):
        return {tool.name for tool in asyncio.run(self.app.list_tools())}


def _build_server(monkeypatch, **env):
    """Run main() up to app.run(), the way a process start would."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(garmin_mcp, "init_api", lambda _email, _password: Mock())
    monkeypatch.setattr(sys, "stdout", sys.stdout)  # main() swaps it; restore after
    server = _Server()

    def capture_run(self, **_kwargs):
        server.app = self

    monkeypatch.setattr(FastMCP, "run", capture_run)
    garmin_mcp.main()
    return server


def test_the_file_tools_are_not_listed_when_file_tools_are_off(monkeypatch):
    names = _build_server(monkeypatch, GARMIN_FILE_TOOLS="off").tool_names()

    assert not FOUR_FILE_TOOLS & names
    assert {"add_weigh_in", "delete_activity", "get_activity_fit_data"} <= names


@pytest.mark.parametrize(
    "env",
    [
        {
            "GARMIN_TOOL_PROFILE": "activities",
            "GARMIN_ENABLED_TOOLS": "download_activity_file,upload_course",
        },
        {"GARMIN_ENABLED_TOOLS": "UPLOAD_COURSE"},
        {"GARMIN_TOOL_PROFILE": "all"},
    ],
    ids=["profile_and_allowlist", "allowlist_alone", "profile_all"],
)
def test_no_profile_or_allowlist_brings_a_file_tool_back(monkeypatch, env):
    names = _build_server(monkeypatch, GARMIN_FILE_TOOLS="off", **env).tool_names()

    assert not FOUR_FILE_TOOLS & names


def test_the_switch_is_read_trimmed_and_in_any_case(monkeypatch):
    names = _build_server(monkeypatch, GARMIN_FILE_TOOLS=" OFF ").tool_names()

    assert not FOUR_FILE_TOOLS & names


def test_calling_a_removed_file_tool_fails_without_touching_a_file(monkeypatch, tmp_path):
    server = _build_server(monkeypatch, GARMIN_FILE_TOOLS="off")
    target = tmp_path / "other-member"

    with pytest.raises(ToolError, match="Unknown tool"):
        asyncio.run(server.app.call_tool("set_fit_download_dir", {"path": str(target)}))

    assert not target.exists()


def test_file_tools_stay_for_the_fork_s_other_users_by_default(monkeypatch):
    names = _build_server(monkeypatch).tool_names()

    assert FOUR_FILE_TOOLS <= names


@pytest.mark.parametrize("value", ["", "maybe"])
def test_an_empty_or_unknown_value_for_the_switch_stops_the_start(
    monkeypatch, capsys, value
):
    with pytest.raises(SystemExit) as exc_info:
        _build_server(monkeypatch, GARMIN_FILE_TOOLS=value)

    assert exc_info.value.code == 1
    assert "GARMIN_FILE_TOOLS" in capsys.readouterr().err


def test_every_name_in_the_file_tool_set_is_a_registered_tool():
    app = FastMCP("file tool names")
    proxy = garmin_mcp._ToolFilter(app, set(), set())
    for module in MODULES:
        module.configure(object())
        module.register_tools(proxy)
    registered = {tool.name for tool in asyncio.run(app.list_tools())}

    assert FILE_TOOLS == FOUR_FILE_TOOLS
    assert FILE_TOOLS <= registered


def _filter_with(**kwargs):
    app = FastMCP("add tool")
    return app, garmin_mcp._ToolFilter(app, set(), set(), **kwargs)


def _plain_tool():
    def upload_course() -> str:
        return "uploaded"

    return upload_course


def test_a_file_tool_added_through_add_tool_is_refused_when_file_tools_are_off():
    app, proxy = _filter_with(file_tools=False)

    with pytest.raises(ValueError, match="upload_course"):
        proxy.add_tool(_plain_tool())
    with pytest.raises(ValueError, match="(?i)download_activity_file"):
        proxy.add_tool(_plain_tool(), name="Download_Activity_File")

    assert asyncio.run(app.list_tools()) == []


def test_add_tool_follows_the_filter_like_the_decorator():
    app = FastMCP("add tool")
    proxy = garmin_mcp._ToolFilter(app, set(), {"get_b"})

    def get_a() -> str:
        return "a"

    def get_b() -> str:
        return "b"

    proxy.add_tool(get_a)
    proxy.add_tool(get_b)
    proxy.add_tool(_plain_tool())

    assert {tool.name for tool in asyncio.run(app.list_tools())} == {
        "get_a",
        "upload_course",
    }


def test_a_file_tool_registered_past_the_filter_stops_the_start(monkeypatch, capsys):
    original = garmin_mcp.courses.register_tools

    def register_past_the_filter(app):
        app = original(app)
        app._app.add_tool(_plain_tool())  # the raw FastMCP, bypassing the filter
        return app

    monkeypatch.setattr(garmin_mcp.courses, "register_tools", register_past_the_filter)

    with pytest.raises(SystemExit) as exc_info:
        _build_server(monkeypatch, GARMIN_FILE_TOOLS="off")

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "GARMIN_FILE_TOOLS is off" in err and "upload_course" in err


def test_unset_means_on_and_values_are_normalised():
    assert resolve_file_tools(None) is True
    assert resolve_file_tools("on") is True
    assert resolve_file_tools(" On ") is True
    assert resolve_file_tools("OFF") is False


def test_the_start_up_log_counts_the_removed_file_tools(monkeypatch, capsys):
    _build_server(monkeypatch, GARMIN_FILE_TOOLS="off")

    assert "File tools off: 4 tool(s)" in capsys.readouterr().err
