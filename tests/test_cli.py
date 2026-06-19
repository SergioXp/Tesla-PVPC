"""Tests for CLI entry point (tesla_pvpc.py) with mocked dependencies.

Covers:
- parse_args: all flags and combinations
- show_prices: with/without daemon, direct fetch, empty data
- _print_prices_table: formatting edge cases
- _format_slot_hours: 24+ offsets
- show_config: with/without config file
- show_dashboard: with/without running daemon
- _kill_existing_instances: pgrep success, failure, edge cases
- _daemonize: fork behavior
- _build_monitor_status_fn: callback factory
"""

import os
import sys
import signal
import subprocess
import json
import tempfile
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, PropertyMock, call
from typing import Dict, Any
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Import ALL functions directly from tesla_pvpc
from tesla_pvpc import (
    parse_args,
    show_prices,
    _print_prices_table,
    _format_slot_hours,
    show_config,
    show_dashboard,
    _kill_existing_instances,
    _daemonize,
    _build_monitor_status_fn,
    main,
)


# =============================================================================
# parse_args tests
# =============================================================================

class TestParseArgs:
    """Test argument parsing for all flags."""

    def test_defaults(self):
        with patch("sys.argv", ["tesla_pvpc.py"]):
            args = parse_args()
        assert args.once is False
        assert args.debug is False
        assert args.prices is False
        assert args.dashboard is False
        assert args.version is False
        assert args.lang == "es"

    def test_once_flag(self):
        with patch("sys.argv", ["tesla_pvpc.py", "--once"]):
            args = parse_args()
        assert args.once is True

    def test_debug_flag(self):
        with patch("sys.argv", ["tesla_pvpc.py", "--debug"]):
            args = parse_args()
        assert args.debug is True

    def test_verbose_flag(self):
        with patch("sys.argv", ["tesla_pvpc.py", "--verbose"]):
            args = parse_args()
        assert args.verbose is True

    def test_dry_run_flag(self):
        with patch("sys.argv", ["tesla_pvpc.py", "--dry-run"]):
            args = parse_args()
        assert args.dry_run is True

    def test_background_flag(self):
        with patch("sys.argv", ["tesla_pvpc.py", "--background"]):
            args = parse_args()
        assert args.background is True

    def test_prices_flag(self):
        with patch("sys.argv", ["tesla_pvpc.py", "--prices"]):
            args = parse_args()
        assert args.prices is True

    def test_dashboard_flag(self):
        with patch("sys.argv", ["tesla_pvpc.py", "--dashboard"]):
            args = parse_args()
        assert args.dashboard is True

    def test_version_flag(self):
        with patch("sys.argv", ["tesla_pvpc.py", "--version"]):
            args = parse_args()
        assert args.version is True

    def test_init_flag(self):
        with patch("sys.argv", ["tesla_pvpc.py", "--init"]):
            args = parse_args()
        assert args.init is True

    def test_show_config_flag(self):
        with patch("sys.argv", ["tesla_pvpc.py", "--show-config"]):
            args = parse_args()
        assert args.show_config is True

    def test_edit_flag(self):
        with patch("sys.argv", ["tesla_pvpc.py", "--edit"]):
            args = parse_args()
        assert args.edit is True

    def test_initial_battery(self):
        with patch("sys.argv", ["tesla_pvpc.py", "--initial-battery", "50"]):
            args = parse_args()
        assert args.initial_battery == 50.0

    def test_config_path(self):
        with patch("sys.argv", ["tesla_pvpc.py", "--config", "/tmp/custom.json"]):
            args = parse_args()
        assert args.config == "/tmp/custom.json"

    def test_lang_flag(self):
        with patch("sys.argv", ["tesla_pvpc.py", "--lang", "en"]):
            args = parse_args()
        assert args.lang == "en"


# =============================================================================
# show_prices tests
# =============================================================================

class TestShowPrices:
    """Test show_prices with various data sources."""

    def test_show_prices_from_daemon_status(self):
        """--prices reads from daemon status file."""
        with patch("auto_charge.status.read_status") as mock_read, \
             patch("tesla_pvpc._print_prices_table") as mock_print:
            mock_read.return_value = {
                "prices": {str(h): float(8.0 + h) for h in range(24)},
                "prices_date": "2026-06-19",
                "config": {"max_price": 10.0},
            }
            with patch("auto_charge.status.status_age_seconds", return_value=30):
                show_prices()
        mock_print.assert_called()

    def test_show_prices_daemon_empty_fetches_directly(self):
        """Daemon status empty → fetches directly."""
        with patch("auto_charge.status.read_status", return_value={}), \
             patch("auto_charge.config.Config") as mock_cfg_cls, \
             patch("auto_charge.prices.PriceProvider") as mock_pp_cls, \
             patch("tesla_pvpc._print_prices_table") as mock_print:
            mock_cfg = MagicMock()
            mock_cfg.max_price_cents_per_kwh = 10.0
            mock_cfg_cls.return_value = mock_cfg
            mock_pp = MagicMock()
            mock_pp.fetch_daily_prices.return_value = {h: 10.0 for h in range(24)}
            mock_pp.last_source = "esios"
            mock_pp_cls.return_value = mock_pp
            show_prices()
        mock_print.assert_called_once()

    def test_show_prices_direct_fetch_fails_prints_error(self):
        """Direct fetch fails → error message."""
        with patch("auto_charge.status.read_status", return_value={}), \
             patch("auto_charge.config.Config") as mock_cfg_cls, \
             patch("auto_charge.prices.PriceProvider") as mock_pp_cls, \
             patch("builtins.print") as mock_print:
            mock_cfg = MagicMock()
            mock_cfg.max_price_cents_per_kwh = 10.0
            mock_cfg_cls.return_value = mock_cfg
            mock_pp = MagicMock()
            mock_pp.fetch_daily_prices.return_value = None
            mock_pp_cls.return_value = mock_pp
            show_prices()
        printed = [str(c[0][0]) for c in mock_print.call_args_list if c[0]]
        any_error = any("No se pudieron obtener" in p for p in printed)
        assert any_error

    def test_show_prices_try_except_works(self):
        """Exception in direct fetch path handled."""
        with patch("auto_charge.status.read_status", return_value={}), \
             patch("auto_charge.config.Config", side_effect=Exception("Config failed")), \
             patch("builtins.print") as mock_print:
            show_prices()
        printed = [str(c[0][0]) for c in mock_print.call_args_list if c[0]]
        any_error = any("Error" in p for p in printed)
        assert any_error


# =============================================================================
# _print_prices_table tests
# =============================================================================

class TestPrintPricesTable:
    """Test price table formatting."""

    def test_prints_all_hours(self):
        prices = {h: 8.0 + (h % 5) for h in range(24)}
        with patch("builtins.print") as mock_print:
            _print_prices_table(prices, "2026-06-19", "esios", max_price_limit=10.0)
        printed = [str(c[0][0]) for c in mock_print.call_args_list if c[0]]
        full = "\n".join(printed)
        assert "Precios de la luz" in full
        assert "2026-06-19" in full
        assert "esios" in full

    def test_without_max_price(self):
        prices = {h: 8.0 for h in range(24)}
        with patch("builtins.print") as mock_print:
            _print_prices_table(prices, "2026-06-19", "redata", max_price_limit=None)
        printed = [str(c[0][0]) for c in mock_print.call_args_list if c[0]]
        assert not any("Límite" in p for p in printed)

    def test_with_age(self):
        prices = {h: 8.0 for h in range(24)}
        with patch("builtins.print") as mock_print:
            _print_prices_table(prices, "2026-06-19", "daemon", age=45)
        printed = [str(c[0][0]) for c in mock_print.call_args_list if c[0]]
        assert any("45s" in p for p in printed)

    def test_marks_cheapest_and_most_expensive(self):
        prices = {h: float(10) for h in range(24)}
        prices[3] = 5.0
        prices[15] = 20.0
        with patch("builtins.print") as mock_print:
            _print_prices_table(prices, "2026-06-19", "test", max_price_limit=12.0)
        printed = [str(c[0][0]) for c in mock_print.call_args_list if c[0]]
        full = "\n".join(printed)
        assert "MÍN" in full
        assert "MÁX" in full

    def test_single_price_value(self):
        prices = {h: 8.0 for h in range(24)}
        with patch("builtins.print") as mock_print:
            _print_prices_table(prices, "2026-06-19", "test")
        printed = [str(c[0][0]) for c in mock_print.call_args_list if c[0]]
        assert len(printed) > 0


# =============================================================================
# _format_slot_hours tests
# =============================================================================

class TestFormatSlotHours:
    """Test slot formatting from dict."""

    def test_today_slot(self):
        result = _format_slot_hours({"start": 10, "end": 13, "kwh": 9.0})
        assert "10:00" in result and "13:00" in result and "9.0" in result

    def test_tomorrow_slot(self):
        result = _format_slot_hours({"start": 24, "end": 27, "kwh": 6.0})
        assert "+1d 00:00" in result
        assert "+1d 03:00" in result

    def test_cross_midnight_slot(self):
        result = _format_slot_hours({"start": 21, "end": 24, "kwh": 9.9})
        assert "21:00" in result


# =============================================================================
# show_config tests
# =============================================================================

class TestShowConfig:
    """Test show_config with various states."""

    def test_show_config_with_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"max_price_cents_per_kwh": 10, "target_time": "19:00", "strict_mode": True}, f)
            f.flush()
            with patch("builtins.print"):
                show_config(f.name)
            os.unlink(f.name)

    def test_show_config_prints_sections(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "max_price_cents_per_kwh": 10, "target_time": "19:00", "strict_mode": True,
                "charging_efficiency": 0.9, "check_interval_minutes": 15,
                "max_charger_power_kw": 3.3, "battery_capacity_kwh": 75,
            }, f)
            f.flush()
            with patch("builtins.print") as mock_print:
                show_config(f.name)
            printed = [str(c[0][0]) for c in mock_print.call_args_list if c[0]]
            full = "\n".join(printed)
            assert "max_price_cents_per_kwh" in full
            assert "target_time" in full
            os.unlink(f.name)

    def test_show_config_no_file(self):
        with patch("os.path.exists", return_value=False), \
             patch("builtins.print") as mock_print:
            show_config("/nonexistent/config.json")
        printed = [str(c[0][0]) for c in mock_print.call_args_list if c[0]]
        # Should show a message about no config (may include i18n keys or formatted text)
        assert len(printed) > 0, "Should print something"
        any_info = any("config" in p.lower() or "init" in p.lower() or "⚠" in p or "no" in p.lower() for p in printed)
        assert any_info, "Should mention missing config"


# =============================================================================
# show_dashboard tests
# =============================================================================

class TestShowDashboard:
    """Test show_dashboard behavior."""

    def test_dashboard_no_daemon(self):
        with patch("auto_charge.status.get_daemon_pid", return_value=None), \
             patch("builtins.print") as mock_print:
            show_dashboard()
        printed = [str(c[0][0]) for c in mock_print.call_args_list if c[0]]
        assert any("No hay ningún daemon" in p for p in printed)

    @pytest.mark.skipif(not os.path.exists(
        os.path.join(PROJECT_ROOT, "auto_charge", "interactive.py")),
        reason="interactive.py requires questionary which may not be installed")
    def test_dashboard_with_daemon(self):
        """Test that show_dashboard reads status and calls live_monitor."""
        import importlib
        # Ensure interactive is importable by mocking questionary first
        mock_questionary = MagicMock()
        with patch.dict(sys.modules, {"questionary": mock_questionary}), \
             patch("auto_charge.status.get_daemon_pid", return_value=12345), \
             patch("auto_charge.status.read_status") as mock_read, \
             patch("auto_charge.interactive.live_monitor") as mock_monitor:
            mock_read.return_value = {
                "vehicle": {"battery_pct": 55.0, "is_charging": True},
                "plan": {"target_pct": 70, "slots": [], "total_cost_eur": 1.2},
                "prices_summary": {"min": 8.0, "max": 15.0, "avg": 10.0},
                "config": {"target_time": "19:00"},
                "daemon_mode": "daemon",
            }
            with patch("auto_charge.status.status_age_seconds", return_value=5):
                show_dashboard()
        mock_monitor.assert_called_once()


# =============================================================================
# _kill_existing_instances tests
# =============================================================================

class TestKillExistingInstances:
    """Test killing previous instances."""

    def test_no_other_instances(self):
        with patch("os.getpid", return_value=1000), \
             patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_run.return_value = mock_result
            _kill_existing_instances()

    def test_kill_other_instance(self):
        with patch("os.getpid", return_value=1000), \
             patch("subprocess.run") as mock_run, \
             patch("os.kill") as mock_kill, \
             patch("time.sleep"):
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "2000\n"
            mock_run.return_value = mock_result
            _kill_existing_instances()
        mock_kill.assert_any_call(2000, signal.SIGTERM)

    def test_pgrep_not_found(self):
        with patch("os.getpid", return_value=1000), \
             patch("subprocess.run", side_effect=FileNotFoundError("pgrep")):
            _kill_existing_instances()

    def test_pgrep_timeout(self):
        with patch("os.getpid", return_value=1000), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("pgrep", 5)):
            _kill_existing_instances()


# =============================================================================
# _daemonize tests
# =============================================================================

class TestDaemonize:
    """Test daemonization (fork to background)."""

    def test_daemonize_parent_exits(self):
        with patch("os.fork") as mock_fork, \
             patch("os.setsid"), \
             patch("sys.exit") as mock_exit:
            mock_fork.return_value = 100  # Parent
            _daemonize()
        mock_exit.assert_called_with(0)

    def test_daemonize_windows(self):
        with patch("os.fork", side_effect=AttributeError("No fork")):
            _daemonize()


# =============================================================================
# _build_monitor_status_fn tests
# =============================================================================

class TestBuildMonitorStatusFn:
    """Test the monitor status callback factory."""

    def test_returns_status_dict(self):
        config = MagicMock()
        config.target_time = "19:00"
        daemon = MagicMock()
        daemon.tessie.get_state.return_value = None
        daemon.current_plan = None
        daemon.prices = {h: 8.0 for h in range(24)}

        get_status = _build_monitor_status_fn(config, daemon)
        status = get_status()
        assert "vehicle" in status
        assert "plan" in status
        assert "prices_summary" in status

    def test_with_plan_data(self):
        config = MagicMock()
        config.target_time = "19:00"
        plan = MagicMock()
        plan.target_pct = 70
        plan.expected_final_pct = 85.0
        plan.total_cost_eur = 1.5
        plan.slots = [MagicMock()]  # Non-empty slots
        str(plan.slots[0])  # Make it str-able
        plan.slots[0].__str__.return_value = "Slot(10:00-13:00, 8.0c/kWh, 9.0kWh)"

        daemon = MagicMock()
        daemon.current_plan = plan
        daemon.prices = {h: 8.0 for h in range(24)}

        get_status = _build_monitor_status_fn(config, daemon)
        status = get_status()
        assert status["plan"]["target_pct"] == 70
        assert status["plan"]["expected_pct"] == 85.0

    def test_without_prices(self):
        """When daemon.prices is empty, prices_summary is empty string."""
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
# main() entry point tests
# =============================================================================

class TestMain:
    """Test main() CLI dispatch."""

    def test_version_flag_prints(self):
        """--version prints version and returns."""
        with patch("sys.argv", ["tesla_pvpc.py", "--version"]), \
             patch("builtins.print") as mock_print:
            main()
        assert mock_print.called

    def test_show_config_flag(self):
        """--show-config → calls show_config."""
        with patch("sys.argv", ["tesla_pvpc.py", "--show-config"]), \
             patch("tesla_pvpc.show_config") as mock_show:
            main()
        mock_show.assert_called_once()

    def test_prices_flag(self):
        """--prices → calls show_prices."""
        with patch("sys.argv", ["tesla_pvpc.py", "--prices"]), \
             patch("tesla_pvpc.show_prices") as mock_show:
            main()
        mock_show.assert_called_once()

    def test_config_not_found_exits(self):
        """Missing config file → exit."""
        with patch("sys.argv", ["tesla_pvpc.py", "--once"]), \
             patch("auto_charge.config.Config", side_effect=FileNotFoundError("Missing")), \
             patch("builtins.print"), \
             pytest.raises(SystemExit):
            main()
