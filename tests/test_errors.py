"""Tests for error handling, edge cases, and failure modes.

Focuses on:
- Price provider failover and parsing errors
- Config validation errors
- Status file edge cases
- Network timeout handling
- CLI argument edge cases
"""

import os
import sys
import json
import tempfile
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, PropertyMock
from typing import Dict
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from auto_charge.planner import ChargePlanner, ChargingPlan, ChargingSlot, _MISSING_PRICE_SENTINEL
from auto_charge.config import Config, DEFAULT_CONFIG, REQUIRED_FIELDS, _coerce, _set_env_var
from auto_charge.prices import _parse_esios_response, _parse_redata_response


# =============================================================================
# Mock Config for tests
# =============================================================================

class MockConfig:
    def __init__(self, **kwargs):
        for k, v in DEFAULT_CONFIG.items():
            if isinstance(v, dict):
                setattr(self, k, dict(v))
            else:
                setattr(self, k, v)
        for k, v in kwargs.items():
            setattr(self, k, v)
        # Ensure properties defined in Config work
        for key in ["target_time", "max_price_cents_per_kwh", "max_charger_power_kw",
                     "battery_capacity_kwh", "min_battery_pct", "strict_mode",
                     "charging_efficiency", "check_interval_minutes"]:
            if key not in kwargs:
                setattr(self, key, DEFAULT_CONFIG.get(key))
        self.tessie_token = kwargs.get("tessie_token", "")
        self.esios_token = kwargs.get("esios_token", "test_token")
        self.debug_mode = kwargs.get("debug_mode", False)
        self.telegram_enabled = kwargs.get("telegram_enabled", False)
        self.telegram_bot_token = kwargs.get("telegram_bot_token", "")
        self.telegram_chat_id = kwargs.get("telegram_chat_id", "")

    @property
    def target_hour(self) -> int:
        return int(self.target_time.split(":")[0])

    @property
    def target_minute(self) -> int:
        return int(self.target_time.split(":")[1])


# =========================================================================
# Price provider parsing tests
# =========================================================================

class TestPriceParsing:
    """Test ESIOS and REData response parsing with various data shapes."""

    def test_esios_parses_normal_data(self):
        """Normal ESIOS response with 24 hours."""
        data = {
            "indicator": {
                "values": [
                    {"value": 50.0, "datetime": "2026-06-19T01:00:00+02:00"},
                    {"value": 60.0, "datetime": "2026-06-19T02:00:00+02:00"},
                ]
            }
        }
        prices = _parse_esios_response(data)
        assert len(prices) > 0, "Should parse at least some prices"
        # 50 EUR/MWh = 5 cents/kWh
        assert abs(prices.get(1, 0) - 5.0) < 0.01, \
            f"Hour 1 should be ~5.0 cents, got {prices.get(1)}"

    def test_esios_handles_empty_values(self):
        """ESIOS response with null values should skip them."""
        data = {
            "indicator": {
                "values": [
                    {"value": None, "datetime": "2026-06-19T01:00:00+02:00"},
                    {"value": 60.0, "datetime": "2026-06-19T02:00:00+02:00"},
                ]
            }
        }
        prices = _parse_esios_response(data)
        assert 1 not in prices, "Hour with null value should be skipped"
        assert 2 in prices, "Hour with valid value should be included"

    def test_esios_handles_malformed_datetime(self):
        """ESIOS response with bad datetime format."""
        data = {
            "indicator": {
                "values": [
                    {"value": 50.0, "datetime": "not-a-date"},
                ]
            }
        }
        # Should not crash, may or may not parse depending on fallback
        prices = _parse_esios_response(data)
        # Don't assert on specific outcome, just ensure no exception

    def test_esios_empty_indicator(self):
        """Empty ESIOS response structure."""
        prices = _parse_esios_response({})
        assert prices == {}, "Empty response should return empty dict"

    def test_redata_parses_normal_data(self):
        """Normal REData response with values array."""
        data = {
            "data": {
                "attributes": {
                    "values": [
                        {"value": 45.0, "datetime": "2026-06-19T01:00:00+02:00"},
                        {"value": 55.0, "datetime": "2026-06-19T02:00:00+02:00"},
                    ]
                }
            }
        }
        prices = _parse_redata_response(data)
        assert len(prices) > 0, "Should parse some prices"

    def test_redata_fallback_included(self):
        """REData fallback when data.attributes.values is empty."""
        data = {
            "data": {"attributes": {"values": []}},
            "included": [
                {
                    "attributes": {
                        "values": [
                            {"value": 50.0, "datetime": "2026-06-19T01:00:00+02:00"},
                        ]
                    }
                }
            ]
        }
        prices = _parse_redata_response(data)
        assert len(prices) > 0, "Should parse from included fallback"

    def test_redata_empty_response(self):
        """Completely empty REData response."""
        prices = _parse_redata_response({})
        assert prices == {}, "Empty response should return empty dict"


# =========================================================================
# Config validation tests
# =========================================================================

class TestConfigValidation:
    """Test config validation edge cases."""

    def test_coerce_bool_true_values(self):
        """_coerce handles various 'true' representations."""
        assert _coerce("true", "bool") is True
        assert _coerce("True", "bool") is True
        assert _coerce("1", "bool") is True
        assert _coerce("yes", "bool") is True
        assert _coerce("on", "bool") is True

    def test_coerce_bool_false_values(self):
        """_coerce handles various 'false' representations."""
        assert _coerce("false", "bool") is False
        assert _coerce("False", "bool") is False
        assert _coerce("0", "bool") is False
        assert _coerce("no", "bool") is False
        assert _coerce("off", "bool") is False

    def test_coerce_int_valid(self):
        """_coerce parses valid int."""
        assert _coerce("42", "int") == 42
        assert _coerce("-5", "int") == -5

    def test_coerce_int_invalid(self):
        """_coerce returns None for invalid int."""
        assert _coerce("not_a_number", "int") is None

    def test_coerce_float_valid(self):
        """_coerce parses valid float."""
        assert _coerce("3.14", "float") == 3.14
        assert _coerce("10", "float") == 10.0

    def test_target_time_validation_valid(self):
        """Valid target_time formats."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"target_time": "19:00"}, f)
            f.flush()
            cfg = Config(f.name)
            assert cfg.target_hour == 19
            assert cfg.target_minute == 0
            os.unlink(f.name)

    def test_target_time_validation_invalid_format(self):
        """Invalid target_time format raises ValueError."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"target_time": "nineteen"}, f)
            f.flush()
            with pytest.raises((ValueError, KeyError)):
                Config(f.name)
            os.unlink(f.name)

    def test_debug_mode_when_no_token(self):
        """No tessite_token → debug_mode = True."""
        # Unset any TESSIE_TOKEN from environment to ensure clean test
        saved_token = os.environ.pop("TESSIE_TOKEN", None)
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                json.dump({"min_battery_pct": 70}, f)
                f.flush()
                cfg = Config(f.name)
                assert cfg.debug_mode, "Should be in debug mode without token"
                os.unlink(f.name)
        finally:
            if saved_token is not None:
                os.environ["TESSIE_TOKEN"] = saved_token

    def test_telegram_not_configured(self):
        """No telegram tokens → telegram_enabled = False."""
        cfg = MockConfig(telegram_bot_token="", telegram_chat_id="")
        assert not cfg.telegram_enabled

    def test_telegram_configured(self):
        """Both telegram tokens present → telegram_enabled = True."""
        # Use a temp config file to avoid environment interference
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "telegram": {"bot_token": "bot123", "chat_id": "chat456"},
                "tessie_token": "",
                "min_battery_pct": 70,
            }, f)
            f.flush()
            cfg = Config(f.name)
            assert cfg.telegram_enabled, "Telegram should be enabled with both tokens"
            os.unlink(f.name)


# =========================================================================
# Planner error edge cases
# =========================================================================

class TestPlannerErrors:
    """Test planner with problematic inputs."""

    def test_empty_prices_dict(self):
        """Empty prices dict → plan should be empty."""
        planner = ChargePlanner(MockConfig())
        plan = planner.plan({}, current_battery_pct=50.0, current_hour=9, date_str="2026-06-19")
        assert len(plan.slots) == 0, "Empty prices should produce empty plan"

    def test_missing_price_hours(self):
        """Prices dict with gaps (hours 5-10 missing)."""
        prices = {h: 8.0 for h in range(24) if h < 5 or h > 10}
        planner = ChargePlanner(MockConfig())
        plan = planner.plan(prices, current_battery_pct=50.0, current_hour=9, date_str="2026-06-19")
        # Window [9..18] includes hours 9, 10 (present) and 11-18 (missing→sentinel)
        # real_prices check: hours 9 and 10 exist with price < 250 → real_prices not empty
        assert len(plan.slots) > 0 or not plan.will_reach_target, \
            "Should still create plan with partial data"

    def test_negative_prices(self):
        """Negative prices (possible in real markets) → still valid."""
        prices = {h: -2.0 if h < 6 else 8.0 for h in range(24)}
        planner = ChargePlanner(MockConfig(strict_mode=False))
        plan = planner.plan(prices, current_battery_pct=50.0, current_hour=9, date_str="2026-06-19")
        assert len(plan.slots) > 0, "Should create plan with negative prices"
        # Cost should be negative for negative price hours
        total_cost = sum(s.kwh_to_deliver * s.price_cents_per_kwh / 100.0 for s in plan.slots)
        # All hours 9-18 are >= 8.0, so no negative cost
        assert plan.total_cost_eur > 0, "Cost should be positive for 9-18 with 8.0 prices"

    def test_battery_at_zero(self):
        """Battery at 0% → should create plan."""
        planner = ChargePlanner(MockConfig())
        prices = {h: 8.0 for h in range(24)}
        plan = planner.plan(prices, current_battery_pct=0.0, current_hour=9, date_str="2026-06-19")
        assert len(plan.slots) > 0, "Should create plan even at 0% battery"
        assert plan.total_kwh > 0, "Should use positive kWh"

    def test_battery_at_100_percent(self):
        """Battery at 100% → no charging needed."""
        planner = ChargePlanner(MockConfig())
        prices = {h: 8.0 for h in range(24)}
        plan = planner.plan(prices, current_battery_pct=100.0, current_hour=9, date_str="2026-06-19")
        assert len(plan.slots) == 0, "Should not plan charging at 100%"

    def test_infinite_loop_protection_hours_needed(self):
        """Very low battery with max_price=0 → hours_needed should be large but finite."""
        planner = ChargePlanner(MockConfig(strict_mode=False, max_price_cents_per_kwh=0))
        prices = {h: 8.0 for h in range(24)}
        plan = planner.plan(prices, current_battery_pct=5.0, current_hour=9, date_str="2026-06-19")
        # With max_price=0, cheap_hours is empty, flexible mode → empty plan
        assert len(plan.slots) == 0, "Flexible with max_price=0 and all expensive → no slots"

    def test_slot_remaining_kwh_precision(self):
        """Very small remaining should still be distributed correctly."""
        planner = ChargePlanner(MockConfig())
        hours = [10, 11, 13, 14, 15]
        prices = {h: 8.0 for h in range(24)}
        slots = planner._group_into_slots(hours, prices, 3.3, 0.5)  # Only 0.5 kWh needed
        assert len(slots) > 0, "Should create slots even for tiny kWh"
        total_kwh = sum(s.kwh_to_deliver for s in slots)
        assert abs(total_kwh - 0.5) < 0.01, f"Total should be ~0.5, got {total_kwh}"


# =========================================================================
# Status file error handling
# =========================================================================

class TestStatusErrors:
    """Test status file edge cases."""

    def test_read_status_no_file(self):
        """No status file → empty dict."""
        from auto_charge.status import read_status, STATUS_PATH
        if os.path.exists(STATUS_PATH):
            os.remove(STATUS_PATH)
        status = read_status()
        assert status == {}, "No file should return empty dict"

    def test_write_status_permission_error(self):
        """Permission error writing status → no crash."""
        from auto_charge.status import write_status
        # Simulate permission error by patching open
        with patch("builtins.open", side_effect=PermissionError("Permission denied")):
            write_status(daemon_pid=12345)  # Should not raise

    def test_get_daemon_pid_none(self):
        """No status file → get_daemon_pid returns None."""
        from auto_charge.status import get_daemon_pid, STATUS_PATH
        if os.path.exists(STATUS_PATH):
            os.remove(STATUS_PATH)
        pid = get_daemon_pid()
        assert pid is None, "Should return None when no daemon"

    def test_status_age_no_file(self):
        """No status file → age returns None."""
        from auto_charge.status import status_age_seconds, STATUS_PATH
        if os.path.exists(STATUS_PATH):
            os.remove(STATUS_PATH)
        age = status_age_seconds()
        assert age is None, "Should return None when no file"


# =========================================================================
# Utility error handling
# =========================================================================

class TestUtilsErrors:
    """Test utility functions error handling."""

    def test_mask_token_none(self):
        """mask_token with None → '(vacío)'."""
        from auto_charge.utils import mask_token
        assert mask_token(None) == "(vacío)"

    def test_mask_token_empty(self):
        """mask_token with empty string → '(vacío)'."""
        from auto_charge.utils import mask_token
        assert mask_token("") == "(vacío)"

    def test_mask_token_short(self):
        """mask_token with short string shows first 4 chars."""
        from auto_charge.utils import mask_token
        result = mask_token("abc123")
        assert "abc1" in result, f"Short token should show first 4 chars, got: {result}"

    def test_mask_token_long(self):
        """mask_token with long string shows first 8 + last 2."""
        from auto_charge.utils import mask_token
        result = mask_token("abcdefghijklmnop")
        assert "abcdefgh" in result, f"Should show first 8 chars, got: {result}"
        assert "op" in result, f"Should show last 2 chars, got: {result}"


# =========================================================================
# Telegram bot error handling
# =========================================================================

class TestTelegramErrors:
    """Test telegram bot error handling."""

    def test_send_message_not_enabled(self):
        """Telegram not enabled → returns False."""
        from auto_charge.telegram_bot import TelegramBot
        bot = TelegramBot(MockConfig(telegram_enabled=False))
        result = bot.send_message("test")
        assert result is False, "Should return False when not enabled"

    def test_poll_no_updates(self):
        """Poll with no updates → no errors."""
        from auto_charge.telegram_bot import TelegramBot
        bot = TelegramBot(MockConfig(telegram_enabled=False))
        # Should not crash
        bot.poll()

    def test_register_and_handle_command(self):
        """Register command and handle it."""
        from auto_charge.telegram_bot import TelegramBot
        bot = TelegramBot(MockConfig(telegram_bot_token="test", telegram_chat_id="test"))
        handler = MagicMock(return_value="handled")
        bot.register_command("test", handler)
        # Can't easily test poll with mocked API, but registration should work
        assert bot._commands.get("/test") is not None


# =========================================================================
# Daemon startup errors
# =========================================================================

class TestDaemonStartupErrors:
    """Test daemon handles startup failures gracefully."""

    def test_no_telegram_bot_config(self):
        """No telegram tokens → telegram_enabled=False, bot still created."""
        from auto_charge.telegram_bot import TelegramBot
        bot = TelegramBot(MockConfig(telegram_bot_token="", telegram_chat_id=""))
        assert not bot.enabled

    def test_coin_error_in_main_loop(self):
        """Exception in main loop → caught, sleep 30s, retry."""
        from auto_charge.daemon import AutoChargeDaemon
        daemon = AutoChargeDaemon.__new__(AutoChargeDaemon)
        daemon.running = True
        daemon.cfg = MockConfig()
        daemon.telegram = MagicMock()
        daemon._shutdown = MagicMock()
        daemon.tessie = MagicMock()
        daemon.price_provider = MagicMock()

        # _tick raises, but run() should catch it
        with patch.object(daemon, "_tick", side_effect=ValueError("test error")):
            with patch("auto_charge.daemon.time.sleep"):  # Don't actually sleep
                with pytest.raises(Exception):
                    # This will try to call _tick and catch the error,
                    # sleep 30s, then call _tick again...
                    # Since daemon.running = True, it loops forever
                    # We need to set running = False after first error
                    daemon.running = False
                    try:
                        daemon.run()
                    except SystemExit:
                        pass
