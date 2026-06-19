"""Tests for config module with mocked file I/O and env var manipulation.

Covers:
- Config loading: config.json + .env merge
- Token migration: config.json → .env
- _set_env_var: create new file, update existing
- Config.set(): secrets to .env, non-secrets to config.json
- Config.save(): saves without tokens
- _coerce: all types and edge cases
- _merge_env: env var priority, nested keys
- Config validation: target_time format
- Property access: debug_mode, telegram_enabled, etc.
"""

import os
import sys
import json
import tempfile
from unittest.mock import MagicMock, patch, mock_open, call
from typing import Dict, Any
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from auto_charge.config import (
    Config,
    _set_env_var,
    _coerce,
    DEFAULT_CONFIG,
    REQUIRED_FIELDS,
    SECRET_KEYS,
    CONFIG_TO_ENV,
    ENV_MAP,
    CONFIG_PATH,
    ENV_PATH,
)

# =============================================================================
# _coerce tests
# =============================================================================

class TestCoerce:
    """Test _coerce type coercion function."""

    def test_coerce_str(self):
        """String type returns value as-is."""
        assert _coerce("anything", "str") == "anything"
        assert _coerce("", "str") == ""

    def test_coerce_bool_true(self):
        """Various true representations."""
        assert _coerce("true", "bool") is True
        assert _coerce("True", "bool") is True
        assert _coerce("1", "bool") is True
        assert _coerce("yes", "bool") is True
        assert _coerce("on", "bool") is True
        # Note: 'Y'/'y' is NOT recognized (only 'yes' is), so this would be False

    def test_coerce_bool_false(self):
        """Various false representations."""
        assert _coerce("false", "bool") is False
        assert _coerce("False", "bool") is False
        assert _coerce("0", "bool") is False
        assert _coerce("no", "bool") is False
        assert _coerce("off", "bool") is False
        assert _coerce("random", "bool") is False

    def test_coerce_int_valid(self):
        """Valid integers."""
        assert _coerce("42", "int") == 42
        assert _coerce("0", "int") == 0
        assert _coerce("-10", "int") == -10

    def test_coerce_int_invalid(self):
        """Invalid int returns None."""
        assert _coerce("12.5", "int") is None
        assert _coerce("abc", "int") is None
        assert _coerce("", "int") is None

    def test_coerce_float_valid(self):
        """Valid floats."""
        assert _coerce("3.14", "float") == 3.14
        assert _coerce("10", "float") == 10.0
        assert _coerce("0", "float") == 0.0
        assert _coerce("-2.5", "float") == -2.5

    def test_coerce_float_invalid(self):
        """Invalid float returns None."""
        assert _coerce("abc", "float") is None
        assert _coerce("", "float") is None


# =============================================================================
# _set_env_var tests
# =============================================================================

class TestSetEnvVar:
    """Test _set_env_var file manipulation."""

    def test_create_new_env_file(self):
        """_set_env_var creates new file if none exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("auto_charge.config.ENV_PATH", os.path.join(tmpdir, ".env")):
                _set_env_var("TESSIE_TOKEN", "secret123")

                assert os.path.exists(os.path.join(tmpdir, ".env"))
                with open(os.path.join(tmpdir, ".env")) as f:
                    content = f.read()
                assert "TESSIE_TOKEN=secret123" in content

    def test_update_existing_env_file(self):
        """_set_env_var updates existing variable in file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = os.path.join(tmpdir, ".env")
            with open(env_path, "w") as f:
                f.write("TESSIE_TOKEN=old_value\nVIN=TESTVIN\n")

            with patch("auto_charge.config.ENV_PATH", env_path):
                _set_env_var("TESSIE_TOKEN", "new_secret")

                with open(env_path) as f:
                    content = f.read()
                assert "TESSIE_TOKEN=new_secret" in content
                assert "VIN=TESTVIN" in content  # Preserved

    def test_preserves_other_lines(self):
        """_set_env_var preserves comments and other env vars."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = os.path.join(tmpdir, ".env")
            with open(env_path, "w") as f:
                f.write("# This is a comment\nOTHER_VAR=hello\n")

            with patch("auto_charge.config.ENV_PATH", env_path):
                _set_env_var("TESSIE_TOKEN", "token123")

                with open(env_path) as f:
                    content = f.read()
                assert "# This is a comment" in content
                assert "OTHER_VAR=hello" in content
                assert "TESSIE_TOKEN=token123" in content

    def test_handles_export_prefix(self):
        """_set_env_var handles 'export KEY=value' format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = os.path.join(tmpdir, ".env")
            with open(env_path, "w") as f:
                f.write("export TESSIE_TOKEN=old_value\n")

            with patch("auto_charge.config.ENV_PATH", env_path):
                _set_env_var("TESSIE_TOKEN", "new_token")

                with open(env_path) as f:
                    content = f.read()
                assert "TESSIE_TOKEN=new_token" in content
                assert "export " not in content


# =============================================================================
# Config initialization and loading
# =============================================================================

class TestConfigInit:
    """Test Config loading with mocked files and env vars."""

    def test_sets_defaults_when_no_config(self):
        """No config.json, no .env → uses defaults."""
        with patch("os.path.exists", return_value=False), \
             patch("auto_charge.config.ENV_MAP", {}):  # No env vars
            cfg = Config.__new__(Config)
            cfg._data = dict(DEFAULT_CONFIG)
            # Manually validate a valid target_time
            cfg._data["target_time"] = "19:00"
            cfg._validate()

        assert cfg._data["target_time"] == "19:00"
        assert cfg._data["max_price_cents_per_kwh"] == 10

    def test_loads_from_config_json(self):
        """Config loads from config.json."""
        config_data = {
            "max_price_cents_per_kwh": 15,
            "min_battery_pct": 80,
            "target_time": "22:00",
            "strict_mode": False,
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config_data, f)
            f.flush()
            config_path = f.name

        try:
            cfg = Config(config_path)
            assert cfg.max_price_cents_per_kwh == 15.0
            assert cfg.min_battery_pct == 80.0
            assert cfg.target_time == "22:00"
            assert cfg.strict_mode is False
        finally:
            os.unlink(config_path)

    def test_env_overrides_config_json(self):
        """Env variables override config.json values."""
        config_data = {"max_price_cents_per_kwh": 5, "target_time": "19:00"}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config_data, f)
            f.flush()
            config_path = f.name

        try:
            with patch.dict(os.environ, {"MAX_PRICE_CENTS_PER_KWH": "12"}, clear=False):
                cfg = Config(config_path)
                # Env should override config.json value of 5
                assert cfg.max_price_cents_per_kwh == 12.0, \
                    f"Should be overridden by env, got {cfg.max_price_cents_per_kwh}"
        finally:
            os.unlink(config_path)

    def test_handles_nested_env_keys(self):
        """Nested keys like telegram.bot_token are properly set from env."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"target_time": "19:00"}, f)
            f.flush()
            config_path = f.name

        try:
            with patch.dict(os.environ, {
                "TELEGRAM_BOT_TOKEN": "bot_from_env",
                "TELEGRAM_CHAT_ID": "chat_from_env",
            }, clear=False):
                cfg = Config(config_path)
                assert cfg.telegram_bot_token == "bot_from_env"
                assert cfg.telegram_chat_id == "chat_from_env"
                assert cfg.telegram_enabled is True
        finally:
            os.unlink(config_path)

    def test_target_time_validation_valid(self):
        """Valid target_time HH:MM passes validation."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"target_time": "19:00"}, f)
            f.flush()

        try:
            cfg = Config(f.name)
            assert cfg.target_hour == 19
            assert cfg.target_minute == 0
        finally:
            os.unlink(f.name)

    def test_target_time_validation_invalid_format_raises(self):
        """Invalid target_time format raises ValueError."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"target_time": "abc"}, f)  # Not HH:MM
            f.flush()

        try:
            with pytest.raises((ValueError, AttributeError, KeyError)):
                Config(f.name)
        finally:
            os.unlink(f.name)

    def test_target_time_validation_bad_format(self):
        """Badly formatted target_time raises ValueError."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"target_time": "nineteen"}, f)
            f.flush()

        try:
            with pytest.raises((ValueError, KeyError)):
                Config(f.name)
        finally:
            os.unlink(f.name)


# =============================================================================
# Config property access
# =============================================================================

class TestConfigProperties:
    """Test Config property accessors."""

    @pytest.fixture
    def cfg(self):
        """Minimal Config instance with known values."""
        c = Config.__new__(Config)
        c._data = {
            "tessie_token": "tok123",
            "vin": "VIN123",
            "esios_token": "esios_tok",
            "max_price_cents_per_kwh": 10,
            "max_charger_power_kw": 3.3,
            "battery_capacity_kwh": 75,
            "min_battery_pct": 70,
            "target_time": "19:00",
            "strict_mode": True,
            "charging_efficiency": 0.9,
            "check_interval_minutes": 15,
            "telegram": {"bot_token": "bot_tok", "chat_id": "chat_id"},
        }
        return c

    def test_properties(self, cfg):
        """All property accessors return correct values."""
        assert cfg.tessie_token == "tok123"
        assert cfg.vin == "VIN123"
        assert cfg.esios_token == "esios_tok"
        assert cfg.max_price_cents_per_kwh == 10.0
        assert cfg.max_charger_power_kw == 3.3
        assert cfg.battery_capacity_kwh == 75.0
        assert cfg.min_battery_pct == 70.0
        assert cfg.target_time == "19:00"
        assert cfg.target_hour == 19
        assert cfg.target_minute == 0
        assert cfg.strict_mode is True
        assert cfg.check_interval_minutes == 15
        assert cfg.charging_efficiency == 0.9

    def test_debug_mode_no_token(self):
        """No tessie_token → debug_mode = True."""
        c = Config.__new__(Config)
        c._data = dict(DEFAULT_CONFIG)
        c._data["tessie_token"] = ""
        assert c.debug_mode is True

    def test_debug_mode_with_token(self):
        """Has tessie_token → debug_mode = False."""
        c = Config.__new__(Config)
        c._data = dict(DEFAULT_CONFIG)
        c._data["tessie_token"] = "exists"
        assert c.debug_mode is False

    def test_telegram_enabled_both_present(self):
        """Both telegram tokens → enabled."""
        c = Config.__new__(Config)
        c._data = dict(DEFAULT_CONFIG)
        c._data["telegram"] = {"bot_token": "b", "chat_id": "c"}
        assert c.telegram_enabled is True

    def test_telegram_disabled_missing_token(self):
        """Missing bot_token → disabled."""
        c = Config.__new__(Config)
        c._data = dict(DEFAULT_CONFIG)
        c._data["telegram"] = {"bot_token": "", "chat_id": "c"}
        assert c.telegram_enabled is False

    def test_telegram_disabled_missing_chat(self):
        """Missing chat_id → disabled."""
        c = Config.__new__(Config)
        c._data = dict(DEFAULT_CONFIG)
        c._data["telegram"] = {"bot_token": "b", "chat_id": ""}
        assert c.telegram_enabled is False


# =============================================================================
# Config.set() tests
# =============================================================================

class TestConfigSet:
    """Test Config.set() with mocked file I/O."""

    def test_set_nonsecret_saves_to_json(self):
        """Non-secret key → saves to config.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = os.path.join(tmpdir, "config.json")
            with open(json_path, "w") as f:
                json.dump({"target_time": "19:00"}, f)

            cfg = Config(json_path)

            with patch("auto_charge.config.ENV_PATH", os.path.join(tmpdir, ".env")):
                cfg.set("max_price_cents_per_kwh", 8)

                # Should update in-memory data
                assert cfg.max_price_cents_per_kwh == 8.0
                # Should save to config.json
                with open(json_path) as f:
                    saved = json.load(f)
                assert saved.get("max_price_cents_per_kwh") == 8

    def test_set_secret_saves_to_env(self):
        """Secret key → saves to .env."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = os.path.join(tmpdir, "config.json")
            with open(json_path, "w") as f:
                json.dump({"target_time": "19:00"}, f)

            env_path = os.path.join(tmpdir, ".env")
            cfg = Config(json_path)

            with patch("auto_charge.config.ENV_PATH", env_path):
                cfg.set("tessie_token", "new_tessie_token")

                # Should update in-memory data
                assert cfg.tessie_token == "new_tessie_token"
                # Should save to .env file
                with open(env_path) as f:
                    content = f.read()
                assert "TESSIE_TOKEN=new_tessie_token" in content


# =============================================================================
# Config save and to_dict
# =============================================================================

class TestConfigSave:
    """Test Config.save() strips tokens and writes correctly."""

    def test_save_strips_tokens(self):
        """Save should not include secret keys in output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = os.path.join(tmpdir, "config.json")
            with open(json_path, "w") as f:
                json.dump({
                    "tessie_token": "should_not_be_saved",
                    "max_price_cents_per_kwh": 10,
                    "target_time": "19:00",
                }, f)

            cfg = Config.__new__(Config)
            cfg._data = {
                "tessie_token": "secret",
                "max_price_cents_per_kwh": 15,
                "target_time": "19:00",
                "vin": "VIN123",
            }
            cfg._path = json_path
            cfg.save()

            with open(json_path) as f:
                saved = json.load(f)
            assert "tessie_token" not in saved, "Token should not be in config.json"
            assert "vin" not in saved, "VIN should not be in config.json"
            assert saved["max_price_cents_per_kwh"] == 15, "Non-secret should be saved"

    def test_to_dict_returns_all_data(self):
        """to_dict() returns complete _data dict."""
        cfg = Config.__new__(Config)
        cfg._data = {"key": "value", "num": 42}
        d = cfg.to_dict()
        assert d == {"key": "value", "num": 42}


# =============================================================================
# Config validation
# =============================================================================

class TestConfigValidation:
    """Test Config validation edge cases."""

    def test_missing_required_fields_logs_warning(self):
        """Missing required fields → warning logged, no crash."""
        cfg = Config.__new__(Config)
        cfg._data = {k: "" for k in REQUIRED_FIELDS}
        cfg._data["target_time"] = "19:00"
        cfg._path = "/dev/null"
        # Should not raise, just log warning
        cfg._validate()

    def test_charging_efficiency_default(self):
        """charging_efficiency defaults to 0.9 when missing."""
        c = Config.__new__(Config)
        c._data = dict(DEFAULT_CONFIG)
        del c._data["charging_efficiency"]
        assert c.charging_efficiency == 0.9

    def test_get_with_default(self):
        """get() returns default for missing key."""
        cfg = Config.__new__(Config)
        cfg._data = {}
        assert cfg.get("nonexistent", "fallback") == "fallback"
        assert cfg.get("nonexistent") is None


# =============================================================================
# Token migration
# =============================================================================

class TestTokenMigration:
    """Test token migration from config.json to .env."""

    def test_migration_flat_keys(self):
        """Flat secret keys (tessie_token) are migrated to .env."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = os.path.join(tmpdir, "config.json")
            with open(json_path, "w") as f:
                json.dump({
                    "tessie_token": "old_token_in_json",
                    "target_time": "19:00",
                }, f)

            env_path = os.path.join(tmpdir, ".env")
            cfg = Config.__new__(Config)
            cfg._path = json_path
            cfg._data = {
                "tessie_token": "old_token_in_json",
                "target_time": "19:00",
            }

            with patch("auto_charge.config.ENV_PATH", env_path), \
                 patch.dict(os.environ, {}, clear=True):
                cfg._migrate_tokens()

                # Token should be in .env
                with open(env_path) as f:
                    env_content = f.read()
                assert "TESSIE_TOKEN=old_token_in_json" in env_content

    def test_migration_nested_keys(self):
        """Nested secret keys (telegram.bot_token) are migrated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = os.path.join(tmpdir, "config.json")
            with open(json_path, "w") as f:
                json.dump({
                    "telegram": {"bot_token": "tg_token_in_json", "chat_id": "tg_chat"},
                    "target_time": "19:00",
                }, f)

            env_path = os.path.join(tmpdir, ".env")
            cfg = Config.__new__(Config)
            cfg._path = json_path
            cfg._data = {
                "telegram": {"bot_token": "tg_token_in_json", "chat_id": "tg_chat"},
                "target_time": "19:00",
            }

            with patch("auto_charge.config.ENV_PATH", env_path), \
                 patch.dict(os.environ, {}, clear=True):
                cfg._migrate_tokens()

                with open(env_path) as f:
                    env_content = f.read()
                assert "TELEGRAM_BOT_TOKEN=tg_token_in_json" in env_content

    def test_migration_skips_if_env_already_set(self):
        """Migration skips token if already in .env."""
        cfg = Config.__new__(Config)
        cfg._data = {
            "tessie_token": "token_in_json",
            "target_time": "19:00",
        }

        with patch("auto_charge.config.ENV_PATH", "/tmp/.env_nonexistent"), \
             patch.dict(os.environ, {"TESSIE_TOKEN": "already_in_env"}, clear=True), \
             patch("auto_charge.config._set_env_var") as mock_set:
            cfg._migrate_tokens()

            # Should NOT call _set_env_var because env already has it
            mock_set.assert_not_called()
