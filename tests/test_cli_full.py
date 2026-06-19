"""Tests for tesla_pvpc.py covering remaining uncovered lines.

Targets: show_config with .env/secrets, show_prices edge cases,
_print_prices_table formatting branches, _format_slot_hours, _kill_existing_instances,
_daemonize, _build_monitor_status_fn, main() dispatch, run_once pipeline.
"""

import os
import sys
import argparse
import json
import tempfile
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, call
from typing import Dict
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tesla_pvpc import (
    show_prices,
    show_config,
    show_dashboard,
    _print_prices_table,
    _format_slot_hours,
    _kill_existing_instances,
    _daemonize,
    _build_monitor_status_fn,
    main,
    parse_args,
    run_once,
)


# =============================================================================
# show_config with .env and secrets
# =============================================================================

class TestShowConfigEnv:
    """Cover show_config with .env tokens and secret display."""

    def test_show_config_with_tokens_in_env(self):
        """show_config displays masked tokens from .env."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "max_price_cents_per_kwh": 10,
                "target_time": "19:00",
                "strict_mode": True,
            }, f)
            f.flush()

            # Also write a .env file next to it
            env_path = os.path.join(os.path.dirname(f.name), ".env")
            with open(env_path, "w") as env_f:
                env_f.write("TESSIE_TOKEN=my_secret_token_here\n")

            with patch("builtins.print") as mock_print:
                show_config(f.name)

            printed = [str(c[0][0]) for c in mock_print.call_args_list if c[0]]
            full = "\n".join(printed)
            assert "max_price_cents_per_kwh" in full
            assert "my_secret_token_here" not in full  # Should not show full token

            os.unlink(f.name)
            os.unlink(env_path)

    def test_show_config_no_file_shows_defaults(self):
        """No config file → shows defaults."""
        with patch("os.path.exists", return_value=False), \
             patch("builtins.print") as mock_print:
            show_config("/nonexistent/config.json")

        printed = [str(c[0][0]) for c in mock_print.call_args_list if c[0]]
        assert len(printed) > 0


# =============================================================================
# _print_prices_table formatting branches
# =============================================================================

class TestPrintPricesDetail:
    """Cover formatting edge cases."""

    def test_prints_current_hour_marker(self):
        """Current hour should be marked with a marker."""
        prices = {h: 8.0 for h in range(24)}

        with patch("builtins.print") as mock_print:
            _print_prices_table(prices, "2026-06-19", "test")

        printed = [str(c[0][0]) for c in mock_print.call_args_list if c[0]]
        full = "\n".join(printed)
        # Should have some hour markers
        assert "◀" in full or "c/kWh" in full

    def test_with_max_price_shows_hours_below(self):
        """Max price limit shows available hours count."""
        prices = {h: 8.0 if h < 12 else 15.0 for h in range(24)}

        with patch("builtins.print") as mock_print:
            _print_prices_table(prices, "2026-06-19", "test", max_price_limit=10.0)

        printed = [str(c[0][0]) for c in mock_print.call_args_list if c[0]]
        full = "\n".join(printed)
        assert "Límite" in full
        assert "12h" in full or "50%" in full

    def test_single_price_value_all_same(self):
        """All hours same price → no division by zero."""
        prices = {h: 10.0 for h in range(24)}

        with patch("builtins.print") as mock_print:
            _print_prices_table(prices, "2026-06-19", "test")

        printed = [str(c[0][0]) for c in mock_print.call_args_list if c[0]]
        assert len(printed) > 0


# =============================================================================
# run_once pipeline
# =============================================================================

class TestRunOncePipeline:
    """Cover run_once with mocked components."""

    def test_run_once_battery_above_target_plans_anyway(self):
        """run_once with battery already above target still checks plan."""
        cfg = MagicMock()
        cfg.target_hour = 19
        cfg.target_time = "19:00"
        cfg.debug_mode = True
        cfg.max_price_cents_per_kwh = 10.0
        cfg.max_charger_power_kw = 3.3
        cfg.battery_capacity_kwh = 75.0
        cfg.min_battery_pct = 70.0
        cfg.strict_mode = True
        cfg.charging_efficiency = 0.9
        cfg.tessie_token = ""

        mock_price_provider = MagicMock()
        mock_price_provider.fetch_daily_prices.return_value = {h: 8.0 for h in range(24)}
        mock_price_provider.last_source = "mock"

        mock_tessie = MagicMock()
        mock_state = MagicMock()
        mock_state.battery_pct = 80.0
        mock_state.is_plugged_in = True
        mock_state.is_charging = False
        mock_state.charge_limit_pct = 90.0
        mock_tessie.get_state.return_value = mock_state

        with patch("auto_charge.prices.PriceProvider", return_value=mock_price_provider), \
             patch("auto_charge.debug_tessie.DebugTessieClient", return_value=mock_tessie), \
             patch("auto_charge.utils.now_spain",
                   return_value=datetime(2026, 6, 19, 9, 0, 0, tzinfo=timezone(timedelta(hours=2)))):
            # Should not crash
            run_once(cfg, debug=True, dry_run=False, initial_battery=80.0)


# =============================================================================
# _kill_existing_instances edge cases
# =============================================================================

class TestKillInstancesDetail:
    """Cover _kill_existing_instances remaining edge cases."""

    def test_kill_with_general_exception(self):
        """Generic exception during kill → caught."""
        with patch("os.getpid", return_value=1000), \
             patch("subprocess.run", side_effect=Exception("Unexpected error")):
            _kill_existing_instances()  # Should not raise

    def test_kill_with_permission_error(self):
        """PermissionError during kill → caught."""
        with patch("os.getpid", return_value=1000), \
             patch("subprocess.run") as mock_run, \
             patch("os.kill", side_effect=[None, PermissionError("No permission")]), \
             patch("time.sleep"):
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "2000\n"
            mock_run.return_value = mock_result

            _kill_existing_instances()  # Should not raise

    def test_kill_empty_stdout(self):
        """pgrep returns empty string → no crash."""
        with patch("os.getpid", return_value=1000), \
             patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = ""
            mock_run.return_value = mock_result

            _kill_existing_instances()  # Should not raise


# =============================================================================
# _daemonize edge cases
# =============================================================================

class TestDaemonizeEdge:
    """Cover _daemonize remaining branches."""

    def test_daemonize_os_error(self):
        """OSError during daemonize → caught, runs in foreground."""
        with patch("os.fork", side_effect=OSError("Resource temporarily unavailable")):
            _daemonize()  # Should not raise


# =============================================================================
# _build_monitor_status_fn edge cases
# =============================================================================

class TestMonitorStatus:
    """Cover _build_monitor_status_fn with prices."""

    def test_with_all_data(self):
        """Full status with vehicle, plan, and prices."""
        config = MagicMock()
        config.target_time = "19:00"

        daemon = MagicMock()
        daemon.tessie.get_state.return_value = None
        daemon.current_plan = None
        daemon.prices = {h: 8.0 for h in range(24)}

        get_status = _build_monitor_status_fn(config, daemon)
        status = get_status()
        assert "min=8.0" in status["prices_summary"]

    def test_without_prices(self):
        """Empty prices → empty prices_summary."""
        config = MagicMock()
        config.target_time = "19:00"

        daemon = MagicMock()
        daemon.tessie.get_state.return_value = None
        daemon.current_plan = None
        daemon.prices = {}

        get_status = _build_monitor_status_fn(config, daemon)
        status = get_status()
        assert status["prices_summary"] == ""


# =============================================================================
# main() dispatch edge cases
# =============================================================================

def _make_args(**kwargs):
    """Build a minimal argparse.Namespace for parse_args mocking."""
    defaults = dict(
        once=False, debug=False, dry_run=False, init=False, show_config=False,
        edit=False, prices=False, dashboard=False, background=False,
        version=False, verbose=False, config="/tmp/.autocharge-test.json",
        initial_battery=35.0, lang="es",
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestMainDispatch:
    """Cover main() entry point dispatch."""

    def test_main_version_no_exit(self):
        """--version prints and returns (doesn't sys.exit)."""
        with patch("tesla_pvpc.parse_args", return_value=_make_args(version=True)), \
             patch("builtins.print") as mock_print:
            main()
        mock_print.assert_called()

    def test_main_show_config(self):
        """--show-config calls show_config."""
        with patch("tesla_pvpc.parse_args", return_value=_make_args(show_config=True)), \
             patch("tesla_pvpc.show_config") as mock_fn:
            main()
        mock_fn.assert_called_once()

    def test_main_prices(self):
        """--prices calls show_prices."""
        with patch("tesla_pvpc.parse_args", return_value=_make_args(prices=True)), \
             patch("tesla_pvpc.show_prices") as mock_fn:
            main()
        mock_fn.assert_called_once()

    def test_main_init(self):
        """--init calls run_interactive_init."""
        with patch.dict("sys.modules", {"questionary": MagicMock()}), \
             patch("tesla_pvpc.parse_args", return_value=_make_args(init=True)), \
             patch("auto_charge.interactive.run_interactive_init") as mock_fn:
            main()
        mock_fn.assert_called_once()

    def test_main_edit(self):
        """--edit flag triggers interactive edit via main()."""
        with patch.dict("sys.modules", {"questionary": MagicMock()}):
            import auto_charge.interactive
            with patch.object(auto_charge.interactive, "run_interactive_edit") as mock_fn, \
                 patch("tesla_pvpc.parse_args", return_value=_make_args(edit=True)):
                main()
            mock_fn.assert_called_once()

    def test_main_verbose_sets_debug_level(self):
        """--verbose flag triggers setup_logger(level=10)."""
        with patch("auto_charge.utils.setup_logger") as mock_setup, \
             patch("tesla_pvpc.parse_args", return_value=_make_args(verbose=True)), \
             patch("tesla_pvpc._kill_existing_instances"), \
             patch("tesla_pvpc.AutoChargeDaemon") as mock_daemon_cls, \
             patch("auto_charge.config.Config") as mock_cfg_cls, \
             patch.dict("sys.modules", {"questionary": MagicMock()}):
            mock_cfg = MagicMock()
            mock_cfg.debug_mode = False
            mock_cfg.tessie_token = ""
            mock_cfg_cls.return_value = mock_cfg
            mock_daemon = MagicMock()
            mock_daemon.run.side_effect = SystemExit("stop")
            mock_daemon_cls.return_value = mock_daemon
            with pytest.raises(SystemExit):
                main()
        mock_setup.assert_called_once_with(level=10)

    def test_main_once_with_config_custom(self):
        """--once with custom config path."""
        with patch("tesla_pvpc.parse_args",
                   return_value=_make_args(once=True, config="/tmp/custom.json")), \
             patch("auto_charge.config.Config") as mock_cfg, \
             patch("tesla_pvpc.run_once"), \
             patch("builtins.print"):
            main()
        mock_cfg.assert_called_once_with("/tmp/custom.json")

    def test_main_config_not_found_exits(self):
        """Missing config → exit."""
        with patch("tesla_pvpc.parse_args",
                   return_value=_make_args(once=True, config="/tmp/custom.json")), \
             patch("auto_charge.config.Config", side_effect=FileNotFoundError("no file")), \
             patch("builtins.print"):
            with pytest.raises(SystemExit):
                main()

    def test_main_config_other_error_exits(self):
        """Config other error → exit."""
        with patch("tesla_pvpc.parse_args",
                   return_value=_make_args(once=True, config="/tmp/custom.json")), \
             patch("auto_charge.config.Config", side_effect=Exception("Parsing error")), \
             patch("builtins.print"):
            with pytest.raises(SystemExit):
                main()

    def test_main_dry_run_with_debug(self):
        """--dry-run with --debug logs ignored message."""
        with patch("tesla_pvpc.parse_args",
                   return_value=_make_args(once=True, dry_run=True, debug=True)), \
             patch("auto_charge.config.Config") as mock_cfg_cls, \
             patch("tesla_pvpc.run_once") as mock_run:
            mock_cfg = MagicMock()
            mock_cfg.debug_mode = False
            mock_cfg.tessie_token = ""
            mock_cfg_cls.return_value = mock_cfg
            main()
        mock_run.assert_called_once()


# =============================================================================
# _format_slot_hours edge cases
# =============================================================================

class TestFormatSlotHoursEdge:
    """Cover _format_slot_hours edge cases."""

    def test_format_midnight_slot(self):
        """Slot ending at midnight (24)."""
        result = _format_slot_hours({"start": 22, "end": 24, "kwh": 5.0})
        assert "22:00" in result
        assert "kWh" in result

    def test_format_two_day_offset(self):
        """Slot with 48+ offset (day after tomorrow)."""
        result = _format_slot_hours({"start": 48, "end": 51, "kwh": 7.0})
        assert "+2d" in result


# =============================================================================
# show_dashboard without daemon
# =============================================================================

class TestShowDashboardEdge:
    """Cover show_dashboard when no daemon."""

    def test_dashboard_no_daemon_message(self):
        """No daemon → user-friendly message."""
        with patch("auto_charge.status.get_daemon_pid", return_value=None), \
             patch("builtins.print") as mock_print:
            show_dashboard()

        printed = [str(c[0][0]) for c in mock_print.call_args_list if c[0]]
        assert any("No hay ningún daemon" in p for p in printed)
