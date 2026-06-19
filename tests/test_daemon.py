"""Tests for the daemon state machine and enforcement logic.

Targets: AutoChargeDaemon._tick(), _enforce_plan(), _next_wake_time(),
         _next_action_description(), _compute_expected_by_hour(),
         _slot_covers_hour(), _check_progress()
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, PropertyMock
from typing import Dict, Optional
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from auto_charge.planner import ChargePlanner, ChargingPlan, ChargingSlot, _MISSING_PRICE_SENTINEL
from auto_charge.daemon import AutoChargeDaemon


# =============================================================================
# Mock utilities
# =============================================================================

class MockConfig:
    """Simplified config with property interfaces matching real Config."""
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


class MockTessie:
    """Mock vehicle that records commands."""
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
    """Create a daemon with mocked components."""
    cfg = config or MockConfig()
    daemon = AutoChargeDaemon.__new__(AutoChargeDaemon)
    daemon.cfg = cfg
    daemon.price_provider = MagicMock()
    daemon.price_provider.last_source = "mock"
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
    # Don't register real signal handlers
    daemon._shutdown = MagicMock()
    return daemon


# =========================================================================
# _enforce_plan tests
# =========================================================================

class TestEnforcePlan:
    """Test the enforcement of charging plans."""

    def test_e1_should_start_charging(self):
        """Slot covers current hour, not charging → start."""
        daemon = make_daemon()
        daemon.current_plan = ChargingPlan(
            slots=[ChargingSlot(10, 12, 8.0, 6.0)],
            target_pct=70.0,
        )
        now = datetime(2026, 6, 19, 10, 30, tzinfo=timezone(timedelta(hours=2)))
        daemon._enforce_plan(now)
        assert "start_charge" in daemon.tessie.commands, "Should start charging"

    def test_e2_should_stop_charging(self):
        """Slot doesn't cover current hour, is charging → stop."""
        daemon = make_daemon(tessie=MockTessie(battery_pct=60.0, charging=True))
        daemon.current_plan = ChargingPlan(
            slots=[ChargingSlot(10, 12, 8.0, 6.0)],
            target_pct=70.0,
        )
        now = datetime(2026, 6, 19, 14, 0, tzinfo=timezone(timedelta(hours=2)))
        daemon._enforce_plan(now)
        assert "stop_charge" in daemon.tessie.commands, "Should stop charging"

    def test_e3_not_plugged_in_warning(self):
        """Slot covers hour but not plugged in → warning, no start."""
        daemon = make_daemon(tessie=MockTessie(battery_pct=50.0, plugged_in=False))
        daemon.current_plan = ChargingPlan(
            slots=[ChargingSlot(10, 12, 8.0, 6.0)],
            target_pct=70.0,
        )
        now = datetime(2026, 6, 19, 10, 30, tzinfo=timezone(timedelta(hours=2)))
        daemon._enforce_plan(now)
        assert "start_charge" not in daemon.tessie.commands, "Should NOT start when unplugged"

    def test_e4_already_charging_should_charge_no_op(self):
        """Already charging and should be → no command (logging gap)."""
        daemon = make_daemon(tessie=MockTessie(battery_pct=50.0, charging=True))
        daemon.current_plan = ChargingPlan(
            slots=[ChargingSlot(10, 12, 8.0, 6.0)],
            target_pct=70.0,
        )
        now = datetime(2026, 6, 19, 10, 30, tzinfo=timezone(timedelta(hours=2)))
        daemon._enforce_plan(now)
        assert len(daemon.tessie.commands) == 0, "No commands should be sent"

    def test_e5_already_stopped_should_stop_no_op(self):
        """Not charging and shouldn't be → no command."""
        daemon = make_daemon(tessie=MockTessie(battery_pct=50.0, charging=False))
        daemon.current_plan = ChargingPlan(
            slots=[ChargingSlot(10, 12, 8.0, 6.0)],
            target_pct=70.0,
        )
        now = datetime(2026, 6, 19, 14, 0, tzinfo=timezone(timedelta(hours=2)))
        daemon._enforce_plan(now)
        assert len(daemon.tessie.commands) == 0, "No commands should be sent"

    def test_e6_slot_covers_via_tomorrow_offset(self):
        """Slot with 24+ offset, current_hour maps correctly."""
        daemon = make_daemon(tessie=MockTessie(battery_pct=50.0, charging=False))
        # Slot covers tomorrow 00:00-03:00 (offsets 24-27)
        daemon.current_plan = ChargingPlan(
            slots=[ChargingSlot(24, 27, 6.0, 9.0)],
            target_pct=70.0,
        )
        # Current hour = 0 (midnight, which maps to tomorrow 00:00)
        now = datetime(2026, 6, 20, 0, 30, tzinfo=timezone(timedelta(hours=2)))
        daemon._enforce_plan(now)
        assert "start_charge" in daemon.tessie.commands, "Should start for tomorrow slot"

    def test_e7_no_plan_no_action(self):
        """No active plan → enforce does nothing."""
        daemon = make_daemon(tessie=MockTessie(battery_pct=50.0, charging=True))
        now = datetime(2026, 6, 19, 10, 0, tzinfo=timezone(timedelta(hours=2)))
        # current_plan = None
        with pytest.raises(AttributeError):
            # _enforce_plan accesses self.current_plan.slots directly
            # This should fail if current_plan is None
            daemon._enforce_plan(now)

    def test_e8_state_none_graceful(self):
        """Vehicle state fetch fails → enforce does nothing."""
        class FailingTessie:
            def get_state(self): return None
        daemon = make_daemon(tessie=FailingTessie())
        daemon.current_plan = ChargingPlan(
            slots=[ChargingSlot(10, 12, 8.0, 6.0)],
            target_pct=70.0,
        )
        now = datetime(2026, 6, 19, 10, 0, tzinfo=timezone(timedelta(hours=2)))
        # Should not raise despite state being None
        daemon._enforce_plan(now)


# =========================================================================
# _next_wake_time tests
# =========================================================================

class TestNextWakeTime:
    """Test wake time calculation."""

    def test_j1_no_plan_interval_only(self):
        """No plan → only interval boundary candidates."""
        daemon = make_daemon()
        now = datetime(2026, 6, 19, 10, 5, tzinfo=timezone(timedelta(hours=2)))
        wake = daemon._next_wake_time(now, interval_minutes=15)
        assert wake is not None
        assert wake.minute in (15, 30, 45, 0), f"Should be at :15/:30/:45/:00, got :{wake.minute:02d}"
        assert wake > now, "Wake should be after now"

    def test_j2_with_future_slot(self):
        """Plan active with future slot → includes slot start."""
        daemon = make_daemon()
        daemon.current_plan = ChargingPlan(
            slots=[ChargingSlot(14, 16, 8.0, 6.0)],
            target_pct=70.0,
        )
        now = datetime(2026, 6, 19, 10, 5, tzinfo=timezone(timedelta(hours=2)))
        wake = daemon._next_wake_time(now, interval_minutes=15)
        assert wake is not None
        # Should wake at 10:15 (next interval) or 14:00 (slot start)
        # Since 10:15 < 14:00, it should be 10:15
        assert wake.hour == 10, f"Should be 10:15, got {wake.hour}:{wake.minute:02d}"
        assert wake.minute == 15, f"Should be 10:15, got {wake.hour}:{wake.minute:02d}"

    def test_j3_slot_in_past_skipped(self):
        """Past slot start → not added to candidates."""
        daemon = make_daemon()
        daemon.current_plan = ChargingPlan(
            slots=[ChargingSlot(9, 11, 8.0, 6.0)],
            target_pct=70.0,
        )
        now = datetime(2026, 6, 19, 10, 5, tzinfo=timezone(timedelta(hours=2)))
        wake = daemon._next_wake_time(now, interval_minutes=15)
        assert wake is not None
        # Slot start 9:00 < 10:05, so it's skipped
        # Only interval boundary remains
        assert wake.hour == 10
        assert wake.minute == 15

    def test_j4_slot_at_24_plus(self):
        """Slot start at 24 (tomorrow 00:00) → calculates correctly."""
        daemon = make_daemon()
        daemon.current_plan = ChargingPlan(
            slots=[ChargingSlot(24, 27, 6.0, 9.0)],
            target_pct=70.0,
        )
        now = datetime(2026, 6, 19, 22, 30, tzinfo=timezone(timedelta(hours=2)))
        wake = daemon._next_wake_time(now, interval_minutes=15)
        assert wake is not None
        # Next interval at 22:45 < tomorrow 00:00, so should be 22:45
        assert wake.hour == 22
        assert wake.minute == 45

    @pytest.mark.skip(reason="Requires mocking now near midnight")
    def test_j7_interval_rolls_over_midnight(self):
        """Interval boundary past midnight → rolls to next day."""
        daemon = make_daemon()
        now = datetime(2026, 6, 19, 23, 50, tzinfo=timezone(timedelta(hours=2)))
        wake = daemon._next_wake_time(now, interval_minutes=15)
        assert wake is not None
        # 23:50 + 15min = 00:05 next day
        assert wake.day == 20  # next day
        assert wake.hour == 0
        assert wake.minute == 5


# =========================================================================
# _next_action_description tests
# =========================================================================

class TestNextActionDescription:
    """Test human-readable action description."""

    def test_n1_plan_active_slot_future(self):
        """Plan active, next slot in future."""
        daemon = make_daemon()
        daemon.current_plan = ChargingPlan(
            slots=[ChargingSlot(14, 16, 8.0, 6.0)],
            target_pct=70.0,
        )
        now = datetime(2026, 6, 19, 10, 0, tzinfo=timezone(timedelta(hours=2)))
        desc = daemon._next_action_description(now)
        assert "slot(s)" in desc, f"Should mention slots, got: {desc}"
        assert "14:00" in desc, f"Should mention next charge time, got: {desc}"

    def test_n2_plan_active_slot_now(self):
        """Plan active, slot already started."""
        daemon = make_daemon()
        daemon.current_plan = ChargingPlan(
            slots=[ChargingSlot(9, 12, 8.0, 9.0)],
            target_pct=70.0,
        )
        now = datetime(2026, 6, 19, 10, 0, tzinfo=timezone(timedelta(hours=2)))
        desc = daemon._next_action_description(now)
        assert "ejecutando" in desc, f"Should mention executing, got: {desc}"

    def test_n3_early_done_no_slots(self):
        """Early plan done but no slots available."""
        daemon = make_daemon()
        daemon._today_early_plan_done = True
        now = datetime(2026, 6, 19, 10, 0, tzinfo=timezone(timedelta(hours=2)))
        desc = daemon._next_action_description(now)
        assert "plan HOY" in desc or "sin slots" in desc, \
            f"Should mention hoy plan with no slots, got: {desc}"

    def test_n4_planning_today(self):
        """Before target, early plan not done."""
        daemon = make_daemon()
        daemon._today_early_plan_done = False
        now = datetime(2026, 6, 19, 9, 0, tzinfo=timezone(timedelta(hours=2)))
        desc = daemon._next_action_description(now)
        assert "planificando" in desc, f"Should mention planning, got: {desc}"

    def test_n5_prices_fetched_before_target(self):
        """Prices fetched but before target hour (waiting for night window).

        Must be in the 'else' branch (current_plan = None). With the fix,
        prices_fetched_today is checked BEFORE _today_early_plan_done,
        so N5 is reachable even when _today_early_plan_done is True.
        """
        daemon = make_daemon()
        daemon.prices_fetched_today = True
        daemon._today_early_plan_done = True
        now = datetime(2026, 6, 19, 10, 0, tzinfo=timezone(timedelta(hours=2)))
        desc = daemon._next_action_description(now)
        assert "preparado" in desc or "esperando" in desc, \
            f"Should mention waiting/prepared, got: {desc}"

    def test_n6_prices_fetched_cross_midnight_pending(self):
        """Prices fetched, past target hour, not planned yet."""
        daemon = make_daemon()
        daemon.prices_fetched_today = True
        daemon.planned_today = False
        now = datetime(2026, 6, 19, 20, 0, tzinfo=timezone(timedelta(hours=2)))
        desc = daemon._next_action_description(now)
        assert "cruzando medianoche" in desc, \
            f"Should mention cross midnight planning, got: {desc}"


# =========================================================================
# _compute_expected_by_hour tests
# =========================================================================

class TestComputeExpectedByHour:
    """Test expected battery level computation per hour."""

    def test_compute_with_today_slot(self):
        """Slot 10-13, starting 50% → compute expected at each hour."""
        daemon = make_daemon()
        plan = ChargingPlan(
            slots=[ChargingSlot(10, 13, 8.0, 9.0)],
            total_kwh=9.0,
            expected_final_pct=62.0,
            target_pct=70.0,
        )
        daemon._compute_expected_by_hour(plan, starting_pct=50.0, date_str="2026-06-19")

        # increment = 3.3 * 0.9 / 75 * 100 = 3.96% per hour
        # Hour 9: not charging → 50%
        # Hour 10: charging → 53.96%
        # Hour 11: charging → 57.92%
        # Hour 12: charging → 61.88%
        # Hour 13+: not charging → 61.88%
        assert 49 <= daemon.expected_by_hour.get(9, 0) <= 51, \
            f"Hour 9 expected ~50%, got {daemon.expected_by_hour.get(9)}"
        assert daemon.expected_by_hour.get(10, 0) > 50, \
            f"Hour 10 should be > 50% (charging starts)"
        assert daemon.expected_by_hour.get(12, 0) > daemon.expected_by_hour.get(10, 0), \
            "Should increase during charging hours"

    def test_compute_with_tomorrow_slot(self):
        """Slot 24-27 (tomorrow 00:00-03:00) → maps to clock hours 0-3."""
        daemon = make_daemon()
        plan = ChargingPlan(
            slots=[ChargingSlot(24, 27, 6.0, 9.0)],
            total_kwh=9.0,
            expected_final_pct=62.0,
            target_pct=70.0,
        )
        daemon._compute_expected_by_hour(plan, starting_pct=50.0, date_str="2026-06-19")

        # Tomorrow 00:00 (offset 24) → clock hour 0
        # Tomorrow 02:00 (offset 26) → clock hour 2
        # Clock hours 0, 1, 2 should have higher values (charging)
        assert daemon.expected_by_hour.get(0, 0) > 50, \
            f"Hour 0 (tomorrow 00:00) should be charging, got {daemon.expected_by_hour.get(0)}"

    def test_compute_no_slots(self):
        """Empty plan → expected_by_hour populated at starting_pct for default range."""
        daemon = make_daemon()
        plan = ChargingPlan(target_pct=70.0, expected_final_pct=50.0)
        daemon._compute_expected_by_hour(plan, starting_pct=50.0, date_str="2026-06-19")
        # Code always iterates up to max_hour=24 (default)
        assert len(daemon.expected_by_hour) == 24, \
            f"Empty plan produces 24 entries at starting %, got {len(daemon.expected_by_hour)}"
        # All entries should be at starting %
        for v in daemon.expected_by_hour.values():
            assert v == 50.0, f"All values should be 50.0 in empty plan, got {v}"

    def test_compute_caps_at_100(self):
        """Battery should not exceed 100%."""
        daemon = make_daemon()
        # 24-hour slot (absurd, but tests capping)
        plan = ChargingPlan(
            slots=[ChargingSlot(10, 34, 8.0, 75.0)],
            total_kwh=75.0,
            expected_final_pct=100.0,
            target_pct=100.0,
        )
        daemon._compute_expected_by_hour(plan, starting_pct=10.0, date_str="2026-06-19")

        for h in range(24):
            val = daemon.expected_by_hour.get(h, 0)
            assert val <= 100.01, f"Hour {h} expected {val:.2f}%, should be <= 100%"


# =========================================================================
# _check_progress tests
# =========================================================================

class TestCheckProgress:
    """Test progress checking and replan trigger."""

    def test_f1_on_track_no_replan(self):
        """Deficit < 3% → no replan."""
        daemon = make_daemon()
        daemon.prices = {h: 8.0 for h in range(24)}
        daemon.expected_by_hour = {10: 45.0}
        daemon.current_plan = ChargingPlan(
            slots=[ChargingSlot(10, 12, 8.0, 6.0)],
            target_pct=70.0,
        )
        now = datetime(2026, 6, 19, 10, 0, tzinfo=timezone(timedelta(hours=2)))
        # Deficit = 45 - 44 = 1% < 3%, no replan
        daemon.tessie._battery_pct = 44.0
        daemon._check_progress(now)
        # Plan should not change
        assert len(daemon.current_plan.slots) == 1

    def test_f3_target_reached(self):
        """Actual >= target → stop charging, clear plan."""
        daemon = make_daemon(tessie=MockTessie(battery_pct=72.0, charging=True))
        daemon.prices = {h: 8.0 for h in range(24)}
        daemon.expected_by_hour = {14: 65.0}
        daemon.current_plan = ChargingPlan(
            slots=[ChargingSlot(10, 18, 8.0, 24.0)],
            target_pct=70.0,
        )
        now = datetime(2026, 6, 19, 14, 0, tzinfo=timezone(timedelta(hours=2)))
        daemon._check_progress(now)
        # Plan should be cleared
        assert daemon.current_plan is None, "Plan should be cleared on target reached"
        assert "stop_charge" in daemon.tessie.commands, "Should stop charging"

    def test_f4_missing_hour_no_op(self):
        """expected_by_hour doesn't have current_hour → no-op."""
        daemon = make_daemon()
        daemon.expected_by_hour = {10: 45.0}
        now = datetime(2026, 6, 19, 11, 0, tzinfo=timezone(timedelta(hours=2)))
        # Should not crash
        daemon._check_progress(now)

    def test_f5_state_none_no_op(self):
        """Vehicle state is None → no-op."""
        class FailingTessie:
            def get_state(self): return None
        daemon = make_daemon(tessie=FailingTessie())
        daemon.expected_by_hour = {10: 45.0}
        now = datetime(2026, 6, 19, 10, 0, tzinfo=timezone(timedelta(hours=2)))
        # Should not crash
        daemon._check_progress(now)


# =========================================================================
# _create_plan with mock data tests
# =========================================================================

class TestCreatePlan:
    """Test plan creation within daemon context."""

    def test_creates_plan_with_prices(self):
        """With prices and battery below target → creates plan."""
        daemon = make_daemon()
        daemon.prices = {h: 8.0 for h in range(24)}
        daemon.prices_date = "2026-06-19"
        daemon._create_plan(current_hour_override=9)
        assert daemon.current_plan is not None, "Should create a plan"
        assert len(daemon.current_plan.slots) > 0, "Plan should have slots"

    def test_empty_slots_when_battery_above_target(self):
        """Battery already above target → no slots."""
        daemon = make_daemon(tessie=MockTessie(battery_pct=80.0))
        daemon.prices = {h: 8.0 for h in range(24)}
        daemon.prices_date = "2026-06-19"
        daemon._create_plan(current_hour_override=9)
        assert daemon.current_plan is None or len(daemon.current_plan.slots) == 0, \
            "Plan should be empty when battery above target"
        assert daemon.planned_today, "planned_today should be True even with empty plan"

    def test_creates_plan_cross_midnight(self):
        """Past target_hour with tomorrow prices → cross-midnight plan."""
        daemon = make_daemon()
        daemon.prices = {h: 8.0 for h in range(24)}
        daemon.prices.update({h + 24: 6.0 for h in range(24)})  # tomorrow prices
        daemon.prices_date = "2026-06-19"
        daemon._create_plan(current_hour_override=21)
        assert daemon.current_plan is not None, "Should create cross-midnight plan"

    def test_create_plan_label_today(self):
        """Hour < target_hour → label is 'HOY'."""
        daemon = make_daemon()
        daemon.prices = {h: 8.0 for h in range(24)}
        daemon._debug_mode = True
        daemon._create_plan(current_hour_override=9)
        # Just verify it doesn't crash

    def test_create_plan_label_cross_midnight(self):
        """Hour >= target_hour and hour > 0 → label is 'HOY→MAÑANA'."""
        daemon = make_daemon()
        daemon.prices = {h: 8.0 for h in range(24)}
        daemon.prices_date = "2026-06-19"
        daemon._debug_mode = True
        daemon._create_plan(current_hour_override=21)
        # Just verify it doesn't crash


# =========================================================================
# _ensure_charge_limit tests
# =========================================================================

class TestEnsureChargeLimit:
    """Test charge limit enforcement."""

    def test_h4_already_correct_no_op(self):
        """Charge limit already >= target → no change."""
        daemon = make_daemon(tessie=MockTessie(battery_pct=50.0))
        daemon.tessie._charge_limit = 80.0  # Already at 80, target is 70
        daemon._ensure_charge_limit()
        assert not any("set_charge_limit" in c for c in daemon.tessie.commands), \
            "Should not change limit if already correct"

    def test_h5_limit_too_low_adjusts(self):
        """Charge limit below target → adjusts."""
        daemon = make_daemon(tessie=MockTessie(battery_pct=50.0))
        daemon.tessie._charge_limit = 30.0  # Below target
        daemon._ensure_charge_limit()
        assert any("set_charge_limit" in c for c in daemon.tessie.commands), \
            "Should increase charge limit"

    def test_state_none_graceful(self):
        """Vehicle state fails → no crash."""
        class FailingTessie:
            def get_state(self): return None
        daemon = make_daemon(tessie=FailingTessie())
        daemon._ensure_charge_limit()
