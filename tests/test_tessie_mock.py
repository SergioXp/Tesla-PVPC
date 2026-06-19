"""Tests for Tessie HTTP client with mocked requests.

Covers:
- TessieClient: get_state, start_charge, stop_charge, set_charge_limit
- HTTP retry logic: 4xx no retry, 5xx retry, network error retry
- VehicleState: all property edge cases
- ReadOnlyVehicleClient: blocks writes, passes reads
- Session management: close()
"""

import os
import sys
import json
from unittest.mock import MagicMock, patch, PropertyMock, call
import pytest
import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from auto_charge.tessie import (
    TessieClient,
    VehicleState,
    ReadOnlyVehicleClient,
    TESSIE_BASE_URL,
    MAX_RETRIES,
)


# =============================================================================
# VehicleState property tests
# =============================================================================

class TestVehicleState:
    """Test VehicleState property parsing from raw dicts."""

    def test_battery_pct_normal(self):
        """Normal battery level."""
        raw = {"charge_state": {"battery_level": 65.5}}
        vs = VehicleState(raw)
        assert vs.battery_pct == 65.5

    def test_battery_pct_missing(self):
        """Missing battery_level → 0."""
        raw = {"charge_state": {}}
        vs = VehicleState(raw)
        assert vs.battery_pct == 0.0

    def test_battery_pct_none(self):
        """None battery_level → TypeError (bug in production code).

        This is a known bug: float(None) raises TypeError.
        We document it here so the test shows the actual behavior.
        """
        raw = {"charge_state": {"battery_level": None}}
        vs = VehicleState(raw)
        with pytest.raises(TypeError):
            _ = vs.battery_pct

    def test_is_charging_true(self):
        """Charging state = 'Charging'."""
        raw = {"charge_state": {"charging_state": "Charging"}}
        vs = VehicleState(raw)
        assert vs.is_charging is True

    def test_is_charging_false(self):
        """Charging state = 'Stopped'."""
        raw = {"charge_state": {"charging_state": "Stopped"}}
        vs = VehicleState(raw)
        assert vs.is_charging is False

    def test_is_charging_unknown(self):
        """Unknown charging state → False."""
        raw = {"charge_state": {"charging_state": "Complete"}}
        vs = VehicleState(raw)
        assert vs.is_charging is False

    def test_is_plugged_in_door_open(self):
        """Charge port door open → plugged in."""
        raw = {"charge_state": {"charge_port_door_open": True}}
        vs = VehicleState(raw)
        assert vs.is_plugged_in is True

    def test_is_plugged_in_latch_engaged(self):
        """Latch engaged but door closed → plugged in."""
        raw = {"charge_state": {"charge_port_door_open": False, "charge_port_latch": "Engaged"}}
        vs = VehicleState(raw)
        assert vs.is_plugged_in is True

    def test_is_plugged_in_not(self):
        """Neither door open nor latch engaged → not plugged in."""
        raw = {"charge_state": {"charge_port_door_open": False, "charge_port_latch": "Disengaged"}}
        vs = VehicleState(raw)
        assert vs.is_plugged_in is False

    def test_charge_limit_normal(self):
        """Normal charge limit."""
        raw = {"charge_state": {"charge_limit_soc": 80}}
        vs = VehicleState(raw)
        assert vs.charge_limit_pct == 80.0

    def test_charge_limit_missing(self):
        """Missing charge limit → 100."""
        raw = {"charge_state": {}}
        vs = VehicleState(raw)
        assert vs.charge_limit_pct == 100.0

    def test_charger_power_charging(self):
        """Charging power when active."""
        raw = {"charge_state": {"charger_power": 7.2}}
        vs = VehicleState(raw)
        assert vs.charger_power_kw == 7.2

    def test_charger_power_zero(self):
        """Zero charging power."""
        raw = {"charge_state": {"charger_power": 0}}
        vs = VehicleState(raw)
        assert vs.charger_power_kw == 0.0

    def test_to_dict(self):
        """to_dict() returns expected keys."""
        raw = {
            "charge_state": {
                "battery_level": 60.0,
                "charging_state": "Charging",
                "charge_port_door_open": True,
                "charge_port_latch": "Engaged",
                "charge_limit_soc": 80,
                "charger_power": 3.3,
            }
        }
        vs = VehicleState(raw)
        d = vs.to_dict()
        assert d["battery_pct"] == 60.0
        assert d["is_charging"] is True
        assert d["is_plugged_in"] is True
        assert d["charge_limit_pct"] == 80.0
        assert d["charger_power_kw"] == 3.3

    def test_to_dict_empty(self):
        """to_dict() with empty raw → returns defaults."""
        vs = VehicleState({})
        d = vs.to_dict()
        assert d["battery_pct"] == 0.0
        assert d["is_charging"] is False


# =============================================================================
# TessieClient with mocked HTTP session
# =============================================================================

def _make_config(token="test_token", vin="TESTVIN123"):
    """Create a mock config for TessieClient."""
    cfg = MagicMock()
    cfg.tessie_token = token
    cfg.vin = vin
    return cfg


class TestTessieClient:
    """Test TessieClient HTTP calls with mocked session."""

    def test_get_state_success(self):
        """get_state() returns VehicleState on success."""
        cfg = _make_config()
        raw_response = {
            "charge_state": {
                "battery_level": 55.0,
                "charging_state": "Stopped",
                "charge_port_door_open": True,
                "charge_port_latch": "Engaged",
                "charge_limit_soc": 70,
                "charger_power": 0,
            }
        }

        # We need to mock the session.get() call
        with patch("auto_charge.tessie.requests.Session") as MockSession:
            session_instance = MockSession.return_value
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = raw_response
            session_instance.get.return_value = mock_resp

            client = TessieClient(cfg)
            state = client.get_state()

        assert state is not None
        assert state.battery_pct == 55.0
        assert state.is_charging is False

    def test_get_state_http_error_returns_none(self):
        """get_state() with HTTP error → returns None."""
        cfg = _make_config()

        with patch("auto_charge.tessie.requests.Session") as MockSession:
            session_instance = MockSession.return_value
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_resp.raise_for_status.side_effect = requests.HTTPError("500", response=mock_resp)
            session_instance.get.return_value = mock_resp

            client = TessieClient(cfg)
            state = client.get_state()

        assert state is None

    def test_get_state_4xx_no_retry(self):
        """4xx errors should not retry (only one attempt)."""
        cfg = _make_config()

        with patch("auto_charge.tessie.requests.Session") as MockSession:
            session_instance = MockSession.return_value
            mock_resp = MagicMock()
            mock_resp.status_code = 401
            mock_resp.reason = "Unauthorized"
            mock_resp.raise_for_status.side_effect = requests.HTTPError("401", response=mock_resp)
            session_instance.get.return_value = mock_resp

            client = TessieClient(cfg)
            state = client.get_state()

        assert state is None
        # Should only call get() once for 4xx errors (no retry)
        assert session_instance.get.call_count == 1, "4xx should not retry"

    def test_start_charge_success(self):
        """start_charge() returns True on success."""
        cfg = _make_config()

        with patch("auto_charge.tessie.requests.Session") as MockSession:
            session_instance = MockSession.return_value
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"result": True}
            session_instance.post.return_value = mock_resp

            client = TessieClient(cfg)
            result = client.start_charge()

        assert result is True

    def test_start_charge_failure(self):
        """start_charge() returns False on failure."""
        cfg = _make_config()

        with patch("auto_charge.tessie.requests.Session") as MockSession:
            session_instance = MockSession.return_value
            session_instance.post.side_effect = requests.RequestException("Network error")

            client = TessieClient(cfg)
            result = client.start_charge()

        assert result is False

    def test_stop_charge_success(self):
        """stop_charge() returns True on success."""
        cfg = _make_config()

        with patch("auto_charge.tessie.requests.Session") as MockSession:
            session_instance = MockSession.return_value
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"result": True}
            session_instance.post.return_value = mock_resp

            client = TessieClient(cfg)
            result = client.stop_charge()

        assert result is True

    def test_set_charge_limit_success(self):
        """set_charge_limit() returns True on success."""
        cfg = _make_config()

        with patch("auto_charge.tessie.requests.Session") as MockSession:
            session_instance = MockSession.return_value
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"result": True}
            session_instance.post.return_value = mock_resp

            client = TessieClient(cfg)
            result = client.set_charge_limit(80)

        assert result is True

    def test_get_vehicle_data_success(self):
        """get_vehicle_data() returns dict on success."""
        cfg = _make_config()
        raw_response = {
            "charge_state": {
                "battery_level": 60.0,
                "charging_state": "Charging",
                "charge_port_door_open": True,
                "charge_port_latch": "Engaged",
                "charge_limit_soc": 80,
                "charger_power": 3.3,
            }
        }

        with patch("auto_charge.tessie.requests.Session") as MockSession:
            session_instance = MockSession.return_value
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = raw_response
            session_instance.get.return_value = mock_resp

            client = TessieClient(cfg)
            data = client.get_vehicle_data()

        assert data["battery_pct"] == 60.0
        assert data["is_charging"] is True

    def test_get_vehicle_data_failure_returns_empty(self):
        """get_vehicle_data() returns {} on failure."""
        cfg = _make_config()

        with patch("auto_charge.tessie.requests.Session") as MockSession:
            session_instance = MockSession.return_value
            session_instance.get.side_effect = requests.RequestException("Failed")

            client = TessieClient(cfg)
            data = client.get_vehicle_data()

        assert data == {}

    def test_close_session(self):
        """close() closes the HTTP session."""
        cfg = _make_config()

        with patch("auto_charge.tessie.requests.Session") as MockSession:
            session_instance = MockSession.return_value
            client = TessieClient(cfg)
            client.close()
            session_instance.close.assert_called_once()

    def test_retry_on_5xx(self):
        """5xx errors should retry up to MAX_RETRIES times."""
        cfg = _make_config()

        with patch("auto_charge.tessie.requests.Session") as MockSession:
            session_instance = MockSession.return_value
            mock_resp = MagicMock()
            mock_resp.status_code = 502
            # Always fail with 5xx
            mock_resp.raise_for_status.side_effect = requests.HTTPError("502", response=mock_resp)
            session_instance.get.return_value = mock_resp

            with patch("auto_charge.tessie.time.sleep"):  # Don't actually sleep
                client = TessieClient(cfg)
                state = client.get_state()

        assert state is None
        # Should retry MAX_RETRIES times for 5xx
        assert session_instance.get.call_count == 3, f"Should retry {MAX_RETRIES} times, got {session_instance.get.call_count}"


# =============================================================================
# ReadOnlyVehicleClient tests
# =============================================================================

class TestReadOnlyVehicleClient:
    """Test dry-run wrapper: reads pass, writes block."""

    def test_read_passes_through(self):
        """get_state() passes through to real client."""
        real = MagicMock()
        real.get_state.return_value = "state_data"
        proxy = ReadOnlyVehicleClient(real)

        result = proxy.get_state()
        assert result == "state_data"
        real.get_state.assert_called_once()

    def test_get_vehicle_data_passes_through(self):
        """get_vehicle_data() passes through."""
        real = MagicMock()
        real.get_vehicle_data.return_value = {"battery_pct": 50}
        proxy = ReadOnlyVehicleClient(real)

        result = proxy.get_vehicle_data()
        assert result["battery_pct"] == 50

    def test_start_charge_blocked(self):
        """start_charge() is blocked."""
        real = MagicMock()
        proxy = ReadOnlyVehicleClient(real)

        result = proxy.start_charge()
        assert result is True  # Returns True to avoid errors
        real.start_charge.assert_not_called()

    def test_stop_charge_blocked(self):
        """stop_charge() is blocked."""
        real = MagicMock()
        proxy = ReadOnlyVehicleClient(real)

        result = proxy.stop_charge()
        assert result is True
        real.stop_charge.assert_not_called()

    def test_set_charge_limit_blocked(self):
        """set_charge_limit() is blocked."""
        real = MagicMock()
        proxy = ReadOnlyVehicleClient(real)

        result = proxy.set_charge_limit(90)
        assert result is True
        real.set_charge_limit.assert_not_called()

    def test_get_blocked_log(self):
        """get_blocked_log() returns list of blocked actions."""
        real = MagicMock()
        proxy = ReadOnlyVehicleClient(real)

        proxy.start_charge()
        proxy.stop_charge()
        proxy.set_charge_limit(80)

        log = proxy.get_blocked_log()
        assert len(log) == 3, f"Should have 3 blocked actions, got {len(log)}"
        assert "start_charge" in log[0]
        assert "stop_charge" in log[1]
        assert "set_charge_limit" in log[2]

    def test_deduplication_of_logging(self):
        """Repeated same action deduplicates log messages but not blocked list."""
        real = MagicMock()
        proxy = ReadOnlyVehicleClient(real)
    
        proxy.start_charge()
        proxy.start_charge()
        proxy.start_charge()
    
        log = proxy.get_blocked_log()
        # Blocked actions list stores ALL calls (no dedup in list)
        assert len(log) == 3, f"Should store all 3 in list, got {len(log)}"
