"""Charging planner: optimise charging schedule based on electricity prices."""

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from auto_charge.config import Config
from auto_charge.utils import logger, now_spain


@dataclass
class ChargingSlot:
    """A scheduled charging time block."""

    start_hour: int  # Spanish local hour (0-23)
    end_hour: int  # Exclusive end hour (e.g., 8 means 07:00-08:00)
    price_cents_per_kwh: float  # Average price for this slot
    kwh_to_deliver: float  # kWh expected during this slot

    @property
    def duration_hours(self) -> float:
        return float(self.end_hour - self.start_hour)

    @staticmethod
    def _hour_label(h: int) -> str:
        day = h // 24
        hour = h % 24
        if day == 0:
            return f"{hour:02d}:00"
        return f"+{day}d {hour:02d}:00"

    def __repr__(self) -> str:
        return (
            f"Slot({self._hour_label(self.start_hour)}-"
            f"{self._hour_label(self.end_hour)}, "
            f"{self.price_cents_per_kwh:.1f}c/kWh, {self.kwh_to_deliver:.1f}kWh)"
        )


@dataclass
class ChargingPlan:
    """Complete charging plan with slots and summary."""

    slots: List[ChargingSlot] = field(default_factory=list)
    total_kwh: float = 0.0
    total_cost_eur: float = 0.0
    expected_final_pct: float = 0.0
    target_pct: float = 0.0
    flexible: bool = False

    @property
    def will_reach_target(self) -> bool:
        return self.expected_final_pct >= self.target_pct

    def summary(self) -> str:
        lines = [
            f"⚡ Plan: {len(self.slots)} slot(s) → target {self.target_pct:.0f}%", 
        ]
        for s in self.slots:
            lines.append(f"  {s}")
        lines.append(
            f"  Total: {self.total_kwh:.1f} kWh → ~{self.expected_final_pct:.1f}% "
            f"(cost: {self.total_cost_eur:.3f} €)"
        )
        if not self.will_reach_target:
            lines.append(f"  ⚠️ Flexible mode: may not reach target ({self.target_pct:.0f}%)")
        return "\n".join(lines)


_MISSING_PRICE_SENTINEL = 500.0  # Very high price for missing data points


class ChargePlanner:
    """Computes the optimal charging schedule given hourly electricity prices."""

    def __init__(self, config: Config):
        self.cfg = config

    def plan(
        self,
        prices: Dict[int, float],
        current_battery_pct: float,
        current_hour: int,
        date_str: str,
    ) -> ChargingPlan:
        """
        Create an optimal charging plan.

        Args:
            prices: {hour (0-23 Spanish): price_cents_per_kWh}
            current_battery_pct: current battery level (0-100)
            current_hour: current Spanish hour (0-23)
            date_str: YYYY-MM-DD for the target day
        """
        target_pct = self.cfg.min_battery_pct
        capacity_kwh = self.cfg.battery_capacity_kwh
        power_kw = self.cfg.max_charger_power_kw
        max_price = self.cfg.max_price_cents_per_kwh
        target_hour = self.cfg.target_hour
        strict = self.cfg.strict_mode

        # kWh needed (account for charging efficiency)
        efficiency = self.cfg.charging_efficiency
        kwh_needed = max(0.0, (target_pct - current_battery_pct) / 100.0 * capacity_kwh)
        hours_needed = math.ceil(kwh_needed / (power_kw * efficiency))

        logger.info(
            f"Current: {current_battery_pct:.1f}%, Target: {target_pct:.0f}% by {self.cfg.target_time}, "
            f"Need: {kwh_needed:.1f} kWh (~{hours_needed}h at {power_kw}kW)"
        )

        if kwh_needed <= 0:
            logger.info("Battery already at or above target! No charging needed.")
            plan = ChargingPlan(
                target_pct=target_pct,
                expected_final_pct=current_battery_pct,
                flexible=not strict,
            )
            return plan

        # Available hours: from current hour through target_hour-1
        # We include current_hour because it has not yet fully elapsed
        if current_hour < target_hour:
            # All within the same day
            available_window = list(range(current_hour, target_hour))
        else:
            # Wraps past midnight: today's remaining + tomorrow's early hours
            # Tomorrow's hours use offset 24 (so 00:00 mañana = 24, 01:00 = 25, etc.)
            available_window = list(range(current_hour, 24)) + list(range(24, 24 + target_hour))
            logger.info(
                f"🌙 Planning window wraps past midnight: "
                f"{current_hour}:00 → 00:00 (+{24 - current_hour}h) → "
                f"{self.cfg.target_time} (+{target_hour}h)"
            )

        if not available_window:
            logger.warning("No time left before target! Cannot create a plan.")
            return ChargingPlan(target_pct=target_pct, expected_final_pct=current_battery_pct)

        # Cross-midnight check: if tomorrow prices are all sentinel, truncate to today
        if current_hour >= target_hour:
            tomorrow_window = [h for h in available_window if h >= 24]
            real_tomorrow = [
                h for h in tomorrow_window
                if h in prices and prices[h] < _MISSING_PRICE_SENTINEL / 2
            ]
            if not real_tomorrow and tomorrow_window:
                logger.warning(
                    "🌙 Cross-midnight window detected but NO real tomorrow prices available. "
                    f"Truncating plan to today's remaining hours ({current_hour}:00-23:00). "
                    "Will re-plan when tomorrow prices are published."
                )
                available_window = list(range(current_hour, 24))
                if not available_window:
                    logger.warning("No hours left today either. Cannot create a plan.")
                    return ChargingPlan(target_pct=target_pct, expected_final_pct=current_battery_pct)

        # Check if we have real price data (not just the sentinel)
        real_prices = [prices.get(h) for h in available_window if h in prices and prices[h] < _MISSING_PRICE_SENTINEL / 2]
        if not real_prices:
            logger.warning(
                f"No real price data available for planning window. "
                "Skipping plan to avoid using absurd sentinel prices."
            )
            return ChargingPlan(target_pct=target_pct, expected_final_pct=current_battery_pct)

        # Filter and sort: prefer cheapest hours below max_price
        cheap_hours: List[Tuple[int, float]] = []
        expensive_hours: List[Tuple[int, float]] = []

        for h in available_window:
            price = prices.get(h, _MISSING_PRICE_SENTINEL)  # Missing data → treat as very expensive
            if price <= max_price:
                cheap_hours.append((h, price))
            else:
                expensive_hours.append((h, price))

        cheap_hours.sort(key=lambda x: x[1])
        expensive_hours.sort(key=lambda x: x[1])

        # Build the schedule
        # Pick the cheapest hours needed (limit to hours_needed, not all cheap hours)
        selected_hours: List[int] = []
        needed_from_cheap = min(hours_needed, len(cheap_hours))
        selected_hours.extend(h for h, _ in cheap_hours[:needed_from_cheap])

        if strict and len(selected_hours) < hours_needed:
            # Strict mode: add more expensive hours to meet the target
            remaining = hours_needed - len(selected_hours)
            selected_hours.extend(h for h, _ in expensive_hours[:remaining])
            logger.info(f"Strict mode: including {remaining} expensive hours to meet target.")
        elif not strict:
            # Flexible mode: only use cheap hours even if target may not be reached
            logger.info("Flexible mode: will only use hours below max price.")

        # Sort selected hours chronologically
        selected_hours.sort()

        # Group consecutive hours into slots (cap kWh to what's actually needed)
        slots = self._group_into_slots(selected_hours, prices, power_kw, kwh_needed)

        # Calculate summary
        total_kwh = sum(s.kwh_to_deliver for s in slots)
        total_cost = sum(s.kwh_to_deliver * s.price_cents_per_kwh / 100.0 for s in slots)
        expected_pct = current_battery_pct + (total_kwh / capacity_kwh * 100.0)
        expected_pct = min(expected_pct, 100.0)  # Cap at 100%

        plan = ChargingPlan(
            slots=slots,
            total_kwh=total_kwh,
            total_cost_eur=total_cost,
            expected_final_pct=expected_pct,
            target_pct=target_pct,
            flexible=not strict,
        )

        logger.info(f"Plan created:\n{plan.summary()}")
        return plan

    def _group_into_slots(
        self,
        hours: List[int],
        prices: Dict[int, float],
        power_kw: float,
        kwh_needed: float,
    ) -> List[ChargingSlot]:
        """Group consecutive hours into ChargingSlot objects, distributing remaining energy."""
        if not hours:
            return []

        slots: List[ChargingSlot] = []
        start = hours[0]
        prev = start
        remaining = kwh_needed

        for h in hours[1:]:
            if h == prev + 1:
                prev = h
            else:
                slot = self._make_slot(start, prev + 1, prices, power_kw, remaining)
                remaining -= slot.kwh_to_deliver
                slots.append(slot)
                start = h
                prev = h
        slot = self._make_slot(start, prev + 1, prices, power_kw, remaining)
        slots.append(slot)
        return slots

    def _make_slot(
        self,
        start: int,
        end: int,
        prices: Dict[int, float],
        power_kw: float,
        remaining_kwh: float,
    ) -> ChargingSlot:
        hours_in_slot = end - start
        efficiency = self.cfg.charging_efficiency
        max_kwh = hours_in_slot * power_kw * efficiency
        kwh = min(max_kwh, remaining_kwh) if remaining_kwh > 0 else 0.0
        avg_price = sum(prices.get(h, _MISSING_PRICE_SENTINEL) for h in range(start, end)) / hours_in_slot
        return ChargingSlot(
            start_hour=start,
            end_hour=end,
            price_cents_per_kwh=avg_price,
            kwh_to_deliver=kwh,
        )

    def replan(
        self,
        prices: Dict[int, float],
        current_battery_pct: float,
        current_hour: int,
        date_str: str,
        expected_pct: float,
    ) -> Optional[ChargingPlan]:
        """
        Re-plan if actual battery % is significantly behind expected.
        Returns a new plan if needed, or None if on track.
        """
        deficit = expected_pct - current_battery_pct
        if deficit > 2.0:  # More than 2% behind expected
            logger.warning(
                f"Behind schedule: expected {expected_pct:.1f}%, actual {current_battery_pct:.1f}% "
                f"(deficit: {deficit:.1f}%). Replanning..."
            )
            return self.plan(prices, current_battery_pct, current_hour, date_str)

        logger.info(f"On track: actual {current_battery_pct:.1f}% vs expected {expected_pct:.1f}%.")
        return None
