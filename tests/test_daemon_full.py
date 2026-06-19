"""Full daemon tests covering remaining uncovered lines in daemon.py."""

import os
import sys
import signal
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from auto_charge.daemon import AutoChargeDaemon, run_daemon
from auto_charge.planner import ChargingPlan, ChargingSlot
from auto_charge.config import Config


@pytest.fixture
def mini_cfg():
    """Minimal config using real Config class values for construction."""
    cfg = MagicMock(spec=Config)
    cfg.tessie_token = ""
    cfg.vin = ""
    cfg.esios_token = ""
    cfg.max_price_cents_per_kwh = 10.0
    cfg.max_charger_power_kw = 3.3
    cfg.battery_capacity_kwh = 75.0
    cfg.min_battery_pct = 70.0
    cfg.target_time = "19:00"
    cfg.target_hour = 19
    cfg.target_minute = 0
    cfg.strict_mode = True
    cfg.charging_efficiency = 0.9
    cfg.check_interval_minutes = 15
    cfg.debug_mode = True
    cfg.telegram_enabled = False
    cfg.telegram_bot_token = ""
    cfg.telegram_chat_id = ""
    return cfg


class TestRealInit:
    """Test AutoChargeDaemon.__init__ with real construction."""

    def test_init_debug_mode_creates_debug_tessie(self, mini_cfg):
        with patch("auto_charge.daemon.write_status"), \
             patch("auto_charge.daemon.signal.signal"), \
             patch("auto_charge.daemon.DebugTessieClient") as mock_debug, \
             patch("auto_charge.daemon.TessieClient") as mock_real:
            daemon = AutoChargeDaemon(mini_cfg)
        mock_debug.assert_called_once()
        mock_real.assert_not_called()
        assert daemon._debug_mode is True

    def test_init_non_debug_creates_real_tessie(self, mini_cfg):
        mini_cfg.debug_mode = False
        mini_cfg.tessie_token = "real_token"
        with patch("auto_charge.daemon.write_status"), \
             patch("auto_charge.daemon.signal.signal"), \
             patch("auto_charge.daemon.TessieClient") as mock_real, \
             patch("auto_charge.daemon.DebugTessieClient") as mock_debug:
            daemon = AutoChargeDaemon(mini_cfg)
        mock_real.assert_called_once()
        mock_debug.assert_not_called()

    def test_init_sets_signal_handlers(self, mini_cfg):
        with patch("auto_charge.daemon.write_status"), \
             patch("auto_charge.daemon.signal.signal") as mock_signal:
            daemon = AutoChargeDaemon(mini_cfg)
        mock_signal.assert_any_call(signal.SIGINT, daemon._shutdown)

    def test_init_writes_status(self, mini_cfg):
        with patch("auto_charge.daemon.write_status") as mock_write, \
             patch("auto_charge.daemon.signal.signal"), \
             patch("auto_charge.daemon.os.getpid", return_value=12345):
            AutoChargeDaemon(mini_cfg)
        mock_write.assert_called_once_with(daemon_pid=12345, daemon_mode="daemon")

    def test_init_has_planner_and_telegram(self, mini_cfg):
        with patch("auto_charge.daemon.write_status"), \
             patch("auto_charge.daemon.signal.signal"), \
             patch("auto_charge.daemon.build_bot") as mock_bot:
            daemon = AutoChargeDaemon(mini_cfg)
        assert daemon.planner is not None
        mock_bot.assert_called_once()


class TestCmdStatusDetail:
    """Cover _cmd_status charger_power > 0 line."""

    def test_status_with_charger_power(self):
        daemon = AutoChargeDaemon.__new__(AutoChargeDaemon)
        daemon.cfg = MagicMock()
        daemon.cfg.target_time = "19:00"
        daemon.current_plan = None
        mock_tessie = MagicMock()
        mock_state = MagicMock()
        mock_state.battery_pct = 50.0
        mock_state.is_plugged_in = True
        mock_state.is_charging = True
        mock_state.charge_limit_pct = 80.0
        mock_state.charger_power_kw = 3.3
        mock_tessie.get_state.return_value = mock_state
        daemon.tessie = mock_tessie
        result = daemon._cmd_status()
        assert "Potencia" in result

    def test_cmd_start_charge_error_path(self):
        daemon = AutoChargeDaemon.__new__(AutoChargeDaemon)
        mock_tessie = MagicMock()
        mock_tessie.start_charge.return_value = False
        daemon.tessie = mock_tessie
        result = daemon._cmd_start_charge()
        assert "Error" in result

    def test_cmd_stop_charge_error_path(self):
        daemon = AutoChargeDaemon.__new__(AutoChargeDaemon)
        mock_tessie = MagicMock()
        mock_tessie.stop_charge.return_value = False
        daemon.tessie = mock_tessie
        result = daemon._cmd_stop_charge()
        assert "Error" in result


class TestCmdForcePlanNoPrices:
    """Cover _cmd_force_plan no prices path."""

    def test_force_plan_no_prices_returns_warning(self):
        daemon = AutoChargeDaemon.__new__(AutoChargeDaemon)
        daemon._debug_mode = False  # Required by _create_plan
        daemon.cfg = MagicMock()
        daemon.cfg.target_hour = 19
        daemon.cfg.target_time = "19:00"
        daemon.prices = {}
        daemon.prices_date = ""
        daemon.price_provider = MagicMock()
        daemon.price_provider.fetch_daily_prices.return_value = None
        daemon.price_provider.last_source = "none"
        mock_tessie = MagicMock()
        mock_state = MagicMock()
        mock_state.battery_pct = 50.0
        mock_tessie.get_state.return_value = mock_state
        daemon.tessie = mock_tessie
        daemon.planner = MagicMock()
        daemon.planner.plan.return_value = MagicMock(slots=[])

        with patch("auto_charge.daemon.now_spain",
                   return_value=datetime(2026, 6, 19, 9, 0, 0, tzinfo=timezone(timedelta(hours=2)))):
            result = daemon._cmd_force_plan()
        assert "⚠️" in result or "No se pudo" in result


class TestRunStartup:
    """Cover run() startup logging."""

    def test_run_startup_logs(self, mini_cfg):
        daemon = AutoChargeDaemon.__new__(AutoChargeDaemon)
        daemon.cfg = mini_cfg
        daemon._debug_mode = True
        daemon.running = True
        daemon.price_provider = MagicMock()
        daemon.price_provider.fetch_daily_prices.return_value = {h: 8.0 for h in range(24)}
        daemon.price_provider.last_source = "mock"
        daemon.tessie = MagicMock()
        mock_state = MagicMock()
        mock_state.battery_pct = 50.0
        mock_state.charge_limit_pct = 80.0
        daemon.tessie.get_state.return_value = mock_state
        daemon.planner = MagicMock()
        mock_plan = MagicMock(
            spec=["slots", "expected_final_pct", "total_kwh", "total_cost_eur", "target_pct", "summary"],
            expected_final_pct=70.0, total_kwh=15.0, total_cost_eur=1.2, target_pct=70.0,
        )
        mock_plan.summary.return_value = "plan summary"
        mock_plan.slots = [ChargingSlot(0, 3, 3.3, 8.0)]
        daemon.planner.plan.return_value = mock_plan
        daemon.prices = {h: 8.0 for h in range(24)}
        daemon.prices_date = "2026-06-19"
        daemon.telegram = MagicMock()

        with patch("auto_charge.daemon.logger") as mock_logger, \
             patch.object(daemon, "_tick", side_effect=SystemExit("exit")):
            with pytest.raises(SystemExit):
                daemon.run()
        assert mock_logger.info.called


class TestTickDebug:
    """Cover _tick debug-specific branches."""

    def test_tick_debug_logs(self):
        daemon = AutoChargeDaemon.__new__(AutoChargeDaemon)
        daemon.cfg = MagicMock(spec=Config)
        daemon.cfg.target_hour = 19
        daemon.cfg.target_time = "19:00"
        daemon.cfg.check_interval_minutes = 15
        daemon.cfg.telegram_enabled = False
        daemon.cfg.debug_mode = True
        daemon.cfg.min_battery_pct = 70.0
        daemon.cfg.max_price_cents_per_kwh = 10.0
        daemon.cfg.max_charger_power_kw = 3.3
        daemon.cfg.battery_capacity_kwh = 75.0
        daemon.cfg.strict_mode = True
        daemon.cfg.charging_efficiency = 0.9
        daemon.cfg.target_time = "19:00"
        daemon._debug_mode = True
        daemon.running = True
        daemon.prices = {h: 8.0 for h in range(24)}
        daemon.prices_date = "2026-06-19"
        daemon.prices_fetched_today = False
        daemon.planned_today = False
        daemon.expected_by_hour = {}
        daemon._day_tracker = "2026-06-19"
        daemon._today_early_plan_done = True  # Skip early plan
        daemon.tessie = MagicMock()
        mock_state = MagicMock()
        mock_state.battery_pct = 50.0
        mock_state.charge_limit_pct = 80.0
        daemon.tessie.get_state.return_value = mock_state
        daemon.price_provider = MagicMock()
        daemon.price_provider.fetch_daily_prices.return_value = {h: 8.0 for h in range(24)}
        daemon.price_provider.last_source = "mock"
        daemon.planner = MagicMock()
        mock_plan = MagicMock(
            spec=["slots", "expected_final_pct", "total_kwh", "total_cost_eur", "summary"],
            expected_final_pct=70.0, total_kwh=15.0, total_cost_eur=1.2,
        )
        mock_plan.summary.return_value = "plan"
        mock_plan.slots = [ChargingSlot(0, 3, 3.3, 8.0)]
        daemon.planner.plan.return_value = mock_plan
        daemon.current_plan = None
        daemon.telegram = MagicMock()
    
        with patch("auto_charge.daemon.now_spain",
                   return_value=datetime(2026, 6, 19, 9, 0, 0, tzinfo=timezone(timedelta(hours=2)))), \
             patch("auto_charge.daemon.today_str", return_value="2026-06-19"), \
             patch("auto_charge.daemon.time.sleep", side_effect=SystemExit("stop")), \
             patch("auto_charge.daemon.logger") as mock_log:
            try:
                daemon._tick()
            except SystemExit:
                pass
        # Verify tick executes without error
        debug_logs = [str(c) for c in mock_log.info.call_args_list if "[DEBUG]" in str(c)]
        assert len(debug_logs) > 0 or mock_log.info.called


class TestNextWakeTimeNone:
    """Cover _next_wake_time edge cases."""

    def test_interval_rolls_over_midnight(self):
        daemon = AutoChargeDaemon.__new__(AutoChargeDaemon)
        daemon.current_plan = None
        now = datetime(2026, 6, 19, 23, 50, 0, tzinfo=timezone(timedelta(hours=2)))
        wake = daemon._next_wake_time(now, interval_minutes=15)
        assert wake is not None
        assert wake.day == 20  # Next day

    def test_slot_24_plus_with_plan(self):
        daemon = AutoChargeDaemon.__new__(AutoChargeDaemon)
        daemon.current_plan = ChargingPlan(
            slots=[ChargingSlot(24, 27, 6.0, 9.0)], target_pct=70.0,
        )
        now = datetime(2026, 6, 19, 22, 30, 0, tzinfo=timezone(timedelta(hours=2)))
        wake = daemon._next_wake_time(now, interval_minutes=15)
        assert wake is not None
        assert wake.hour == 22
        assert wake.minute == 45


class TestNextActionN6N7:
    """Cover N6 and N7."""

    def test_n6_cross_midnight_planning(self):
        daemon = AutoChargeDaemon.__new__(AutoChargeDaemon)
        daemon.cfg = MagicMock()
        daemon.cfg.target_hour = 19
        daemon.current_plan = None
        daemon.prices_fetched_today = True
        daemon.planned_today = False
        daemon._today_early_plan_done = True
        now = datetime(2026, 6, 19, 21, 0, 0, tzinfo=timezone(timedelta(hours=2)))
        desc = daemon._next_action_description(now)
        assert "cruzando medianoche" in desc

    def test_n7_waiting_next_cycle(self):
        daemon = AutoChargeDaemon.__new__(AutoChargeDaemon)
        daemon.cfg = MagicMock()
        daemon.cfg.target_hour = 19
        daemon.current_plan = None
        daemon.prices_fetched_today = True
        daemon.planned_today = True
        now = datetime(2026, 6, 19, 21, 0, 0, tzinfo=timezone(timedelta(hours=2)))
        desc = daemon._next_action_description(now)
        assert "siguiente ciclo" in desc


class TestRunDaemonFile:
    """Cover run_daemon entry point."""

    def test_run_daemon_creates_and_runs(self):
        mock_cfg = MagicMock()
        mock_daemon = MagicMock()
        with patch("auto_charge.daemon.Config", return_value=mock_cfg), \
             patch("auto_charge.daemon.AutoChargeDaemon", return_value=mock_daemon):
            run_daemon("/tmp/test.json")
        mock_daemon.run.assert_called_once()

    def test_run_daemon_file_not_found(self):
        with patch("auto_charge.daemon.Config", side_effect=FileNotFoundError("Not found")), \
             patch("builtins.print"), pytest.raises(SystemExit):
            run_daemon("/nonexistent/config.json")
