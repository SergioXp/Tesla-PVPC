"""Telegram bot: notifications and interactive commands."""

import json
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import requests

from auto_charge.config import Config
from auto_charge.utils import logger, now_spain

TELEGRAM_API = "https://api.telegram.org/bot"


class TelegramBot:
    """Telegram integration for notifications and remote control commands."""

    def __init__(self, config: Config):
        self._token = config.telegram_bot_token
        self._chat_id = config.telegram_chat_id
        self._enabled = config.telegram_enabled
        self._last_update_id: int = 0
        self._commands: Dict[str, Callable] = {}

        if self._enabled:
            logger.info(f"Telegram bot enabled (chat_id={self._chat_id})")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def send_message(self, text: str) -> bool:
        """Send a notification message to the configured chat."""
        if not self._enabled:
            return False

        url = f"{TELEGRAM_API}{self._token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code != 200:
                logger.warning(f"Telegram send failed: {resp.text}")
                return False
            return True
        except requests.RequestException as e:
            logger.error(f"Telegram error: {e}")
            return False

    def register_command(self, command: str, handler: Callable[[str, str], str]) -> None:
        """
        Register a handler for a Telegram command.
        handler(chat_id, args) → response_text
        """
        self._commands[f"/{command}"] = handler

    def poll(self) -> None:
        """
        Check for new messages and dispatch commands.
        Call this in the main loop periodically.
        """
        if not self._enabled:
            return

        updates = self._get_updates()
        for upd in updates:
            self._last_update_id = int(upd.get("update_id", self._last_update_id))

            msg = upd.get("message", {})
            text = msg.get("text", "")
            chat_id = str(msg.get("chat", {}).get("id", ""))

            if not text.startswith("/"):
                continue

            parts = text.split(maxsplit=1)
            cmd = parts[0]
            args = parts[1] if len(parts) > 1 else ""

            handler = self._commands.get(cmd)
            if handler:
                try:
                    response = handler(chat_id, args)
                    if response:
                        self._send_to(chat_id, response)
                except Exception as e:
                    logger.error(f"Command {cmd} error: {e}")
                    self._send_to(chat_id, f"❌ Error: {e}")
            else:
                self._send_to(
                    chat_id,
                    f"❓ Comando desconocido: {cmd}\nUsa /help para ver los comandos disponibles.",
                )

    def _get_updates(self) -> List[Dict[str, Any]]:
        """Fetch pending updates from Telegram."""
        url = f"{TELEGRAM_API}{self._token}/getUpdates"
        params = {
            "offset": self._last_update_id + 1,
            "timeout": 1,
            "allowed_updates": json.dumps(["message"]),
        }
        try:
            resp = requests.get(url, params=params, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    return data.get("result", [])
        except requests.RequestException:
            pass
        return []

    def _send_to(self, chat_id: str, text: str) -> None:
        """Send a message to a specific chat_id."""
        url = f"{TELEGRAM_API}{self._token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        try:
            requests.post(url, json=payload, timeout=10)
        except requests.RequestException:
            pass


def build_bot(
    config: Config,
    get_status_fn: Callable[[], str],
    force_plan_fn: Callable[[], str],
    start_charge_fn: Callable[[], str],
    stop_charge_fn: Callable[[], str],
    set_config_fn: Callable[[str, str], str],
) -> TelegramBot:
    """Create a TelegramBot with all command handlers wired up."""

    bot = TelegramBot(config)

    bot.register_command("help", lambda cid, a: _help_text())
    bot.register_command("status", lambda cid, a: get_status_fn())
    bot.register_command("plan", lambda cid, a: force_plan_fn())
    bot.register_command("startcharge", lambda cid, a: start_charge_fn())
    bot.register_command("stopcharge", lambda cid, a: stop_charge_fn())
    bot.register_command("config", lambda cid, a: _config_info(config))
    bot.register_command("set", lambda cid, a: set_config_fn(cid, a))

    return bot


def _help_text() -> str:
    return (
        "🤖 *Tesla-PVPC - Comandos*\n\n"
        "/status - Estado actual (batería, carga, plan)\n"
        "/plan - Recalcular plan de carga ahora\n"
        "/startcharge - Iniciar carga manualmente\n"
        "/stopcharge - Detener carga manualmente\n"
        "/config - Ver configuración actual\n"
        "/set `<clave>` `<valor>` - Cambiar configuración\n"
        "  Ej: `/set max_price_cents_per_kwh 8`\n"
        "  Ej: `/set min_battery_pct 80`\n"
        "  Ej: `/set strict_mode false`\n"
        "/help - Este mensaje"
    )


def _config_info(config: Config) -> str:
    d = config.to_dict()
    # Redact tokens
    d["tessie_token"] = d["tessie_token"][:10] + "..." if d.get("tessie_token") else "(no)"
    d["esios_token"] = d["esios_token"][:10] + "..." if d.get("esios_token") else "(no)"
    if "telegram" in d:
        d["telegram"]["bot_token"] = d["telegram"]["bot_token"][:10] + "..." if d["telegram"].get("bot_token") else "(no)"

    lines = ["⚙️ *Configuración actual*"]
    for k, v in d.items():
        if k == "telegram":
            lines.append(f"  telegram.bot_token: {d['telegram']['bot_token']}")
            lines.append(f"  telegram.chat_id: {d['telegram']['chat_id']}")
        else:
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)
