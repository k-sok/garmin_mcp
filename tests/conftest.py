"""
Shared pytest fixtures for Garmin MCP testing
"""
import os

import pytest
from unittest.mock import Mock
from datetime import datetime, timedelta
from mcp.server.fastmcp import FastMCP


@pytest.fixture(autouse=True)
def _restore_umask():
    """main() and seed_tokens.main() set umask 077 for the whole process.

    Put the caller's umask back so one test cannot change the file modes
    another test creates.
    """
    previous = os.umask(0)
    os.umask(previous)
    yield
    os.umask(previous)


@pytest.fixture(autouse=True)
def _isolated_home(request, tmp_path_factory, monkeypatch):
    """Keep tests off the real token store and FIT config.

    main() tightens whatever token store the environment names, so a test that
    runs it must never resolve to the developer's own ~/.garminconnect. e2e
    tests need the real one and are left alone.
    """
    if request.node.get_closest_marker("e2e"):
        return
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))
    for name in ("GARMINTOKENS", "GARMINTOKENS_BASE64", "GARMIN_FIT_CONFIG"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def mock_garmin_client():
    """Create a mock Garmin client with common methods stubbed"""
    client = Mock()

    # Configure mock to have all the methods we need
    # By default, methods return None (can be overridden in tests)
    client.get_activities = Mock(return_value=[])
    client.get_stats = Mock(return_value={})
    client.get_user_summary = Mock(return_value={})
    client.get_body_composition = Mock(return_value={})
    client.get_stats_and_body = Mock(return_value={})
    client.get_steps_data = Mock(return_value={})
    client.get_daily_steps = Mock(return_value={})
    client.get_training_readiness = Mock(return_value={})
    client.get_body_battery = Mock(return_value={})
    client.get_body_battery_events = Mock(return_value={})
    client.get_blood_pressure = Mock(return_value={})
    client.get_floors = Mock(return_value={})
    client.get_training_status = Mock(return_value={})
    client.get_rhr_day = Mock(return_value={})
    client.get_heart_rates = Mock(return_value={})
    client.get_hydration_data = Mock(return_value={})
    client.get_sleep_data = Mock(return_value={})
    client.get_stress_data = Mock(return_value={})
    client.get_respiration_data = Mock(return_value={})
    client.get_spo2_data = Mock(return_value={})
    client.get_all_day_stress = Mock(return_value={})
    client.get_all_day_events = Mock(return_value={})

    return client


@pytest.fixture
def today_str():
    """Return today's date as YYYY-MM-DD string"""
    return datetime.now().strftime("%Y-%m-%d")


@pytest.fixture
def yesterday_str():
    """Return yesterday's date as YYYY-MM-DD string"""
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


@pytest.fixture
def date_range():
    """Return a tuple of (start_date, end_date) as strings"""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=7)
    return (start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))


@pytest.fixture
def sample_activity():
    """Sample activity data matching Garmin API response format"""
    return {
        "activityId": 12345678901,
        "activityName": "Morning Run",
        "activityType": {
            "typeKey": "running",
            "typeId": 1
        },
        "startTimeLocal": "2024-01-15 07:00:00",
        "distance": 5000.0,
        "duration": 1800.0,
        "averageHR": 145,
        "maxHR": 165,
        "calories": 350
    }


@pytest.fixture
def sample_steps_data():
    """Sample steps data matching Garmin API response format"""
    return {
        "steps": 10000,
        "dailyStepGoal": 8000,
        "stepGoalDistance": 10000,
        "totalDistance": 7500,
        "wellnessDistanceUnit": "meter"
    }


@pytest.fixture
def sample_sleep_data():
    """Sample sleep data matching Garmin API response format"""
    return {
        "dailySleepDTO": {
            "sleepTimeSeconds": 28800,  # 8 hours
            "napTimeSeconds": 0,
            "sleepStartTimestampGMT": 1705276800000,
            "sleepEndTimestampGMT": 1705305600000,
            "deepSleepSeconds": 7200,
            "lightSleepSeconds": 14400,
            "remSleepSeconds": 7200,
            "awakeSleepSeconds": 0,
            "awakeCount": 2,
            "restlessMomentsCount": 15,
            "avgSleepStress": 15,
            "restingHeartRate": 55,
            "sleepScores": {
                "overall": {
                    "value": 85,
                    "qualifierKey": "GOOD",
                    "optimalStart": 75,
                    "optimalEnd": 100
                }
            }
        },
        "wellnessSpO2SleepSummaryDTO": {
            "averageSpo2": 96,
            "lowestSpo2": 93
        },
        "avgOvernightHrv": 45
    }


@pytest.fixture
def sample_heart_rate_data():
    """Sample heart rate data matching Garmin API response format"""
    return {
        "restingHeartRate": 55,
        "maxHeartRate": 180,
        "minHeartRate": 45,
        "lastSevenDaysAvgRestingHeartRate": 57
    }


@pytest.fixture
def sample_body_battery_data():
    """Sample body battery data matching Garmin API response format"""
    return [{
        "startTimestampGMT": 1705276800000,
        "endTimestampGMT": 1705363200000,
        "chargedValue": 100,
        "drainedValue": 25,
        "bodyBatteryMostRecentValue": 75
    }]


@pytest.fixture
def sample_training_status():
    """Sample training status data matching Garmin API response format"""
    return {
        "trainingStatusKey": "PRODUCTIVE",
        "load7Day": 250,
        "load4Week": 1000,
        "vo2MaxValue": 52.5,
        "fitnessAge": 25
    }


def create_test_app(module, mock_client):
    """
    Helper function to create a FastMCP app with a specific module registered

    Args:
        module: The module to register (e.g., health_wellness)
        mock_client: Mock Garmin client to configure the module with

    Returns:
        FastMCP app instance with tools registered
    """
    # Configure the module with mock client
    module.configure(mock_client)

    # Create app and register tools
    app = FastMCP("Test Garmin MCP")
    app = module.register_tools(app)

    return app


@pytest.fixture
def app_factory(mock_garmin_client):
    """
    Factory fixture to create FastMCP apps with different modules

    Usage:
        app = app_factory(health_wellness)
    """
    def _create_app(module):
        return create_test_app(module, mock_garmin_client)

    return _create_app
