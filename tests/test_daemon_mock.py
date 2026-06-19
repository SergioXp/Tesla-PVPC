"""Advanced tests for daemon with mocked components.

Covers:
- _tick: full loop with various state combinations
- _fetch_prices: with and without include_tomorrow
- _shutdown: signal handler, graceful stop
- run(): main loop, error handling in loop
- _cmd_status: formatted status string
- _cmd_set: config changes via Telegram
- _cmd_force_plan, _cmd_start_charge, _cmd_stop_charge
- _ensure_charge_limit edge cases
- _write_status error handling
"""

import os
import sys
import signal
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, PropertyMock, call
from typing import Dict, Optional, Any
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from auto_charge.daemon import AutoChargeDaemon, run_daemon
from auto_charge.planner import ChargePlanner, ChargingPlan, ChargingSlot


# =============================================================================
# Mock utilities
# =============================================================================

class MockConfig:
    """Minimal mock config for daemon tests."""
    def __init__(self, **kwargs):
        self.tessie_token = kwargs.get("tessie_token", "")
        self.vin = kwargs.get("vin", "")
        self.esios_token = kwargs.get("esios_token", "")
        self.max_price_cents_per_kwh = kwargs.get("max_price_cents_per_kwh", 10.0)
        self.max_charger_power_kw = kwargs.get("max_charger_power_kw", 3.3)
        self.battery_capacity_kwh = kwargs.get("battery_capacity_kwh", 75.0)
        self.min_battery_pct = kwargs.get("min_battery_pct", 70.0)
        self.target_time = kwargs.get("target_time", "19:00")
        self.strict_mode = kwargs.get("strict_mode", True)
        self.charging_efficiency = kwargs.get("charging_efficiency", 0.9)
        self.check_interval_minutes = kwargs.get("check_interval_minutes", 15)
        self.debug_mode = kwargs.get("debug_mode", False)  # Default to NON-debug
        self.telegram_enabled = kwargs.get("telegram_enabled", False)
        self.telegram_bot_token = kwargs.get("telegram_bot_token", "")
        self.telegram_chat_id = kwargs.get("telegram_chat_id", "")

    @property
    def target_hour(self) -> int:
        return int(self.target_time.split(":")[0])

    @property
    def target_minute(self) -> int:
        return int(self.target_time.split(":")[1])


class MockTessie:
    """Mock vehicle recording commands."""
    def __init__(self, battery_pct=50.0, charging=False, plugged_in=True):
        self._battery_pct = battery_pct
        self._charging = charging
        self._plugged_in = plugged_in
        self._charge_limit = 100.0
        self.commands = []

    def get_state(self):
        from auto_charge.tessie import VehicleState
        raw = {
            "charge_state": {
                "battery_level": self._battery_pct,
                "charging_state": "Charging" if self._charging else "Stopped",
                "charge_port_door_open": self._plugged_in,
                "charge_port_latch": "Engaged" if self._plugged_in else "Disengaged",
                "charge_limit_soc": self._charge_limit,
                "charger_power": 3.3 if self._charging else 0,
            }
        }
        return VehicleState(raw)

    def start_charge(self):
        self.commands.append("start_charge")
        self._charging = True
        return True

    def stop_charge(self):
        self.commands.append("stop_charge")
        self._charging = False
        return True

    def set_charge_limit(self, percent):
        self.commands.append(f"set_charge_limit({percent})")
        self._charge_limit = float(percent)
        return True


def make_daemon(config=None, tessie=None):
    """Create daemon with mocked components.
    
    Note: _shutdown is NOT mocked so the real method (setting running=False) works.
    Signal handlers are not registered because we use __new__().
    """
    cfg = config or MockConfig()
    daemon = AutoChargeDaemon.__new__(AutoChargeDaemon)
    daemon.cfg = cfg
    daemon.price_provider = MagicMock()
    daemon.price_provider.last_source = "mock"
    # Default: return today prices as 24 hours
    daemon.price_provider.fetch_daily_prices.return_value = {h: 8.0 for h in range(24)}
    daemon.tessie = tessie or MockTessie()
    daemon._debug_mode = cfg.debug_mode
    daemon.current_plan = None
    daemon.prices = {}
    daemon.prices_date = ""
    daemon.prices_fetched_today = False
    daemon.planned_today = False
    daemon.expected_by_hour = {}
    daemon.last_state_time = None
    daemon.running = True
    daemon._day_tracker = ""
    daemon._today_early_plan_done = False
    daemon.planner = ChargePlanner(cfg)
    daemon.telegram = MagicMock()
    # Don't mock _shutdown - let the real method work
    return daemon


# =============================================================================
# _shutdown tests
# =============================================================================

class TestShutdown:
    """Test daemon shutdown behavior."""

    def test_shutdown_sets_running_false(self):
        """_shutdown() sets running=False."""
        daemon = make_daemon()
        daemon.running = True
        daemon._shutdown()  # Calls the REAL _shutdown method
        assert daemon.running is False

    def test_shutdown_with_signal(self):
        """_shutdown() can be called with signal args (as signal handler)."""
        daemon = make_daemon()
        daemon.running = True
        daemon._shutdown(signum=signal.SIGTERM, frame=None)
        assert daemon.running is False


# =============================================================================
# _fetch_prices tests
# =============================================================================

class TestFetchPrices:
    """Test _fetch_prices with mocked price provider."""

    def test_fetch_today_only(self):
        """_fetch_prices(include_tomorrow=False) fetches only today."""
        daemon = make_daemon()
        daemon.price_provider.fetch_daily_prices.return_value = {h: 8.0 for h in range(24)}

        with patch("auto_charge.daemon.today_str", return_value="2026-06-19"):
            daemon._fetch_prices(include_tomorrow=False)

        assert len(daemon.prices) == 24
        assert daemon.prices_date == "2026-06-19"
        assert daemon.prices_fetched_today is False  # Not set when no tomorrow

    def test_fetch_with_tomorrow(self):
        """_fetch_prices(include_tomorrow=True) merges tomorrow with +24 offset."""
        daemon = make_daemon()

        def side_effect(date_str):
            if "2026-06-19" in date_str:
                return {h: 8.0 for h in range(24)}
            return {h: 6.0 for h in range(24)}
        daemon.price_provider.fetch_daily_prices.side_effect = side_effect

        with patch("auto_charge.daemon.today_str", return_value="2026-06-19"), \
             patch("auto_charge.daemon.tomorrow_str", return_value="2026-06-20"):
            daemon._fetch_prices(include_tomorrow=True)

        assert len(daemon.prices) == 48, f"Should have 48 prices, got {len(daemon.prices)}"
        assert daemon.prices[24] == 6.0, "Tomorrow hour 0 → offset 24"
        assert daemon.prices[47] == 6.0, "Tomorrow hour 23 → offset 47"
        assert daemon.prices_fetched_today is True

    def test_fetch_today_fails(self):
        """Today prices fail → prices unchanged, early return."""
        daemon = make_daemon()
        daemon.price_provider.fetch_daily_prices.return_value = {}
        daemon.prices = {0: 1.0}

        daemon._fetch_prices()

        assert daemon.prices == {0: 1.0}

    def test_fetch_today_few_hours(self):
        """Today prices < 20 hours → rejected."""
        daemon = make_daemon()
        daemon.price_provider.fetch_daily_prices.return_value = {h: 8.0 for h in range(5)}
        daemon.prices = {0: 1.0}

        daemon._fetch_prices()

        assert daemon.prices == {0: 1.0}

    def test_fetch_tomorrow_fails_keeps_today(self):
        """Tomorrow fails → keeps today prices, doesn't set prices_fetched_today."""
        daemon = make_daemon()

        def side_effect(date_str):
            if "2026-06-19" in date_str:
                return {h: 8.0 for h in range(24)}
            return {}
        daemon.price_provider.fetch_daily_prices.side_effect = side_effect

        with patch("auto_charge.daemon.today_str", return_value="2026-06-19"), \
             patch("auto_charge.daemon.tomorrow_str", return_value="2026-06-20"):
            daemon._fetch_prices(include_tomorrow=True)

        assert len(daemon.prices) == 24
        assert daemon.prices_fetched_today is False


# =============================================================================
# _tick tests (key state transitions)
# =============================================================================

class TestTick:
    """Test _tick behavior with various state combinations."""

    def test_tick_resets_daily_flags_on_day_change(self):
        """New day resets all daily state.
        
        After reset, the early plan step runs (before target_hour, debug=False),
        fetches prices and creates a plan, setting planned_today=True.
        """
        daemon = make_daemon(MockConfig(debug_mode=False))
        daemon._day_tracker = "2026-06-18"
        daemon.prices_fetched_today = True
        daemon.planned_today = True
        daemon.current_plan = ChargingPlan(slots=[], target_pct=70.0)
        daemon.expected_by_hour = {10: 50.0}

        with patch("auto_charge.daemon.now_spain",
                   return_value=datetime(2026, 6, 19, 9, 0, 0, tzinfo=timezone(timedelta(hours=2)))), \
             patch("auto_charge.daemon.today_str", return_value="2026-06-19"), \
             patch("auto_charge.daemon.time.sleep", side_effect=SystemExit("sleep called")), \
             pytest.raises(SystemExit):
            daemon._tick()

        assert daemon._day_tracker == "2026-06-19", "Day should be updated"
        # prices_fetched_today was reset, but early plan step may fetch prices
        # planned_today was reset, but early plan creates plan and sets it True
        assert daemon.current_plan is not None, "Early plan should create a plan"

    def test_tick_telegram_poll_called(self):
        """_tick calls telegram.poll() when enabled."""
        daemon = make_daemon(MockConfig(debug_mode=False))
        daemon.cfg.telegram_enabled = True
        daemon.telegram.poll = MagicMock()
        # Set early plan done to skip the early plan step
        daemon._today_early_plan_done = True

        with patch("auto_charge.daemon.now_spain",
                   return_value=datetime(2026, 6, 19, 9, 0, 0, tzinfo=timezone(timedelta(hours=2)))), \
             patch("auto_charge.daemon.today_str", return_value="2026-06-19"), \
             patch("auto_charge.daemon.time.sleep", side_effect=SystemExit("stop")):
            try:
                daemon._tick()
            except SystemExit:
                pass

        daemon.telegram.poll.assert_called()

    def test_tick_early_plan_when_before_target(self):
        """Before target hour, not debug → early plan step runs."""
        daemon = make_daemon(MockConfig(debug_mode=False))
        daemon.cfg.target_time = "19:00"
        daemon._today_early_plan_done = False

        with patch("auto_charge.daemon.now_spain",
                   return_value=datetime(2026, 6, 19, 9, 0, 0, tzinfo=timezone(timedelta(hours=2)))), \
             patch("auto_charge.daemon.today_str", return_value="2026-06-19"), \
             patch("auto_charge.daemon.time.sleep", side_effect=SystemExit("stop")):
            try:
                daemon._tick()
            except SystemExit:
                pass

        assert daemon._today_early_plan_done is True

    def test_tick_fetch_tomorrow_after_2015(self):
        """After 20:15, not debug → fetches tomorrow prices."""
        daemon = make_daemon(MockConfig(debug_mode=False))
        daemon.prices_fetched_today = False
        daemon._today_early_plan_done = True

        with patch("auto_charge.daemon.now_spain",
                   return_value=datetime(2026, 6, 19, 20, 30, 0, tzinfo=timezone(timedelta(hours=2)))), \
             patch("auto_charge.daemon.today_str", return_value="2026-06-19"), \
             patch("auto_charge.daemon.tomorrow_str", return_value="2026-06-20"), \
             patch("auto_charge.daemon.time.sleep", side_effect=SystemExit("stop")):
            try:
                daemon._tick()
            except SystemExit:
                pass

        assert daemon.price_provider.fetch_daily_prices.call_count >= 2

    def test_tick_writes_status(self):
        """_tick calls _write_status."""
        daemon = make_daemon(MockConfig(debug_mode=False))
        daemon._today_early_plan_done = True

        with patch("auto_charge.daemon.now_spain",
                   return_value=datetime(2026, 6, 19, 9, 0, 0, tzinfo=timezone(timedelta(hours=2)))), \
             patch("auto_charge.daemon.today_str", return_value="2026-06-19"), \
             patch("auto_charge.daemon.time.sleep", side_effect=SystemExit("stop")), \
             patch.object(daemon, "_write_status") as mock_write:
            try:
                daemon._tick()
            except SystemExit:
                pass

        mock_write.assert_called_once()

    def test_tick_enforces_plan_when_active(self):
        """_tick calls _enforce_plan when there's an active plan."""
        daemon = make_daemon(MockConfig(debug_mode=False))
        daemon._today_early_plan_done = True
        daemon.current_plan = ChargingPlan(
            slots=[ChargingSlot(9, 12, 8.0, 9.0)],
            target_pct=70.0,
        )

        with patch("auto_charge.daemon.now_spain",
                   return_value=datetime(2026, 6, 19, 10, 0, 0, tzinfo=timezone(timedelta(hours=2)))), \
             patch("auto_charge.daemon.today_str", return_value="2026-06-19"), \
             patch("auto_charge.daemon.time.sleep", side_effect=SystemExit("stop")), \
             patch.object(daemon, "_enforce_plan") as mock_enforce:
            try:
                daemon._tick()
            except SystemExit:
                pass

        mock_enforce.assert_called()


# =============================================================================
# run() tests
# =============================================================================

class TestRun:
    """Test daemon main loop."""

    def test_run_calls_tick(self):
        """run() calls _tick() in the loop."""
        daemon = make_daemon(MockConfig(debug_mode=False))

        # Make _tick raise to exit the loop
        with patch.object(daemon, "_tick", side_effect=[None, SystemExit("exit")]):
            with pytest.raises(SystemExit):
                daemon.run()

    def test_run_debug_mode_fetches_immediately(self):
        """Debug mode → fetches prices at startup."""
        daemon = make_daemon(MockConfig(debug_mode=True))
        daemon.price_provider.fetch_daily_prices.return_value = {h: 8.0 for h in range(24)}

        with patch("auto_charge.daemon.now_spain",
                   return_value=datetime(2026, 6, 19, 9, 0, 0, tzinfo=timezone(timedelta(hours=2)))), \
             patch.object(daemon, "_tick", side_effect=SystemExit("exit")):
            with pytest.raises(SystemExit):
                daemon.run()

        assert daemon.price_provider.fetch_daily_prices.called, \
            "Debug mode should fetch prices at startup"

    def test_run_error_in_loop_caught(self):
        """Error in _tick is caught, loop continues."""
        daemon = make_daemon(MockConfig(debug_mode=False))

        tick_call_count = [0]

        def tick_with_error():
            tick_call_count[0] += 1
            if tick_call_count[0] == 1:
                raise ValueError("test error")
            # On second call, set running=False
            daemon.running = False

        with patch.object(daemon, "_tick", side_effect=tick_with_error), \
             patch("auto_charge.daemon.time.sleep") as mock_sleep:
            daemon.run()

        # Should have ticked twice (error caught, second tick exits)
        assert tick_call_count[0] == 2
        # Should have slept once (after the error)
        mock_sleep.assert_called_once_with(30)


# =============================================================================
# _cmd_status tests
# =============================================================================

class TestCmdStatus:
    """Test Telegram status command."""

    def test_status_with_plan(self):
        """_cmd_status returns formatted string with vehicle state and plan."""
        daemon = make_daemon()
        daemon.current_plan = ChargingPlan(
            slots=[ChargingSlot(10, 13, 8.0, 9.0)],
            target_pct=70.0,
            total_kwh=9.0,
            total_cost_eur=0.72,
            expected_final_pct=62.0,
        )

        result = daemon._cmd_status()
        assert "Batería" in result
        assert "50.0%" in result

    def test_status_without_plan(self):
        """_cmd_status says 'Sin plan activo' when no plan."""
        daemon = make_daemon()
        daemon.current_plan = None
        result = daemon._cmd_status()
        assert "Sin plan activo" in result

    def test_status_state_none(self):
        """Vehicle unreachable → error message."""
        class DeadTessie:
            def get_state(self): return None
        daemon = make_daemon(tessie=DeadTessie())
        result = daemon._cmd_status()
        assert "No se puede contactar" in result


# =============================================================================
# _cmd_force_plan tests
# =============================================================================

class TestCmdForcePlan:
    """Test Telegram force plan command."""

    def test_force_plan_success(self):
        """_cmd_force_plan creates plan and returns success."""
        daemon = make_daemon()
        daemon.prices = {h: 8.0 for h in range(24)}
        daemon.prices_date = "2026-06-19"

        with patch("auto_charge.daemon.now_spain",
                   return_value=datetime(2026, 6, 19, 9, 0, 0, tzinfo=timezone(timedelta(hours=2)))):
            result = daemon._cmd_force_plan()
        assert "plan" in result.lower() or "✅" in result


# =============================================================================
# _cmd_start/_stop_charge tests
# =============================================================================

class TestCmdCharge:
    """Test Telegram charge commands."""

    def test_start_charge(self):
        """_cmd_start_charge returns success message."""
        daemon = make_daemon()
        result = daemon._cmd_start_charge()
        assert "✅" in result or "carga" in result.lower()

    def test_stop_charge(self):
        """_cmd_stop_charge returns success message."""
        daemon = make_daemon()
        result = daemon._cmd_stop_charge()
        assert "✅" in result or "parada" in result.lower()


# =============================================================================
# _cmd_set tests
# =============================================================================

class TestCmdSet:
    """Test Telegram set config command."""

    def test_set_valid_key(self):
        """_cmd_set with valid key updates config."""
        daemon = make_daemon()
        daemon.cfg.set = MagicMock()
        result = daemon._cmd_set("chat1", "max_price_cents_per_kwh 8")
        assert "✅" in result

    def test_set_no_args(self):
        """_cmd_set without args shows usage."""
        daemon = make_daemon()
        result = daemon._cmd_set("chat1", "")
        assert "Uso" in result

    def test_set_invalid_key(self):
        """_cmd_set with invalid key shows error."""
        daemon = make_daemon()
        result = daemon._cmd_set("chat1", "invalid_key 123")
        assert "Clave no permitida" in result

    def test_set_one_arg(self):
        """_cmd_set with only key (no value) shows usage."""
        daemon = make_daemon()
        result = daemon._cmd_set("chat1", "max_price_cents_per_kwh")
        assert "Uso" in result


# =============================================================================
# _write_status tests
# =============================================================================

class TestWriteStatus:
    """Test _write_status error handling."""

    def test_write_status_with_state(self):
        """_write_status calls write_status with daemon data."""
        daemon = make_daemon()
        daemon.current_plan = ChargingPlan(
            slots=[ChargingSlot(10, 13, 8.0, 9.0)],
            target_pct=70.0,
            total_kwh=9.0,
            total_cost_eur=0.72,
            expected_final_pct=62.0,
        )
        daemon.prices = {h: 8.0 for h in range(24)}
        daemon.prices_date = "2026-06-19"

        with patch("auto_charge.daemon.write_status") as mock_write:
            daemon._write_status()
        mock_write.assert_called_once()

    def test_write_status_handles_tessie_error(self):
        """Tessie error during write_status → doesn't crash."""
        class ErrorTessie:
            def get_state(self):
                raise RuntimeError("API error")
        daemon = make_daemon(tessie=ErrorTessie())

        with patch("auto_charge.daemon.write_status") as mock_write:
            daemon._write_status()
        mock_write.assert_called_once()


# =============================================================================
# _ensure_charge_limit tests
# =============================================================================

class TestEnsureChargeLimitAdvanced:
    """Test charge limit enforcement edge cases."""

    def test_limit_already_high(self):
        """Charge limit already high → no change."""
        daemon = make_daemon(tessie=MockTessie(battery_pct=50.0))
        daemon.tessie._charge_limit = 90.0
        daemon._ensure_charge_limit()
        assert not any("set_charge_limit" in c for c in daemon.tessie.commands)

    def test_limit_at_exact_target(self):
        """Charge limit exactly at target → no change."""
        daemon = make_daemon(tessie=MockTessie(battery_pct=50.0))
        daemon.tessie._charge_limit = 70.0
        daemon._ensure_charge_limit()
        assert not any("set_charge_limit" in c for c in daemon.tessie.commands)

    def test_limit_too_low_adjusts(self):
        """Charge limit below target → adjusts."""
        daemon = make_daemon(tessie=MockTessie(battery_pct=50.0))
        daemon.tessie._charge_limit = 30.0
        daemon._ensure_charge_limit()
        assert any("set_charge_limit" in c for c in daemon.tessie.commands)


# =============================================================================
# run_daemon entry point
# =============================================================================

class TestRunDaemon:
    """Test run_daemon entry point."""

    def test_run_daemon_config_not_found(self):
        """run_daemon with non-existent config → exits with error."""
        with patch("auto_charge.daemon.Config", side_effect=FileNotFoundError("Config not found")), \
             pytest.raises(SystemExit):
            run_daemon("/nonexistent/config.json")

    def test_run_daemon_starts(self):
        """run_daemon creates daemon and runs it."""
        mock_daemon = MagicMock()
        mock_daemon.run = MagicMock()

        with patch("auto_charge.daemon.Config") as mock_config, \
             patch("auto_charge.daemon.AutoChargeDaemon", return_value=mock_daemon):
            run_daemon()

        mock_daemon.run.assert_called_once()
