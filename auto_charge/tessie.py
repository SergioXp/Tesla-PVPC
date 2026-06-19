"""Tessie API client: vehicle state, charging control."""

import time
from typing import Any, Dict, Optional

import requests

from auto_charge.config import Config
from auto_charge.utils import logger

TESSIE_BASE_URL = "https://api.tessie.com"
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds
_COMMAND_OPTS = "?wait_for_completion=true&max_attempts=3"


class VehicleState:
    """Represents the current state of the Tesla vehicle."""

    def __init__(self, raw: Dict[str, Any]):
        self._raw = raw

    @property
    def battery_pct(self) -> float:
        """Battery state of charge in percent (0-100)."""
        val = self._raw.get("charge_state", {}).get("battery_level", 0)
        return 0.0 if val is None else float(val)

    @property
    def is_charging(self) -> bool:
        """Whether the vehicle is currently charging."""
        return self._raw.get("charge_state", {}).get("charging_state", "") == "Charging"

    @property
    def is_plugged_in(self) -> bool:
        """Whether the charge cable is connected."""
        cs = self._raw.get("charge_state", {})
        return cs.get("charge_port_door_open", False) or cs.get("charge_port_latch", "") != "Disengaged"

    @property
    def charge_limit_pct(self) -> float:
        """Current charge limit set in the vehicle."""
        val = self._raw.get("charge_state", {}).get("charge_limit_soc", 100)
        return 100.0 if val is None else float(val)

    @property
    def charger_power_kw(self) -> float:
        """Current charging power in kW."""
        val = self._raw.get("charge_state", {}).get("charger_power", 0)
        return 0.0 if val is None else float(val)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "battery_pct": self.battery_pct,
            "is_charging": self.is_charging,
            "is_plugged_in": self.is_plugged_in,
            "charge_limit_pct": self.charge_limit_pct,
            "charger_power_kw": self.charger_power_kw,
        }


class TessieClient:
    """Client for the Tessie API to control a Tesla vehicle."""

    def __init__(self, config: Config):
        self._token = config.tessie_token
        self._vin = config.vin
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            }
        )

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._session.close()

    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Make an API request with smart retry logic.

        - 4xx errors (client errors): no retry, log as info (common for Tessie)
        - 5xx / network errors: retry up to MAX_RETRIES times
        - body=None → no JSON body sent (Tessie uses query params for commands)
        """
        url = f"{TESSIE_BASE_URL}/{self._vin}{path}"
        for attempt in range(MAX_RETRIES):
            try:
                if method == "GET":
                    resp = self._session.get(url, timeout=30)
                elif body is not None:
                    resp = self._session.post(url, json=body, timeout=30)
                else:
                    resp = self._session.post(url, timeout=30)
                resp.raise_for_status()
                return resp.json()
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if 400 <= status < 500:
                    # 4xx: client error, won't succeed on retry
                    logger.info(f"Tessie {method} {path} → {status} ({e.response.reason if e.response is not None else '?'})")
                    return None
                # 5xx: server error, retry
                logger.warning(f"Tessie {method} {path} failed (attempt {attempt+1}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY)
            except requests.RequestException as e:
                # Network errors: retry
                logger.warning(f"Tessie {method} {path} failed (attempt {attempt+1}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY)
        return None

    def _get(self, path: str) -> Optional[Dict[str, Any]]:
        return self._request("GET", path)

    def _post(self, path: str) -> Optional[Dict[str, Any]]:
        """POST with query params embedded in the path string."""
        return self._request("POST", path)

    def get_state(self) -> Optional[VehicleState]:
        """Get the full vehicle state."""
        data = self._get("/state")
        if data:
            return VehicleState(data)
        return None

    def get_vehicle_data(self) -> Dict[str, Any]:
        """Get raw vehicle data dict."""
        state = self.get_state()
        if state:
            return state.to_dict()
        return {}

    def start_charge(self) -> bool:
        """Start charging the vehicle."""
        logger.info("Sending START charge command...")
        result = self._post(f"/command/start_charging{_COMMAND_OPTS}")
        if result is not None:
            logger.info("Charge start command sent successfully.")
            return True
        return False

    def stop_charge(self) -> bool:
        """Stop charging the vehicle."""
        logger.info("Sending STOP charge command...")
        result = self._post(f"/command/stop_charging{_COMMAND_OPTS}")
        if result is not None:
            logger.info("Charge stop command sent successfully.")
            return True
        return False

    def set_charge_limit(self, percent: int) -> bool:
        """Set the charge limit percentage."""
        logger.info(f"Setting charge limit to {percent}%...")
        result = self._post(f"/command/set_charge_limit?percent={percent}&{_COMMAND_OPTS[1:]}")
        if result is not None:
            logger.info(f"Charge limit set to {percent}%.")
            return True
        return False


class ReadOnlyVehicleClient:
    """Wraps any vehicle client but blocks all write commands (dry-run mode).

    Reads (get_state) go through to the real API.
    Writes (start_charge, stop_charge, set_charge_limit) are intercepted,
    logged, and skipped — no commands are sent to the vehicle.
    Deduplicates repeated calls to avoid log noise.
    """

    def __init__(self, real_client):
        self._real = real_client
        self._blocked_actions: list = []
        self._last_write_state: str = ""  # Track to deduplicate logs
        logger.info("🛡️  DRY-RUN mode: reads real vehicle data, blocks all write commands.")

    def get_state(self):
        return self._real.get_state()

    def get_vehicle_data(self):
        return self._real.get_vehicle_data()

    def _log_blocked(self, msg: str) -> None:
        """Log a blocked action, deduplicating repeated calls."""
        if msg != self._last_write_state:
            logger.info(msg)
            self._last_write_state = msg
        self._blocked_actions.append(msg)

    def start_charge(self) -> bool:
        msg = "[DRY-RUN] → BLOCKED: start_charge() — would have sent START command"
        self._log_blocked(msg)
        return True

    def stop_charge(self) -> bool:
        msg = "[DRY-RUN] → BLOCKED: stop_charge() — would have sent STOP command"
        self._log_blocked(msg)
        return True

    def set_charge_limit(self, percent: int) -> bool:
        msg = f"[DRY-RUN] → BLOCKED: set_charge_limit({percent}) — would have set limit to {percent}%"
        self._log_blocked(msg)
        return True

    def get_blocked_log(self) -> list:
        """Return the log of all blocked write actions."""
        return list(self._blocked_actions)
