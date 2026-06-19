"""Comprehensive tests for Tesla-PVPC planning system.

Covers all scenarios described in CASUISTICAS.md.
Tests are organized by scenario ID (e.g., A1, B2, etc.).
"""

import os
import sys
import math
from typing import Dict

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest
from auto_charge.planner import ChargePlanner, ChargingPlan, ChargingSlot, _MISSING_PRICE_SENTINEL
from auto_charge.daemon import AutoChargeDaemon

# =============================================================================
# Test fixture: mock config with known values
# =============================================================================


class MockConfig:
    """Simplified config for testing.
    
    Defaults match typical config.json values.
    """
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

    @property
    def target_hour(self) -> int:
        return int(self.target_time.split(":")[0])

    @property
    def target_minute(self) -> int:
        return int(self.target_time.split(":")[1])

    def __getattr__(self, name: str):
        # Fallback for any property not explicitly defined
        raise AttributeError(f"MockConfig has no attribute '{name}'")


@pytest.fixture
def cfg():
    """Default config for most tests."""
    return MockConfig()


@pytest.fixture
def flex_cfg():
    """Flexible mode config."""
    return MockConfig(strict_mode=False)


def make_planner(cfg_override=None):
    """Create a ChargePlanner with config."""
    cfg = cfg_override or MockConfig()
    return ChargePlanner(cfg)


def cheap_prices(base=8.0, count=24):
    """Generate 24h of cheap prices (all below default max_price=10)."""
    return {h: base + (h % 3) for h in range(count)}


def mixed_prices(cheap_range=range(0, 24), cheap_price=8.0, expensive_price=25.0):
    """Generate 24h with specific cheap/expensive hours."""
    p = {}
    for h in range(24):
        p[h] = cheap_price if h in cheap_range else expensive_price
    return p


def expensive_prices(base=20.0, count=24):
    """Generate 24h of expensive prices (all above default max_price=10)."""
    return {h: base + (h % 5) for h in range(count)}


def sentinel_prices(count=24, sentinel=_MISSING_PRICE_SENTINEL):
    """Generate hours with sentinel (missing) prices."""
    return {h: sentinel for h in range(count)}


def cross_midnight_prices(today_price=8.0, tomorrow_price=7.0, count_tomorrow=24):
    """Generate today (0-23) + tomorrow (24-47) prices."""
    p = cheap_prices(base=today_price)
    for h in range(count_tomorrow):
        p[h + 24] = tomorrow_price + (h % 3)
    return p


# =========================================================================
# SECTION A: Intradía (current_hour < target_hour)
# =========================================================================


class TestIntradia:
    """All scenarios where current_hour < target_hour (within same day)."""

    # --- A1: Normal — sobran horas baratas ---
    def test_a1_normal_sufficient_cheap_hours(self, cfg):
        """A1: 09:00, target 19:00, 60%→70%, cheap prices, all below max_price."""
        prices = cheap_prices(base=8.0)
        planner = make_planner(cfg)
        plan = planner.plan(prices, current_battery_pct=60.0, current_hour=9, date_str="2026-06-19")

        assert len(plan.slots) > 0, "A1: Should have at least 1 slot"
        assert plan.will_reach_target, f"A1: Should reach target ({plan.expected_final_pct:.1f}% vs {plan.target_pct:.0f}%)"
        assert plan.total_kwh > 0, "A1: Should deliver some kWh"
        assert plan.total_cost_eur > 0, "A1: Should have non-zero cost"

    # --- A2: Horas justas — no cabe en ventana ---
    def test_a2_tight_window(self, cfg):
        """A2: 17:00, target 19:00, 50%→70%, only 2h available."""
        prices = cheap_prices(base=8.0)
        planner = make_planner(cfg)
        plan = planner.plan(prices, current_battery_pct=50.0, current_hour=17, date_str="2026-06-19")

        assert len(plan.slots) <= 2, f"A2: At most 2 slots, got {len(plan.slots)}"
        assert not plan.will_reach_target, "A2: Should NOT reach target (not enough hours)"

    # --- A3: Batería ya en target ---
    def test_a3_battery_at_target(self, cfg):
        """A3: 75% battery, target 70% → no charging needed."""
        prices = cheap_prices(base=8.0)
        planner = make_planner(cfg)
        plan = planner.plan(prices, current_battery_pct=75.0, current_hour=9, date_str="2026-06-19")

        assert len(plan.slots) == 0, "A3: Should have no slots"
        assert plan.expected_final_pct >= 75.0, f"A3: Expected final >= 75%"

    # --- A4: Todos los precios caros ---
    def test_a4a_all_expensive_strict(self, cfg):
        """A4: All expensive, strict mode → includes expensive hours."""
        prices = expensive_prices(base=20.0)
        planner = make_planner(cfg)  # strict=True by default
        plan = planner.plan(prices, current_battery_pct=50.0, current_hour=9, date_str="2026-06-19")

        assert len(plan.slots) > 0, "A4a: Strict should still create a plan"
        assert plan.will_reach_target, "A4a: Strict should reach target even with expensive hours"

    def test_a4b_all_expensive_flexible(self, flex_cfg):
        """A4: All expensive, flexible mode → empty plan."""
        prices = expensive_prices(base=20.0)
        planner = make_planner(flex_cfg)
        plan = planner.plan(prices, current_battery_pct=50.0, current_hour=9, date_str="2026-06-19")

        assert len(plan.slots) == 0, "A4b: Flexible with all expensive should have no slots"

    # --- A5: Solo 1h disponible ---
    def test_a5_single_hour_available(self, cfg):
        """A5: 18:00, target 19:00, only [18] available."""
        prices = cheap_prices(base=8.0)
        planner = make_planner(cfg)
        plan = planner.plan(prices, current_battery_pct=50.0, current_hour=18, date_str="2026-06-19")

        assert len(plan.slots) == 1, f"A5: Should have exactly 1 slot, got {len(plan.slots)}"
        assert not plan.will_reach_target, "A5: Should NOT reach target (only 1h)"

    # --- A6: Ventana vacía ---
    def test_a6_boundary_equal(self, cfg):
        """A6: 19:00, target 19:00 → current_hour == target_hour → enters cross-midnight mode.

        When current_hour == target_hour, the 'intradia' branch gives an empty range,
        but the 'else' (cross-midnight) branch applies.
        Without tomorrow prices, the truncation guard reduces to [19..23] = 5h.
        5h at 3.3kW * 0.9 = 14.85 kWh → ~19.8% extra. 50% + 19.8% = 69.8% < 70% target.
        """
        prices = cheap_prices(base=8.0)  # Only 0-23, no tomorrow prices
        planner = make_planner(cfg)
        plan = planner.plan(prices, current_battery_pct=50.0, current_hour=19, date_str="2026-06-19")

        # Should produce a plan (cross-midnight window, truncated to today)
        assert len(plan.slots) > 0, "A6: Should have slots from truncated cross-midnight window"
        # All slots should be within today (truncation guard)
        for slot in plan.slots:
            assert slot.end_hour <= 24, f"A6: Slot end {slot.end_hour} should be <= 24 (truncated)"
        # 5h is not enough to go from 50% to 70%
        assert not plan.will_reach_target, "A6: 5h window insufficient to reach 70% target"

    # --- A7: Agrupación no consecutiva ---
    def test_a7_non_consecutive_slots(self, cfg):
        """A7: Cheap hours at 10, 12, 14, 15, 16, 18 (not all consecutive)."""
        prices = mixed_prices(cheap_range={10, 12, 14, 15, 16, 18}, cheap_price=8.0, expensive_price=25.0)
        planner = make_planner(MockConfig(strict_mode=False, max_price_cents_per_kwh=15))
        plan = planner.plan(prices, current_battery_pct=50.0, current_hour=9, date_str="2026-06-19")

        # Multiple slots expected (not all consecutive)
        # 10 isolated, 12 isolated, 14-15-16 consecutive, 18 isolated = 4 groups
        assert len(plan.slots) >= 2, f"A7: Should have multiple slots, got {len(plan.slots)}"

    # --- A8: Slot único consecutivo ---
    def test_a8_consecutive_slot(self, cfg):
        """A8: 6 consecutive cheap hours → single slot."""
        prices = mixed_prices(cheap_range=set(range(10, 16)), cheap_price=7.5, expensive_price=25.0)
        planner = make_planner(MockConfig(strict_mode=False, max_price_cents_per_kwh=15))
        plan = planner.plan(prices, current_battery_pct=50.0, current_hour=9, date_str="2026-06-19")

        assert len(plan.slots) == 1, f"A8: Should have exactly 1 slot, got {len(plan.slots)}"
        assert plan.slots[0].start_hour == 10, f"A8: Slot should start at 10, got {plan.slots[0].start_hour}"
        assert plan.slots[0].end_hour == 16, f"A8: Slot should end at 16, got {plan.slots[0].end_hour}"


# =========================================================================
# SECTION B: Cross-midnight (current_hour >= target_hour)
# =========================================================================


class TestCrossMidnight:
    """All scenarios where current_hour >= target_hour (wrap past midnight)."""

    # --- B1: Cross-midnight con precios de mañana ---
    def test_b1_cross_midnight_with_tomorrow_prices(self, cfg):
        """B1: 21:00, target 19:00, 35%→70%, today+tomo prices merged."""
        prices = cross_midnight_prices(today_price=8.0, tomorrow_price=6.0)
        planner = make_planner(cfg)
        plan = planner.plan(prices, current_battery_pct=35.0, current_hour=21, date_str="2026-06-19")

        assert len(plan.slots) > 0, "B1: Should have slots"
        # Check that at least one slot uses 24+ hours (tomorrow)
        has_tomorrow = any(s.start_hour >= 24 or s.end_hour > 24 for s in plan.slots)
        assert has_tomorrow, "B1: Should include tomorrow hours (24+)"

    # --- B2: Cross-midnight SIN precios de mañana (truncation guard) ---
    def test_b2_cross_midnight_no_tomorrow_prices(self, cfg):
        """B2: 21:00, target 19:00, 35%→70%, only today prices → truncated."""
        prices = cheap_prices(base=8.0)  # Only 0-23, no tomorrow prices
        planner = make_planner(cfg)
        plan = planner.plan(prices, current_battery_pct=35.0, current_hour=21, date_str="2026-06-19")

        assert len(plan.slots) > 0, "B2: Should have slots (truncated to today)"
        # All slot hours should be < 24 (today only)
        for slot in plan.slots:
            assert slot.start_hour < 24, f"B2: Start hour {slot.start_hour} should be < 24 (today only)"
            assert slot.end_hour <= 24, f"B2: End hour {slot.end_hour} should be <= 24 (today only)"
        assert not plan.will_reach_target, "B2: Should NOT reach target (only 3h available)"

    # --- B3: Cross-midnight con batería muy baja ---
    def test_b3_cross_midnight_very_low_battery(self, cfg):
        """B3: 22:00, target 08:00, 10%→80%, very low battery in tight window."""
        prices = cross_midnight_prices(today_price=8.0, tomorrow_price=6.0)
        planner = make_planner(cfg)
        plan = planner.plan(prices, current_battery_pct=10.0, current_hour=22, date_str="2026-06-19")

        assert len(plan.slots) > 0, "B3: Should have some slots"
        # With 10h window and needing ~22h, should NOT reach target
        # (flexible mode kicks in since strict can't add expensive hours beyond window)

    # --- B4: Cross-midnight con current_hour == 23 ---
    def test_b4_cross_midnight_hour_23(self, cfg):
        """B4: 23:00, target 07:00 → window [23] + [24..30]."""
        prices = cross_midnight_prices(today_price=8.0, tomorrow_price=6.0)
        planner = make_planner(cfg)
        plan = planner.plan(prices, current_battery_pct=60.0, current_hour=23, date_str="2026-06-19")

        assert len(plan.slots) > 0, "B4: Should have slots"

    # --- B5: Cross-midnight sin mañana, hour 23 ---
    def test_b5_cross_midnight_hour_23_no_tomorrow(self, cfg):
        """B5: 23:00, target 07:00, only today prices → truncated to [23]."""
        prices = cheap_prices(base=8.0)  # Only 0-23
        planner = make_planner(cfg)
        plan = planner.plan(prices, current_battery_pct=60.0, current_hour=23, date_str="2026-06-19")

        assert len(plan.slots) == 1, f"B5: Should have exactly 1 slot, got {len(plan.slots)}"
        assert plan.slots[0].start_hour == 23, f"B5: Slot should start at 23, got {plan.slots[0].start_hour}"
        assert plan.slots[0].end_hour == 24, f"B5: Slot should end at 24, got {plan.slots[0].end_hour}"


# =========================================================================
# SECTION C: Precios — Casos Especiales
# =========================================================================


class TestSpecialPrices:
    """Edge cases with price data."""

    def test_c1_all_sentinel_prices(self, cfg):
        """C1: All prices are sentinel → empty plan."""
        prices = sentinel_prices()
        planner = make_planner(cfg)
        plan = planner.plan(prices, current_battery_pct=50.0, current_hour=9, date_str="2026-06-19")

        assert len(plan.slots) == 0, "C1: All sentinel should produce empty plan"

    def test_c2_mixed_sentinel_and_real(self, cfg):
        """C2: Some sentinel, some real - should use only real."""
        prices = {h: 8.0 if h < 6 else _MISSING_PRICE_SENTINEL for h in range(24)}
        planner = make_planner(MockConfig(strict_mode=False, max_price_cents_per_kwh=15))
        plan = planner.plan(prices, current_battery_pct=50.0, current_hour=9, date_str="2026-06-19")

        # Hours 9-18 should all be sentinel
        # With window [9..18] and all sentinel, real_prices check should fail
        assert len(plan.slots) == 0, "C2: No real prices in window → empty plan"

    def test_c3_restrictive_max_price(self, cfg):
        """C3: max_price=5, all prices >= 7 → strict includes expensive."""
        prices = cheap_prices(base=7.0)
        planner = make_planner(MockConfig(max_price_cents_per_kwh=5.0))
        plan = planner.plan(prices, current_battery_pct=50.0, current_hour=9, date_str="2026-06-19")

        assert len(plan.slots) > 0, "C3: Strict should still create plan"
        assert plan.will_reach_target, "C3: Strict should reach target"

    def test_c4_permissive_max_price(self, cfg):
        """C4: max_price=100, all prices <= 15 → all hours cheap."""
        prices = cheap_prices(base=8.0)
        planner = make_planner(MockConfig(max_price_cents_per_kwh=100.0))
        plan = planner.plan(prices, current_battery_pct=50.0, current_hour=9, date_str="2026-06-19")

        assert len(plan.slots) > 0, "C4: Should have slots"
        # All hours should be in cheap_hours (prices[9..18] all < 100)


# =========================================================================
# SECTION D: Display y formato de horas
# =========================================================================


class TestHourLabel:
    """Test _hour_label static method."""

    def test_d1_hour_label_today(self):
        """D1: Hours 0-23 → HH:00 format."""
        assert ChargingSlot._hour_label(0) == "00:00"
        assert ChargingSlot._hour_label(9) == "09:00"
        assert ChargingSlot._hour_label(12) == "12:00"
        assert ChargingSlot._hour_label(23) == "23:00"

    def test_d2_hour_label_tomorrow(self):
        """D2: Hours 24+ → +1d HH:00 format."""
        assert ChargingSlot._hour_label(24) == "+1d 00:00"
        assert ChargingSlot._hour_label(25) == "+1d 01:00"
        assert ChargingSlot._hour_label(47) == "+1d 23:00"
        assert ChargingSlot._hour_label(48) == "+2d 00:00"  # Day after tomorrow

    def test_d3_slot_repr_today(self):
        """Slot __repr__ with today hours."""
        slot = ChargingSlot(start_hour=10, end_hour=13, price_cents_per_kwh=8.5, kwh_to_deliver=3.0)
        r = repr(slot)
        assert "10:00" in r, f"Should contain 10:00, got: {r}"
        assert "13:00" in r, f"Should contain 13:00, got: {r}"
        assert "8.5" in r, f"Should contain 8.5, got: {r}"

    def test_d4_slot_repr_tomorrow(self):
        """Slot __repr__ with tomorrow hours (24+)."""
        slot = ChargingSlot(start_hour=24, end_hour=27, price_cents_per_kwh=6.5, kwh_to_deliver=3.0)
        r = repr(slot)
        assert "+1d 00:00" in r, f"Should contain +1d 00:00, got: {r}"
        assert "+1d 03:00" in r, f"Should contain +1d 03:00, got: {r}"


# =========================================================================
# SECTION E: _slot_covers_hour — Ejecución del plan
# =========================================================================


class TestSlotCoversHour:
    """Test AutoChargeDaemon._slot_covers_hour static method."""

    def test_e1_today_slot_during(self):
        """E1a: Slot today, current_hour inside → True."""
        slot = ChargingSlot(start_hour=9, end_hour=12, price_cents_per_kwh=8.0, kwh_to_deliver=9.0)
        assert AutoChargeDaemon._slot_covers_hour(slot, 10) is True

    def test_e1_today_slot_before(self):
        """E1b: Slot today, current_hour before start → False."""
        slot = ChargingSlot(start_hour=9, end_hour=12, price_cents_per_kwh=8.0, kwh_to_deliver=9.0)
        assert AutoChargeDaemon._slot_covers_hour(slot, 8) is False

    def test_e1_today_slot_after(self):
        """E1c: Slot today, current_hour at end (exclusive) → False."""
        slot = ChargingSlot(start_hour=9, end_hour=12, price_cents_per_kwh=8.0, kwh_to_deliver=9.0)
        assert AutoChargeDaemon._slot_covers_hour(slot, 12) is False

    def test_e2_tomorrow_slot_during(self):
        """E2a: Slot tomorrow (24+), current_hour=0 (tomorrow 00:00) → True."""
        slot = ChargingSlot(start_hour=24, end_hour=27, price_cents_per_kwh=6.0, kwh_to_deliver=9.0)
        assert AutoChargeDaemon._slot_covers_hour(slot, 0) is True

    def test_e2_tomorrow_slot_later(self):
        """E2b: Slot tomorrow (24-27), current_hour=2 (adjusted=26) → True."""
        slot = ChargingSlot(start_hour=24, end_hour=27, price_cents_per_kwh=6.0, kwh_to_deliver=9.0)
        assert AutoChargeDaemon._slot_covers_hour(slot, 2) is True

    def test_e2_tomorrow_slot_past_end(self):
        """E2c: Slot tomorrow (24-27), current_hour=3 (adjusted=27) → False."""
        slot = ChargingSlot(start_hour=24, end_hour=27, price_cents_per_kwh=6.0, kwh_to_deliver=9.0)
        assert AutoChargeDaemon._slot_covers_hour(slot, 3) is False

    def test_e3_midnight_cross_slot(self):
        """E3: Slot 21-24 covers today hours 21, 22, 23."""
        slot = ChargingSlot(start_hour=21, end_hour=24, price_cents_per_kwh=8.0, kwh_to_deliver=9.0)
        assert AutoChargeDaemon._slot_covers_hour(slot, 21) is True
        assert AutoChargeDaemon._slot_covers_hour(slot, 23) is True
        assert AutoChargeDaemon._slot_covers_hour(slot, 0) is False  # Next day


# =========================================================================
# SECTION F: Progreso y Replan
# =========================================================================


class TestReplan:
    """Test ChargePlanner.replan() logic."""

    def test_f1_on_track(self, cfg):
        """F1: On track, deficit < 2% → no replan."""
        prices = cheap_prices(base=8.0)
        planner = make_planner(cfg)
        result = planner.replan(
            prices=prices,
            current_battery_pct=44.0,
            current_hour=10,
            date_str="2026-06-19",
            expected_pct=45.0,  # deficit = 1.0%
        )
        assert result is None, "F1: On track should return None"

    def test_f2_behind_schedule(self, cfg):
        """F2: Behind schedule, deficit > 2% → replan."""
        prices = cheap_prices(base=8.0)
        planner = make_planner(cfg)
        result = planner.replan(
            prices=prices,
            current_battery_pct=55.0,
            current_hour=14,
            date_str="2026-06-19",
            expected_pct=65.0,  # deficit = 10.0%
        )
        assert result is not None, "F2: Behind schedule should return a new plan"
        assert isinstance(result, ChargingPlan), "F2: Result should be ChargingPlan"


# =========================================================================
# SECTION G: Daemon máquina de estados (test del planner indirectamente)
# =========================================================================


class TestDaemonState:
    """Test daemon state machine indirectly through planner behavior."""

    def test_g1_early_plan_before_target(self, cfg):
        """G1: Early plan (current_hour < target_hour) → within today."""
        prices = cheap_prices(base=8.0)
        planner = make_planner(cfg)
        plan = planner.plan(prices, current_battery_pct=60.0, current_hour=9, date_str="2026-06-19")

        assert len(plan.slots) > 0, "G1: Should have slots"
        # All slots should be in today's hours (end <= target_hour or close)
        for slot in plan.slots:
            assert slot.start_hour < 24, f"G1: Start {slot.start_hour} should be < 24"

    def test_g3_cross_midnight_plan(self, cfg):
        """G3: Cross-midnight plan (current_hour >= target_hour) → may include 24+."""
        prices = cross_midnight_prices()
        planner = make_planner(cfg)
        plan = planner.plan(prices, current_battery_pct=35.0, current_hour=21, date_str="2026-06-19")

        assert len(plan.slots) > 0, "G3: Should have slots"

    def test_g5_day_change_behavior(self, cfg):
        """G5: Simulate behavior when day changes (plan resets).
        
        This tests that a plan created at 23:00 on day 1 would differ from
        a plan created at 09:00 on day 2 with same battery level.
        """
        prices = cheap_prices(base=8.0)
        planner = make_planner(cfg)

        # Plan on "day 1" at 23:00 (late night)
        plan1 = planner.plan(prices, current_battery_pct=60.0, current_hour=23, date_str="2026-06-19")

        # Plan on "day 2" at 09:00 (next morning)
        plan2 = planner.plan(prices, current_battery_pct=60.0, current_hour=9, date_str="2026-06-20")

        # Both are within target (19:00), so both are intradia
        # Day 1 at 23:00 → cross-midnight (23 >= 19)
        # Day 2 at 09:00 → intradia (9 < 19)
        if plan1.slots and plan2.slots:
            assert plan1.expected_final_pct != plan2.expected_final_pct or \
                   len(plan1.slots) != len(plan2.slots), \
                   "G5: Plans at different times should differ"


# =========================================================================
# Integration: End-to-end scenarios
# =========================================================================


class TestIntegration:
    """Full scenario tests combining multiple aspects."""

    def test_scenario_low_battery_midday_strict(self, cfg):
        """Low battery (35%) at midday (12:00), strict mode, target 19:00.
        
        Expected: Uses ALL available cheap hours to maximize charge.
        """
        prices = cheap_prices(base=8.0)
        planner = make_planner(cfg)
        plan = planner.plan(prices, current_battery_pct=35.0, current_hour=12, date_str="2026-06-19")

        assert len(plan.slots) > 0, "Should have slots"
        # Window is 12-18 = 7 hours ≈ 20.8 kWh ≈ 27.7% → 35+27.7 = 62.7%
        # Expect will_reach_target=False (can't reach 70%)
        assert not plan.will_reach_target, "Can't reach 70% with only 7h"

    def test_scenario_low_battery_early_morning(self, cfg):
        """Low battery (35%) at 07:00, target 19:00.
        
        Has 12 hours → can reach 70%.
        """
        prices = cheap_prices(base=8.0)
        planner = make_planner(MockConfig(target_time="19:00"))
        plan = planner.plan(prices, current_battery_pct=35.0, current_hour=7, date_str="2026-06-19")

        assert len(plan.slots) > 0, "Should have slots"
        # 12 hours * 3.3kW * 0.9 = 35.6 kWh → 35% + 47.5% = 82.5%
        assert plan.will_reach_target, "Should reach target with 12h available"

    def test_scenario_nearly_full_battery(self, cfg):
        """Battery at 95%, target 70% → no charging needed."""
        prices = cheap_prices(base=8.0)
        planner = make_planner(cfg)
        plan = planner.plan(prices, current_battery_pct=95.0, current_hour=9, date_str="2026-06-19")

        assert len(plan.slots) == 0, "No slots needed"
        assert plan.expected_final_pct >= 95.0, "Should stay at 95%"

    def test_scenario_strict_vs_flexible_cost(self):
        """Strict mode costs more than flexible mode (same conditions)."""
        prices = mixed_prices(cheap_range=set(range(9, 14)), cheap_price=8.0, expensive_price=25.0)

        strict_planner = make_planner(MockConfig(strict_mode=True))
        flex_planner = make_planner(MockConfig(strict_mode=False))

        strict_plan = strict_planner.plan(prices, current_battery_pct=50.0, current_hour=9, date_str="2026-06-19")
        flex_plan = flex_planner.plan(prices, current_battery_pct=50.0, current_hour=9, date_str="2026-06-19")

        # Strict should deliver more kWh (uses expensive hours)
        assert strict_plan.total_kwh >= flex_plan.total_kwh, \
            f"Strict ({strict_plan.total_kwh:.1f}) >= Flexible ({flex_plan.total_kwh:.1f})"

    def test_scenario_cross_midnight_full_prices_vs_no_tomorrow(self):
        """Cross-midnight with tomorrow prices costs less than without."""
        cfg = MockConfig(strict_mode=False, max_price_cents_per_kwh=15)
        planner = make_planner(cfg)

        only_today = cheap_prices(base=8.0)  # 0-23 only
        full_prices = cross_midnight_prices(today_price=8.0, tomorrow_price=6.0)

        plan_no_tomo = planner.plan(only_today, current_battery_pct=60.0, current_hour=21, date_str="2026-06-19")
        plan_with_tomo = planner.plan(full_prices, current_battery_pct=60.0, current_hour=21, date_str="2026-06-19")

        # Verify both produce plans (truncation vs full cross-midnight)
        if plan_no_tomo.slots and plan_with_tomo.slots:
            # With tomorrow prices, should have more energy delivered
            assert plan_with_tomo.total_kwh >= plan_no_tomo.total_kwh, \
                "With tomorrow prices should deliver >= kWh than without"


# =========================================================================
# Makefile helpers
# =========================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
