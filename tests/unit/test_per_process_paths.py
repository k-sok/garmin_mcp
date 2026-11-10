"""Every per-user file follows the process's own HOME and variables (#8, L1)."""

from garmin_mcp import activity_analysis, token_utils


def test_two_homes_give_two_token_stores(monkeypatch):
    monkeypatch.delenv("GARMINTOKENS", raising=False)

    monkeypatch.setenv("HOME", "/tmp/a")
    first = token_utils.get_token_path()
    monkeypatch.setenv("HOME", "/tmp/b")
    second = token_utils.get_token_path()

    assert first == "/tmp/a/.garminconnect"
    assert second == "/tmp/b/.garminconnect"


def test_the_token_variable_wins_over_the_home(monkeypatch):
    monkeypatch.setenv("HOME", "/tmp/a")
    monkeypatch.setenv("GARMINTOKENS", "/tmp/t")

    assert token_utils.get_token_path() == "/tmp/t"


def test_the_fit_config_follows_its_variable_at_call_time(monkeypatch):
    monkeypatch.setenv("GARMIN_FIT_CONFIG", "/tmp/one.json")
    assert activity_analysis._get_fit_config_path() == "/tmp/one.json"

    monkeypatch.setenv("GARMIN_FIT_CONFIG", "/tmp/two.json")

    assert activity_analysis._get_fit_config_path() == "/tmp/two.json"


def test_the_fit_config_falls_back_to_the_home_of_the_process(monkeypatch):
    import os

    monkeypatch.delenv("GARMIN_FIT_CONFIG", raising=False)
    monkeypatch.setenv("HOME", "/tmp/a")
    first = os.path.expanduser(activity_analysis._get_fit_config_path())
    monkeypatch.setenv("HOME", "/tmp/b")
    second = os.path.expanduser(activity_analysis._get_fit_config_path())

    assert first == "/tmp/a/.garminconnect_fit_config.json"
    assert second == "/tmp/b/.garminconnect_fit_config.json"
