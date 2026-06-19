"""Configuration manager. Loads from .env and/or config.json with validation.

Sensitive data (tokens, VIN) goes in .env.
Non-sensitive settings (prices, times, etc.) go in config.json.
"""

import json
import os
import re
import tempfile
from typing import Any, Dict, List, Optional, Set

try:
    from dotenv import load_dotenv

    _DOTENV_LOADED = load_dotenv()
except ImportError:
    _DOTENV_LOADED = False

from auto_charge.utils import logger

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
EXAMPLE_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.example.json")
ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")

# Which config keys are secrets (tokens, VIN) and should ALWAYS go in .env
SECRET_KEYS: Set[str] = {
    "tessie_token",
    "vin",
    "esios_token",
    "telegram.bot_token",
    "telegram.chat_id",
}

# Reverse map: config_key → ENV_VAR
CONFIG_TO_ENV: Dict[str, str] = {
    "tessie_token": "TESSIE_TOKEN",
    "vin": "VIN",
    "esios_token": "ESIOS_TOKEN",
    "max_price_cents_per_kwh": "MAX_PRICE_CENTS_PER_KWH",
    "max_charger_power_kw": "MAX_CHARGER_POWER_KW",
    "battery_capacity_kwh": "BATTERY_CAPACITY_KWH",
    "min_battery_pct": "MIN_BATTERY_PCT",
    "target_time": "TARGET_TIME",
    "strict_mode": "STRICT_MODE",
    "charging_efficiency": "CHARGING_EFFICIENCY",
    "check_interval_minutes": "CHECK_INTERVAL_MINUTES",
    "telegram.bot_token": "TELEGRAM_BOT_TOKEN",
    "telegram.chat_id": "TELEGRAM_CHAT_ID",
}

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
    "max_price_cents_per_kwh",
    "max_charger_power_kw",
    "battery_capacity_kwh",
    "min_battery_pct",
    "target_time",
    "strict_mode",
    "charging_efficiency",
    "check_interval_minutes",
]


def _set_env_var(env_var: str, value: str) -> None:
    """Set or update a variable in the .env file.

    Creates the file if it doesn't exist. Preserves existing comments and order.
    """
    lines: List[str] = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r") as f:
            lines = f.readlines()

    # Find and replace existing assignment, or append
    found = False
    new_lines: List[str] = []
    for line in lines:
        stripped = line.strip()
        # Match KEY=value or export KEY=value (ignore comments)
        if re.match(rf"^(export\s+)?{re.escape(env_var)}=", stripped):
            new_lines.append(f"{env_var}={value}\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append(f"{env_var}={value}\n")

    os.makedirs(os.path.dirname(ENV_PATH) or ".", exist_ok=True)
    with open(ENV_PATH, "w") as f:
        f.writelines(new_lines)


def _atomic_write(path: str, data: dict) -> None:
    """Write JSON data to a file atomically to prevent corruption on crash.

    Writes to a temporary file in the same directory, then renames it
    to the target path (rename is atomic on Unix).
    """
    dir_path = os.path.dirname(path) or "."
    os.makedirs(dir_path, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(tmp_path, path)
    except:
        # Clean up temp file on any error
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


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
            try:
                with open(self._path, "r") as f:
                    user_config = json.load(f)
            except json.JSONDecodeError as e:
                logger.error(
                    f"❌ config.json has invalid JSON at line {e.lineno}, column {e.colno}: {e.msg}\n"
                    f"   Run '--init' to recreate it or fix the file manually."
                )
                user_config = {}
            self._data.update(user_config)

        # 2. Migrate tokens from config.json → .env (secrets don't belong in config.json)
        self._migrate_tokens()

        # 3. Override with .env values (env vars take priority over config.json)
        self._merge_env()

        # 4. If no tokens at all: check if we should run in debug mode
        if not self._data.get("tessie_token"):
            logger.warning(
                "⚠️  No Tessie token found. Running in DEBUG MODE (simulated vehicle). "
                "Add TESSIE_TOKEN to .env to use a real Tesla."
            )

        self._validate()

    def _get_nested(self, key: str) -> Any:
        """Get a value from _data using dot notation (e.g. 'telegram.bot_token')."""
        keys = key.split(".")
        d = self._data
        for k in keys:
            if isinstance(d, dict):
                d = d.get(k)
            else:
                return None
        return d

    def _migrate_tokens(self) -> None:
        """Migrate any tokens still in config.json to .env, then remove from config.json.
        This runs once per load to transition users from the old config.json-only setup.
        Handles both flat keys (tessie_token) and nested keys (telegram.bot_token).
        """
        dirty = False
        for config_key, env_var in CONFIG_TO_ENV.items():
            if config_key not in SECRET_KEYS:
                continue
            value = self._get_nested(config_key)
            if value and not os.environ.get(env_var):
                # Token exists in config.json but not in .env → migrate
                _set_env_var(env_var, str(value))
                logger.info(f"🔒 Migrated {env_var} from config.json to .env")
                dirty = True
                # Also set in current environment so _merge_env picks it up
                os.environ[env_var] = str(value)

        if dirty:
            # Clean tokens from _data and save config.json without them
            self._strip_tokens()
            if os.path.exists(self._path):
                _atomic_write(self._path, self._strip_tokens_from_dict(dict(self._data)))

    def _strip_tokens_from_dict(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Remove secret keys from a config dict."""
        result = {}
        for k, v in data.items():
            if k in SECRET_KEYS:
                continue
            if k == "telegram" and isinstance(v, dict):
                # Keep telegram section but remove nested secret keys
                cleaned = {}
                for tk, tv in v.items():
                    if f"telegram.{tk}" not in SECRET_KEYS:
                        cleaned[tk] = tv
                if cleaned:
                    result[k] = cleaned
            else:
                result[k] = v
        return result

    def _strip_tokens(self) -> None:
        """Remove secret keys from in-memory _data."""
        for key in list(SECRET_KEYS):
            if "." in key:
                parts = key.split(".")
                if parts[0] in self._data and isinstance(self._data[parts[0]], dict):
                    self._data[parts[0]].pop(parts[1], None)
            else:
                self._data.pop(key, None)

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
        """Persist non-sensitive config to config.json (tokens stay in .env)."""
        data_to_save = self._strip_tokens_from_dict(self._data)
        if data_to_save:
            _atomic_write(self._path, data_to_save)
            logger.info("Configuration saved (tokens kept in .env).")

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
        """Set a config value. Secrets go to .env, non-secrets to config.json."""
        if key in SECRET_KEYS:
            # Save to .env
            env_var = CONFIG_TO_ENV.get(key)
            if env_var:
                _set_env_var(env_var, str(value))
                # Also update current environment so subsequent _merge_env picks it up
                os.environ[env_var] = str(value)
            # Update in-memory dict
            keys = key.split(".")
            d = self._data
            for k in keys[:-1]:
                d = d.setdefault(k, {})
            d[keys[-1]] = value
            logger.info(f"🔒 {key} saved to .env")
        else:
            # Save to config.json as before
            keys = key.split(".")
            d = self._data
            for k in keys[:-1]:
                d = d.setdefault(k, {})
            d[keys[-1]] = value
            self.save()

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._data)
