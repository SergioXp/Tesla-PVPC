"""Configuration manager. Loads from .env and/or config.json with validation."""

import json
import os
from typing import Any, Dict, Optional

try:
    from dotenv import load_dotenv

    _DOTENV_LOADED = load_dotenv()
except ImportError:
    _DOTENV_LOADED = False

from auto_charge.utils import logger

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
EXAMPLE_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.example.json")

# Mapping: ENV_VAR → (config_key, type_coercion)
# type_coercion: "str", "int", "float", "bool"
ENV_MAP: Dict[str, tuple] = {
    "TESSIE_TOKEN": ("tessie_token", "str"),
    "VIN": ("vin", "str"),
    "ESIOS_TOKEN": ("esios_token", "str"),
    "MAX_PRICE_CENTS_PER_KWH": ("max_price_cents_per_kwh", "float"),
    "MAX_CHARGER_POWER_KW": ("max_charger_power_kw", "float"),
    "BATTERY_CAPACITY_KWH": ("battery_capacity_kwh", "float"),
    "MIN_BATTERY_PCT": ("min_battery_pct", "float"),
    "TARGET_TIME": ("target_time", "str"),
    "STRICT_MODE": ("strict_mode", "bool"),
    "CHARGING_EFFICIENCY": ("charging_efficiency", "float"),
    "CHECK_INTERVAL_MINUTES": ("check_interval_minutes", "int"),
    "TELEGRAM_BOT_TOKEN": ("telegram.bot_token", "str"),
    "TELEGRAM_CHAT_ID": ("telegram.chat_id", "str"),
}

def _coerce(value: str, coerce_type: str) -> Any:
    """Coerce a string env var to the appropriate Python type."""
    if coerce_type == "bool":
        return value.lower() in ("true", "1", "yes", "on")
    elif coerce_type == "int":
        try:
            return int(value)
        except ValueError:
            return None
    elif coerce_type == "float":
        try:
            return float(value)
        except ValueError:
            return None
    else:
        return value  # str


DEFAULT_CONFIG: Dict[str, Any] = {
    "tessie_token": "",
    "vin": "",
    "esios_token": "",
    "max_price_cents_per_kwh": 10,
    "max_charger_power_kw": 3.3,
    "battery_capacity_kwh": 75,
    "min_battery_pct": 70,
    "target_time": "19:00",
    "strict_mode": True,
    "charging_efficiency": 0.9,
    "check_interval_minutes": 15,
    "telegram": {
        "bot_token": "",
        "chat_id": "",
    },
}

REQUIRED_FIELDS = [
    "tessie_token",
    "vin",
    "esios_token",
    "max_price_cents_per_kwh",
    "max_charger_power_kw",
    "battery_capacity_kwh",
    "min_battery_pct",
    "target_time",
    "strict_mode",
    "charging_efficiency",
    "check_interval_minutes",
]


class Config:
    """Handles loading, saving, and accessing configuration."""

    def __init__(self, path: str = CONFIG_PATH):
        self._path = path
        self._data: Dict[str, Any] = dict(DEFAULT_CONFIG)
        self._load()

    def _load(self) -> None:
        has_config_json = os.path.exists(self._path)

        # 1. Load config.json if it exists
        if has_config_json:
            with open(self._path, "r") as f:
                user_config = json.load(f)
            self._data.update(user_config)

        # 2. Override with .env values (env vars take priority over config.json)
        self._merge_env()

        # 3. If no tokens at all: check if we should run in debug mode
        if not has_config_json and not self._data.get("tessie_token"):
            logger.warning(
                "⚠️  No Tessie token found. Running in DEBUG MODE (simulated vehicle). "
                "Add TESSIE_TOKEN to .env or config.json to use a real Tesla."
            )

        self._validate()

    def _merge_env(self) -> None:
        """Merge environment variables into config, overriding any existing values."""
        for env_var, (config_key, coerce_type) in ENV_MAP.items():
            value = os.environ.get(env_var)
            if value is None or value == "":
                continue

            coerced = _coerce(value, coerce_type)
            if coerced is None:
                logger.warning(
                    f"Env var {env_var}='{value}' could not be parsed as {coerce_type}, skipping."
                )
                continue

            # Handle nested keys (e.g., "telegram.bot_token")
            keys = config_key.split(".")
            d = self._data
            for k in keys[:-1]:
                d = d.setdefault(k, {})
            d[keys[-1]] = coerced

        if self._data.get("tessie_token") or self._data.get("esios_token"):
            logger.info("Configuration loaded from .env (env vars override config.json).")

    def _validate(self) -> None:
        missing = [k for k in REQUIRED_FIELDS if not self._data.get(k) and self._data.get(k) != 0]
        if missing:
            logger.warning(f"Missing config fields: {missing}")

        # Validate target_time format
        try:
            parts = self._data["target_time"].split(":")
            if len(parts) != 2:
                raise ValueError
            int(parts[0]), int(parts[1])
        except (ValueError, AttributeError):
            raise ValueError("target_time must be in HH:MM format (e.g., '19:00')")

    def save(self) -> None:
        """Persist current config to disk (preserving comments is not supported)."""
        with open(self._path, "w") as f:
            json.dump(self._data, f, indent=4, ensure_ascii=False)
        logger.info("Configuration saved.")

    # --- Getters ---

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._data:
            return self._data[name]
        raise AttributeError(f"Config has no attribute '{name}'")

    @property
    def tessie_token(self) -> str:
        return self._data["tessie_token"]

    @property
    def vin(self) -> str:
        return self._data["vin"]

    @property
    def esios_token(self) -> str:
        return self._data["esios_token"]

    @property
    def max_price_cents_per_kwh(self) -> float:
        return float(self._data["max_price_cents_per_kwh"])

    @property
    def max_charger_power_kw(self) -> float:
        return float(self._data["max_charger_power_kw"])

    @property
    def battery_capacity_kwh(self) -> float:
        return float(self._data["battery_capacity_kwh"])

    @property
    def min_battery_pct(self) -> float:
        return float(self._data["min_battery_pct"])

    @property
    def target_time(self) -> str:
        return self._data["target_time"]

    @property
    def target_hour(self) -> int:
        return int(self._data["target_time"].split(":")[0])

    @property
    def target_minute(self) -> int:
        return int(self._data["target_time"].split(":")[1])

    @property
    def strict_mode(self) -> bool:
        return bool(self._data["strict_mode"])

    @property
    def check_interval_minutes(self) -> int:
        return int(self._data["check_interval_minutes"])

    @property
    def charging_efficiency(self) -> float:
        return float(self._data.get("charging_efficiency", 0.9))

    @property
    def telegram_bot_token(self) -> str:
        return self._data.get("telegram", {}).get("bot_token", "")

    @property
    def telegram_chat_id(self) -> str:
        return self._data.get("telegram", {}).get("chat_id", "")

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def debug_mode(self) -> bool:
        """True if Tessie token is missing → run in debug/simulation mode."""
        return not self._data.get("tessie_token")

    # --- Setters ---

    def set(self, key: str, value: Any) -> None:
        """Set a config value. Supports nested keys with dot notation (e.g., 'telegram.bot_token')."""
        keys = key.split(".")
        d = self._data
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = value
        self.save()

    def set_and_save(self, key: str, value: Any) -> None:
        self.set(key, value)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._data)
