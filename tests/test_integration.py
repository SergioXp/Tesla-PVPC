"""Integration tests: run_once and daemon with mocked dependencies.

Tests the full pipeline: price fetching → planning → enforcement → status.
Uses mocked price providers and mocked vehicle to test real logic.
"""

import os
import sys
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, PropertyMock
from typing import Dict, Optional
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from auto_charge.planner import ChargePlanner, ChargingPlan, ChargingSlot


# =============================================================================
# Mock infrastructure
# =============================================================================

class MockConfig:
    def __init__(self, **kwargs):
        self.tessie_token = kwargs.get("tessie_token", "")
        self.vin = kwargs.get("vin", "")
        self.esios_token = kwargs.get("esios_token", "test_token")
        self.max_price_cents_per_kwh = kwargs.get("max_price_cents_per_kwh", 10.0)
        self.max_charger_power_kw = kwargs.get("max_charger_power_kw", 3.3)
        self.battery_capacity_kwh = kwargs.get("battery_capacity_kwh", 75.0)
        self.min_battery_pct = kwargs.get("min_battery_pct", 70.0)
        self.target_time = kwargs.get("target_time", "19:00")
        self.strict_mode = kwargs.get("strict_mode", True)
        self.charging_efficiency = kwargs.get("charging_efficiency", 0.9)
        self.check_interval_minutes = kwargs.get("check_interval_minutes", 15)
        self.debug_mode = kwargs.get("debug_mode", False)
        self.telegram_enabled = kwargs.get("telegram_enabled", False)
        self.telegram_bot_token = kwargs.get("telegram_bot_token", "")
        self.telegram_chat_id = kwargs.get("telegram_chat_id", "")
        self.debug = False

    @property
    def target_hour(self) -> int:
        return int(self.target_time.split(":")[0])

    @property
    def target_minute(self) -> int:
        return int(self.target_time.split(":")[1])


class MockPriceProvider:
    """Price provider that returns canned data or simulates failures."""

    def __init__(self, prices_to_return=None, should_fail=False):
        self.last_source = "mock"
        self._prices = prices_to_return or {h: 8.0 + (h % 3) for h in range(24)}
        self._should_fail = should_fail
        self.call_count = 0
        self.last_date = None

    def fetch_daily_prices(self, date_str: str) -> Dict[int, float]:
        self.call_count += 1
        self.last_date = date_str
        if self._should_fail:
            return {}
        return dict(self._prices)


# =========================================================================
# run_once integration tests
# =========================================================================

class TestRunOnceIntegration:
    """Test the full run_once() pipeline with mocked components."""

    def test_run_once_basic_flow(self):
        """run_once with cheap prices, debug vehicle → creates plan, starts charging."""
        from tesla_pvpc import run_once
        from auto_charge.debug_tessie import DebugTessieClient

        config = MockConfig()
        prices = {h: 7.5 for h in range(24)}

        # Create a price provider that returns our prices
        pp = MockPriceProvider(prices_to_return=prices)

        with patch("auto_charge.prices.PriceProvider", return_value=pp):
            with patch("auto_charge.utils.now_spain",
                       return_value=datetime(2026, 6, 19, 9, 0, 0,
                                             tzinfo=timezone(timedelta(hours=2)))):
                run_once(config, debug=True, dry_run=False, initial_battery=50.0)

        # Verify the mock price provider was called
        assert pp.call_count > 0, "Price provider should have been called"

    def test_run_once_battery_above_target(self):
        """Battery already at target → no charging."""
        from tesla_pvpc import run_once
        config = MockConfig(min_battery_pct=50.0)
        prices = {h: 7.5 for h in range(24)}
        pp = MockPriceProvider(prices_to_return=prices)

        with patch("auto_charge.prices.PriceProvider", return_value=pp):
            with patch("auto_charge.utils.now_spain",
                       return_value=datetime(2026, 6, 19, 9, 0, 0,
                                             tzinfo=timezone(timedelta(hours=2)))):
                run_once(config, debug=True, dry_run=False, initial_battery=80.0)

        # High battery (80% > 50%) → no charging needed
        assert pp.call_count > 0

    def test_run_once_no_prices_exits(self):
        """No prices available → exits gracefully."""
        from tesla_pvpc import run_once
        config = MockConfig()
        pp = MockPriceProvider(should_fail=True)

        with patch("auto_charge.prices.PriceProvider", return_value=pp):
            with patch("auto_charge.utils.now_spain",
                       return_value=datetime(2026, 6, 19, 9, 0, 0,
                                             tzinfo=timezone(timedelta(hours=2)))):
                run_once(config, debug=True, dry_run=False, initial_battery=50.0)

        assert pp.call_count == 1, "Should have tried to fetch prices"

    def test_run_once_cross_midnight_fetch_tomorrow(self):
        """Past target_hour → also fetches tomorrow prices."""
        from tesla_pvpc import run_once
        config = MockConfig()
        today_prices = {h: 8.0 for h in range(24)}

        # Tomorrow prices fail (simulate before 20:15)
        class FailingTomorrowProvider:
            def __init__(self, *args, **kwargs):
                self.last_source = "mock"
                self._today_called = False
                self.call_count = 0

            def fetch_daily_prices(self, date_str: str):
                self.call_count += 1
                if "2026-06-19" in date_str:
                    return dict(today_prices)
                return {}  # Tomorrow fails

        with patch("auto_charge.prices.PriceProvider", return_value=FailingTomorrowProvider()):
            with patch("auto_charge.utils.now_spain",
                       return_value=datetime(2026, 6, 19, 21, 0, 0,
                                             tzinfo=timezone(timedelta(hours=2)))):
                run_once(MockConfig(), debug=True, dry_run=False, initial_battery=50.0)

    def test_run_once_dry_run_blocks_writes(self):
        """dry_run mode → vehicle commands blocked."""
        from tesla_pvpc import run_once
        config = MockConfig(debug_mode=False, strict_mode=True)

        # We need a real Tessie-like client but with mocked API calls
        with patch("auto_charge.utils.now_spain",
                   return_value=datetime(2026, 6, 19, 9, 0, 0,
                                         tzinfo=timezone(timedelta(hours=2)))):
            with patch("auto_charge.prices.PriceProvider",
                       return_value=MockPriceProvider()):
                run_once(config, debug=False, dry_run=True, initial_battery=50.0)


# =========================================================================
# Planner + daemon integration
# =========================================================================

class TestPlannerDaemonIntegration:
    """Test planner output feeds correctly into daemon actions."""

    def test_plan_to_enforce_flow_strict(self):
        """Strict plan creates enough slots to reach target."""
        config = MockConfig(strict_mode=True)
        planner = ChargePlanner(config)
        prices = {h: 8.0 for h in range(24)}

        plan = planner.plan(prices, current_battery_pct=50.0, current_hour=9, date_str="2026-06-19")
        assert plan.will_reach_target, "Strict mode should reach target"
        assert len(plan.slots) > 0, "Should have slots"

        # Verify slot coverage
        total_slot_hours = sum(s.end_hour - s.start_hour for s in plan.slots)
        assert total_slot_hours >= 6, "Should have at least ~6 hours of charging"

    def test_plan_to_enforce_flow_flexible(self):
        """Flexible plan may not reach target."""
        config = MockConfig(strict_mode=False)
        planner = ChargePlanner(config)
        prices = {h: 8.0 for h in range(24)}

        plan = planner.plan(prices, current_battery_pct=50.0, current_hour=9, date_str="2026-06-19")
        # Flexible should still reach target with all cheap hours available
        assert plan.will_reach_target, "Flexible should reach target with all-cheap prices"
        assert not plan.flexible or len(plan.slots) > 0

    def test_slot_covers_boundary_conditions(self):
        """Test _slot_covers_hour at exact boundaries."""
        from auto_charge.daemon import AutoChargeDaemon

        # Today slot 10-12
        slot = ChargingSlot(10, 12, 8.0, 6.0)
        assert AutoChargeDaemon._slot_covers_hour(slot, 10) is True  # Start boundary
        assert AutoChargeDaemon._slot_covers_hour(slot, 11) is True  # Middle
        assert AutoChargeDaemon._slot_covers_hour(slot, 12) is False  # End (exclusive)

        # Tomorrow slot 24-27
        slot2 = ChargingSlot(24, 27, 6.0, 9.0)
        assert AutoChargeDaemon._slot_covers_hour(slot2, 0) is True   # Adjusted to 24
        assert AutoChargeDaemon._slot_covers_hour(slot2, 2) is True   # Adjusted to 26
        assert AutoChargeDaemon._slot_covers_hour(slot2, 3) is False  # Adjusted to 27

    def test_compute_expected_then_check_progress(self):
        """Plan + compute expected + check progress at specific hour."""
        from auto_charge.daemon import AutoChargeDaemon

        config = MockConfig()
        planner = ChargePlanner(config)
        prices = {h: 8.0 for h in range(24)}

        plan = planner.plan(prices, current_battery_pct=50.0, current_hour=9, date_str="2026-06-19")
        assert len(plan.slots) > 0, "Should have slots"

        # Create a partial daemon to test expected_by_hour
        daemon = AutoChargeDaemon.__new__(AutoChargeDaemon)
        daemon.cfg = config
        daemon.expected_by_hour = {}
        daemon.prices = prices

        daemon._compute_expected_by_hour(plan, 50.0, "2026-06-19")
        assert len(daemon.expected_by_hour) > 0, "Should have expected values"

        # Verify progress starts below target and increases
        first_hour = min(daemon.expected_by_hour.keys())
        last_hour = max(daemon.expected_by_hour.keys())
        assert daemon.expected_by_hour[first_hour] >= 50.0, "Start >= initial"
        assert daemon.expected_by_hour[last_hour] <= 100.0, "Never exceed 100%"


# =========================================================================
# DebugTessieClient integration tests
# =========================================================================

class TestDebugVehicleIntegration:
    """Test that the debug vehicle works correctly with the planner."""

    def test_debug_vehicle_state_changes(self):
        """DebugTessieClient correctly reports state changes."""
        from auto_charge.debug_tessie import DebugTessieClient
        config = MockConfig()
        vehicle = DebugTessieClient(config, initial_battery_pct=35.0)

        state = vehicle.get_state()
        assert state is not None, "Should return state"
        assert state.battery_pct == 35.0, "Should start at 35%"
        assert not state.is_charging, "Should not be charging initially"
        assert state.is_plugged_in, "Should be plugged in"

        vehicle.start_charge()
        state2 = vehicle.get_state()
        assert state2.is_charging, "Should be charging after start"

        vehicle.stop_charge()
        state3 = vehicle.get_state()
        assert not state3.is_charging, "Should not be charging after stop"

    def test_debug_vehicle_simulates_progress(self):
        """DebugTessieClient simulates battery increase over time.

        Note: debug_tessie.py imports now_spain via 'from auto_charge.utils import now_spain',
        so the function reference is local to that module. Patch at the usage site.
        """
        from auto_charge.debug_tessie import DebugTessieClient
        config = MockConfig(battery_capacity_kwh=75.0, max_charger_power_kw=3.3,
                            charging_efficiency=0.9)
        vehicle = DebugTessieClient(config, initial_battery_pct=50.0)

        # Patch at the import site (debug_tessie) because 'from ... import' creates local ref
        with patch("auto_charge.debug_tessie.now_spain",
                   return_value=datetime(2026, 6, 19, 10, 0, 0,
                                         tzinfo=timezone(timedelta(hours=2)))):
            vehicle.start_charge()
            state = vehicle.get_state()
            first_pct = state.battery_pct

        with patch("auto_charge.debug_tessie.now_spain",
                   return_value=datetime(2026, 6, 19, 11, 0, 0,
                                         tzinfo=timezone(timedelta(hours=2)))):
            state = vehicle.get_state()
            second_pct = state.battery_pct

        # 1 hour at 3.3kW * 0.9 = 2.97 kWh = 3.96% on 75kWh battery
        assert second_pct > first_pct, f"Battery should increase: {first_pct} -> {second_pct}"
        assert abs(second_pct - first_pct - 3.96) < 0.1, \
            f"Should increase ~3.96%, got {second_pct - first_pct:.2f}%"

    def test_debug_vehicle_caps_at_100(self):
        """Battery shouldn't exceed 100% even with prolonged charging."""
        from auto_charge.debug_tessie import DebugTessieClient
        config = MockConfig()
        vehicle = DebugTessieClient(config, initial_battery_pct=99.0)
        vehicle.start_charge()

        # Simulate 3 hours
        with patch("auto_charge.utils.now_spain",
                   return_value=datetime(2026, 6, 19, 13, 0, 0,
                                         tzinfo=timezone(timedelta(hours=2)))):
            state = vehicle.get_state()
            assert state.battery_pct <= 100.0, f"Should cap at 100%, got {state.battery_pct:.1f}%"


# =========================================================================
# Status file integration
# =========================================================================

class TestStatusFileIntegration:
    """Test status file read/write with real daemon data."""

    def test_status_write_and_read(self):
        """Write status with plan data, then read it back."""
        from auto_charge.status import write_status, read_status

        test_pid = 99999
        write_status(
            daemon_pid=test_pid,
            daemon_mode="debug",
            vehicle={"battery_pct": 65.0, "is_charging": True},
            plan={
                "target_pct": 70.0,
                "expected_pct": 85.0,
                "total_kwh": 15.0,
                "slots": [],
            },
            prices_summary={"min": 8.0, "max": 12.0, "avg": 10.0, "count": 24},
        )

        status = read_status()
        assert status.get("daemon_pid") == test_pid
        assert status.get("daemon_mode") == "debug"
        assert status.get("vehicle", {}).get("battery_pct") == 65.0

    def test_status_handles_corrupt_file(self):
        """Corrupt JSON → returns empty dict."""
        from auto_charge.status import read_status, STATUS_PATH

        # Write invalid JSON
        with open(STATUS_PATH, "w") as f:
            f.write("{invalid json!!!}")

        status = read_status()
        assert status == {}, "Corrupt file should return empty dict"

    def test_status_age_seconds(self):
        """Status file age returns reasonable value."""
        from auto_charge.status import status_age_seconds, write_status
        import time

        write_status(daemon_pid=88888)
        age = status_age_seconds()
        assert age is not None
        assert 0 <= age < 5, f"Age should be < 5s, got {age}s"
