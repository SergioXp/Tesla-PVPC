"""Debug Tessie client: simulates a Tesla vehicle for testing and logging.

When no Tessie token is configured, this client replaces the real TessieClient
and provides simulated vehicle state with extensive debug logging.
"""

from datetime import datetime
from typing import Any, Dict, Optional

from auto_charge.config import Config
from auto_charge.tessie import VehicleState
from auto_charge.utils import logger, now_spain


class DebugTessieClient:
    """Simulates Tesla vehicle state for testing without a real Tessie token.

    Maintains an internal simulated battery that:
    - Starts at a configurable initial percentage (default: 35%)
    - Increments while "charging" based on charger power and efficiency
    - Tracks plugged-in state and charge limit
    - Logs every API call as [DEBUG] SIMULATED
    """

    def __init__(self, config: Config, initial_battery_pct: float = 35.0):
        self._cfg = config
        self._charging: bool = False
        self._battery_pct: float = initial_battery_pct
        self._charge_limit: float = float(config.min_battery_pct)
        self._plugged_in: bool = True
        self._last_state_time: Optional[datetime] = None
        self._action_log: list = []

        logger.info(f"[DEBUG] Simulated vehicle created: battery={initial_battery_pct:.1f}%, "
                     f"plugged_in=True, charge_limit={self._charge_limit:.0f}%")

    def _simulate_charge_progress(self) -> None:
        """Advance simulated battery based on elapsed time and charging state."""
        now = now_spain()
        if self._last_state_time is None:
            self._last_state_time = now
            return

        if not self._charging or not self._plugged_in:
            self._last_state_time = now
            return

        # Calculate elapsed hours since last state check
        elapsed_hours = (now - self._last_state_time).total_seconds() / 3600.0
        if elapsed_hours < 0:
            elapsed_hours = 0

        # kWh added = power_kw * efficiency * elapsed_hours
        power_kw = self._cfg.max_charger_power_kw
        efficiency = self._cfg.charging_efficiency
        capacity_kwh = self._cfg.battery_capacity_kwh

        kwh_added = power_kw * efficiency * elapsed_hours
        pct_added = (kwh_added / capacity_kwh) * 100.0

        old_pct = self._battery_pct
        self._battery_pct = min(self._battery_pct + pct_added, 100.0)
        self._last_state_time = now

        if self._battery_pct != old_pct:
            logger.info(
                f"[DEBUG] Battery simulation: {old_pct:.2f}% → {self._battery_pct:.2f}% "
                f"(+{pct_added:.2f}% over {elapsed_hours:.2f}h at {power_kw}kW)"
            )

    def _build_raw_state(self) -> Dict[str, Any]:
        """Build a fake raw state dict matching Tessie's API response format."""
        return {
            "charge_state": {
                "battery_level": self._battery_pct,
                "charging_state": "Charging" if self._charging else "Stopped",
                "charge_port_door_open": self._plugged_in,
                "charge_port_latch": "Engaged" if self._plugged_in else "Disengaged",
                "charge_limit_soc": self._charge_limit,
                "charger_power": self._cfg.max_charger_power_kw if self._charging else 0,
                "time_to_full_charge": (
                    (100.0 - self._battery_pct) / 100.0
                    * self._cfg.battery_capacity_kwh
                    / self._cfg.max_charger_power_kw
                ) if self._charging else 0,
            }
        }

    # ------------------------------------------------------------------
    # Public API (mirrors TessieClient)
    # ------------------------------------------------------------------

    def get_state(self) -> Optional[VehicleState]:
        self._simulate_charge_progress()
        raw = self._build_raw_state()
        logger.info(
            f"[DEBUG] get_state() → battery={self._battery_pct:.1f}%, "
            f"charging={'ON' if self._charging else 'OFF'}, "
            f"plugged={'YES' if self._plugged_in else 'NO'}"
        )
        return VehicleState(raw)

    def get_vehicle_data(self) -> Dict[str, Any]:
        state = self.get_state()
        return state.to_dict() if state else {}

    def start_charge(self) -> bool:
        now = now_spain()
        msg = f"[DEBUG] → SIMULATED API CALL: start_charge() at {now.strftime('%H:%M:%S')}"
        logger.info(msg)
        self._action_log.append(msg)
        self._charging = True
        self._last_state_time = now
        return True

    def stop_charge(self) -> bool:
        now = now_spain()
        msg = f"[DEBUG] → SIMULATED API CALL: stop_charge() at {now.strftime('%H:%M:%S')}"
        logger.info(msg)
        self._action_log.append(msg)
        self._charging = False
        return True

    def set_charge_limit(self, percent: int) -> bool:
        msg = f"[DEBUG] → SIMULATED API CALL: set_charge_limit({percent}%)"
        logger.info(msg)
        self._action_log.append(msg)
        self._charge_limit = float(percent)
        return True

    def set_plugged_in(self, state: bool) -> None:
        """Set whether the simulated car is plugged in (for testing edge cases)."""
        self._plugged_in = state
        logger.info(f"[DEBUG] Simulated plug state set to: {'plugged' if state else 'unplugged'}")

    def set_battery_pct(self, pct: float) -> None:
        """Override simulated battery percentage (for testing replanning)."""
        self._battery_pct = max(0.0, min(pct, 100.0))
        self._last_state_time = now_spain()
        logger.info(f"[DEBUG] Simulated battery manually set to: {self._battery_pct:.1f}%")

    def get_action_log(self) -> list:
        """Return the log of all simulated API calls made so far."""
        return list(self._action_log)
