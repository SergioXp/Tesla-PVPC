"""Tests for Telegram bot with mocked HTTP requests.

Covers:
- TelegramBot: send_message success/failure
- poll(): update fetching, command dispatch
- _get_updates(): success, empty, failure
- _send_to(): send to specific chat
- build_bot(): command wiring
- Config info and help text
- Network error handling
"""

import os
import sys
import json
from unittest.mock import MagicMock, patch, call
from typing import Dict, Any
import pytest
import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from auto_charge.telegram_bot import (
    TelegramBot,
    build_bot,
    _help_text,
    _config_info,
    TELEGRAM_API,
)


def _make_config(bot_token="bot123", chat_id="chat456", enabled=True):
    """Create a mock config for TelegramBot."""
    cfg = MagicMock()
    cfg.telegram_bot_token = bot_token
    cfg.telegram_chat_id = chat_id
    cfg.telegram_enabled = enabled
    cfg.max_price_cents_per_kwh = 10.0
    cfg.max_charger_power_kw = 3.3
    cfg.battery_capacity_kwh = 75.0
    cfg.target_time = "19:00"
    cfg.strict_mode = True
    cfg.charging_efficiency = 0.9
    cfg.check_interval_minutes = 15
    cfg.min_battery_pct = 70.0
    cfg.tessie_token = ""
    cfg.esios_token = ""
    cfg.vin = ""
    return cfg


# =============================================================================
# TelegramBot initialization
# =============================================================================

class TestTelegramBotInit:
    """Test TelegramBot construction and basic properties."""

    def test_enabled(self):
        """Enabled bot returns enabled=True."""
        bot = TelegramBot(_make_config())
        assert bot.enabled is True

    def test_disabled(self):
        """Disabled bot returns enabled=False."""
        bot = TelegramBot(_make_config(enabled=False))
        assert bot.enabled is False

    def test_no_token_disabled(self):
        """Bot with no token but enabled flag → enabled but no token."""
        cfg = _make_config(bot_token="", chat_id="")
        cfg.telegram_enabled = False
        bot = TelegramBot(cfg)
        assert bot.enabled is False


# =============================================================================
# send_message tests
# =============================================================================

class TestSendMessage:
    """Test TelegramBot.send_message() with mocked HTTP."""

    def test_send_disabled_returns_false(self):
        """Disabled bot → send_message returns False."""
        bot = TelegramBot(_make_config(enabled=False))
        result = bot.send_message("Hello")
        assert result is False

    def test_send_success(self):
        """Successful send → returns True."""
        bot = TelegramBot(_make_config())

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("auto_charge.telegram_bot.requests.post", return_value=mock_resp):
            result = bot.send_message("Hello world")

        assert result is True

    def test_send_failure_http(self):
        """HTTP error → returns False."""
        bot = TelegramBot(_make_config())

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad Request"

        with patch("auto_charge.telegram_bot.requests.post", return_value=mock_resp):
            result = bot.send_message("Hello")

        assert result is False

    def test_send_network_error(self):
        """Network error → returns False."""
        bot = TelegramBot(_make_config())

        with patch("auto_charge.telegram_bot.requests.post",
                   side_effect=requests.RequestException("Connection failed")):
            result = bot.send_message("Hello")

        assert result is False

    def test_send_correct_payload(self):
        """send_message sends correct payload to Telegram API."""
        bot = TelegramBot(_make_config())
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("auto_charge.telegram_bot.requests.post", return_value=mock_resp) as mock_post:
            bot.send_message("Test msg with **markdown**")

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args.kwargs
        assert "json" in call_kwargs
        assert call_kwargs["json"]["chat_id"] == "chat456"
        assert call_kwargs["json"]["text"] == "Test msg with **markdown**"
        assert call_kwargs["json"]["parse_mode"] == "Markdown"
        assert call_kwargs["timeout"] == 10


# =============================================================================
# poll tests
# =============================================================================

class TestPoll:
    """Test TelegramBot.poll() with mocked HTTP."""

    def test_poll_disabled_returns(self):
        """Disabled bot → poll does nothing."""
        bot = TelegramBot(_make_config(enabled=False))
        # Should not crash
        bot.poll()

    def test_poll_no_updates(self):
        """poll() with no updates → no handler called."""
        bot = TelegramBot(_make_config())
        mock_handler = MagicMock()
        bot.register_command("status", mock_handler)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True, "result": []}

        with patch("auto_charge.telegram_bot.requests.get", return_value=mock_resp):
            bot.poll()

        mock_handler.assert_not_called()

    def test_poll_with_command(self):
        """poll() dispatches registered command."""
        bot = TelegramBot(_make_config())
        mock_handler = MagicMock(return_value="Status OK")
        bot.register_command("status", mock_handler)

        # Simulate an update with /status command
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ok": True,
            "result": [
                {
                    "update_id": 1001,
                    "message": {
                        "text": "/status",
                        "chat": {"id": "chat456"},
                    },
                }
            ]
        }

        with patch("auto_charge.telegram_bot.requests.get", return_value=mock_resp), \
             patch("auto_charge.telegram_bot.requests.post") as mock_post:
            bot.poll()

        mock_handler.assert_called_once_with("chat456", "")
        # Should send response
        mock_post.assert_called()

    def test_poll_with_unknown_command(self):
        """Unknown command → sends error message."""
        bot = TelegramBot(_make_config())

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ok": True,
            "result": [
                {
                    "update_id": 1002,
                    "message": {
                        "text": "/unknown_cmd",
                        "chat": {"id": "chat456"},
                    },
                }
            ]
        }

        with patch("auto_charge.telegram_bot.requests.get", return_value=mock_resp), \
             patch("auto_charge.telegram_bot.requests.post") as mock_post:
            bot.poll()

        # Should send error about unknown command
        mock_post.assert_called()
        call_kwargs = mock_post.call_args.kwargs
        assert "Comando desconocido" in call_kwargs["json"]["text"]

    def test_poll_with_args(self):
        """Command with arguments → args passed to handler."""
        bot = TelegramBot(_make_config())
        mock_handler = MagicMock(return_value="Done")
        bot.register_command("set", mock_handler)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ok": True,
            "result": [
                {
                    "update_id": 1003,
                    "message": {
                        "text": "/set max_price 8",
                        "chat": {"id": "chat456"},
                    },
                }
            ]
        }

        with patch("auto_charge.telegram_bot.requests.get", return_value=mock_resp), \
             patch("auto_charge.telegram_bot.requests.post"):
            bot.poll()

        mock_handler.assert_called_once_with("chat456", "max_price 8")

    def test_poll_handler_raises_error(self):
        """Handler that raises error → sends error message."""
        bot = TelegramBot(_make_config())
        def failing_handler(cid, args):
            raise ValueError("Something broke")
        bot.register_command("plan", failing_handler)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ok": True,
            "result": [
                {
                    "update_id": 1004,
                    "message": {
                        "text": "/plan",
                        "chat": {"id": "chat456"},
                    },
                }
            ]
        }

        with patch("auto_charge.telegram_bot.requests.get", return_value=mock_resp), \
             patch("auto_charge.telegram_bot.requests.post") as mock_post:
            bot.poll()

        # Should send error message
        mock_post.assert_called()
        call_kwargs = mock_post.call_args.kwargs
        assert "Error" in call_kwargs["json"]["text"]

    def test_poll_non_command_ignored(self):
        """Non-command message (no /) → ignored."""
        bot = TelegramBot(_make_config())
        mock_handler = MagicMock()
        bot.register_command("status", mock_handler)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ok": True,
            "result": [
                {
                    "update_id": 1005,
                    "message": {
                        "text": "just a regular message",
                        "chat": {"id": "chat456"},
                    },
                }
            ]
        }

        with patch("auto_charge.telegram_bot.requests.get", return_value=mock_resp):
            bot.poll()

        mock_handler.assert_not_called()


# =============================================================================
# _get_updates and _send_to error handling
# =============================================================================

class TestInternalMethods:
    """Test TelegramBot internal methods."""

    def test_get_updates_http_error(self):
        """_get_updates HTTP error → empty list."""
        bot = TelegramBot(_make_config())

        with patch("auto_charge.telegram_bot.requests.get",
                   side_effect=requests.RequestException("Timeout")):
            updates = bot._get_updates()

        assert updates == []

    def test_get_updates_not_ok(self):
        """_get_updates returns ok=False → empty list."""
        bot = TelegramBot(_make_config())

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": False, "result": []}

        with patch("auto_charge.telegram_bot.requests.get", return_value=mock_resp):
            updates = bot._get_updates()

        assert updates == []

    def test_send_to_http_error(self):
        """_send_to HTTP error → doesn't raise."""
        bot = TelegramBot(_make_config())

        with patch("auto_charge.telegram_bot.requests.post",
                   side_effect=requests.RequestException("Failed")):
            bot._send_to("chat789", "Hello")  # Should not raise


# =============================================================================
# build_bot tests
# =============================================================================

class TestBuildBot:
    """Test build_bot command wiring."""

    def test_build_bot_registers_all_commands(self):
        """build_bot() registers all expected commands."""
        cfg = _make_config()

        bot = build_bot(
            cfg,
            get_status_fn=lambda: "status response",
            force_plan_fn=lambda: "plan response",
            start_charge_fn=lambda: "start response",
            stop_charge_fn=lambda: "stop response",
            set_config_fn=lambda cid, args: "set response",
        )

        assert bot._commands.get("/help") is not None
        assert bot._commands.get("/status") is not None
        assert bot._commands.get("/plan") is not None
        assert bot._commands.get("/startcharge") is not None
        assert bot._commands.get("/stopcharge") is not None
        assert bot._commands.get("/config") is not None
        assert bot._commands.get("/set") is not None


# =============================================================================
# Help text and config info
# =============================================================================

class TestHelpers:
    """Test static helper functions."""

    def test_help_text_contains_commands(self):
        """_help_text() lists all commands."""
        help_text = _help_text()
        assert "/help" in help_text
        assert "/status" in help_text
        assert "/plan" in help_text
        assert "/startcharge" in help_text
        assert "/stopcharge" in help_text
        assert "/config" in help_text
        assert "/set" in help_text

    def test_config_info_called(self):
        """_config_info() returns formatted config."""
        cfg = _make_config()
        # Add to_dict method
        cfg.to_dict = MagicMock(return_value={
            "tessie_token": "",
            "esios_token": "esiosabc123",
            "vin": "",
            "max_price_cents_per_kwh": 10,
            "telegram": {"bot_token": "", "chat_id": ""},
            "strict_mode": True,
        })
        info = _config_info(cfg)
        assert "Configuración" in info
        assert "max_price_cents_per_kwh" in info
