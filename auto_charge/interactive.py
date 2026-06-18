"""Interactive UI: main menu, setup wizard, and live monitor using questionary."""

import json
import os
import sys
import time
from typing import Any, Callable, Dict

import questionary

from auto_charge.config import CONFIG_PATH, DEFAULT_CONFIG
from auto_charge.i18n import t
from auto_charge.utils import now_spain

# =============================================================================
# Shared helpers
# =============================================================================


def _load_existing_config(path: str) -> Dict[str, Any]:
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return dict(DEFAULT_CONFIG)


def _get_nested(data: dict, key: str) -> Any:
    keys = key.split(".")
    d = data
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k, "")
        else:
            return ""
    return d


def _set_nested(data: dict, key: str, value: Any) -> None:
    keys = key.split(".")
    d = data
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def _validate_field(key: str, value: Any) -> bool:
    checks = {
        "max_price_cents_per_kwh": lambda v: float(v) > 0,
        "max_charger_power_kw": lambda v: 0 < float(v) <= 250,
        "battery_capacity_kwh": lambda v: 10 < float(v) <= 250,
        "min_battery_pct": lambda v: 0 < float(v) <= 100,
        "target_time": lambda v: _validate_hhmm(v),
        "charging_efficiency": lambda v: 0.5 < float(v) <= 1.0,
        "check_interval_minutes": lambda v: 1 <= int(v) <= 120,
    }
    check = checks.get(key)
    if check:
        try:
            if not check(value):
                questionary.print(f"   ❌ {t('init.out-of-range')}\n")
                return False
        except (ValueError, TypeError):
            questionary.print(f"   ❌ {t('init.invalid-format')}\n")
            return False
    return True


def _validate_hhmm(value: str) -> bool:
    try:
        parts = value.split(":")
        return len(parts) == 2 and 0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59
    except (ValueError, IndexError):
        return False


def _mask_token(value: Any) -> str:
    if value is None:
        return "(vacío)"
    s = str(value)
    if len(s) > 12:
        return s[:8] + "..." + s[-2:]
    elif s:
        return s[:4] + "..."
    return "(vacío)"


# =============================================================================
# Main menu
# =============================================================================


def main_menu() -> str:
    """Show interactive main menu with arrow keys. Returns action string."""
    questionary.print(f"\n  {t('menu.title')}", style="bold cyan")
    questionary.print(f"  {t('menu.subtitle')}\n", style="italic")

    choice = questionary.select(
        t("menu.choose"),
        choices=[
            questionary.Choice(title=f"🚀  {t('menu.start-daemon')}", value="daemon"),
            questionary.Choice(title=f"⚡  {t('menu.run-once')}", value="once"),
            questionary.Separator(),
            questionary.Choice(title=f"⚙️   {t('menu.configure')}", value="init"),
            questionary.Choice(title=f"📋  {t('menu.show-config')}", value="show"),
            questionary.Choice(title=f"✏️   {t('menu.edit-config')}", value="edit"),
            questionary.Choice(title=f"🔍  {t('menu.monitor')}", value="monitor"),
            questionary.Separator(),
            questionary.Choice(title=f"❌  {t('menu.exit')}", value="exit"),
        ],
    ).ask()

    return choice if choice else "exit"


# =============================================================================
# Setup wizard
# =============================================================================

_FIELDS = [
    ("tessie_token", "label.tessie_token", "init.desc.tessie_token", "str", ""),
    ("vin", "label.vin", "init.desc.vin", "str", ""),
    ("esios_token", "label.esios_token", "init.desc.esios_token", "str", ""),
    ("max_price_cents_per_kwh", "label.max_price", "init.desc.max_price", "float", 10),
    ("max_charger_power_kw", "label.charger_power", "init.desc.charger_power", "float", 3.3),
    ("battery_capacity_kwh", "label.battery_capacity", "init.desc.battery_capacity", "float", 75),
    ("min_battery_pct", "label.min_battery", "init.desc.min_battery", "float", 70),
    ("target_time", "label.target_time", "init.desc.target_time", "str", "19:00"),
    ("strict_mode", "label.strict_mode", "init.desc.strict_mode", "bool", True),
    ("charging_efficiency", "label.efficiency", "init.desc.efficiency", "float", 0.9),
    ("check_interval_minutes", "label.check_interval", "init.desc.check_interval", "int", 15),
    ("telegram.bot_token", "label.telegram_bot", "init.desc.telegram_bot", "str", ""),
    ("telegram.chat_id", "label.telegram_chat", "init.desc.telegram_chat", "str", ""),
]


def run_interactive_init(config_path: str = CONFIG_PATH) -> None:
    """Interactive setup wizard with arrow-key navigation."""
    questionary.print(f"\n{'='*60}", style="bold cyan")
    questionary.print(f"  {t('init.title')}", style="bold")
    questionary.print(f"{'='*60}\n")
    questionary.print(t("init.intro"))
    questionary.print(t("init.enter-hint"))
    questionary.print(f"{t('init.skip-hint')}\n")

    existing = _load_existing_config(config_path)
    has_existing = os.path.exists(config_path)

    if has_existing:
        questionary.print(f"📁 {t('init.existing')}: {config_path}", style="green")
        questionary.print(f"   {t('init.existing-values')}\n")
    else:
        questionary.print(f"📝 {t('init.new-config')}: {config_path}\n")

    data = dict(existing) if has_existing else dict(DEFAULT_CONFIG)

    for key, label_key, desc_key, ftype, default in _FIELDS:
        label = t(label_key)
        current = _get_nested(data, key)

        if ftype == "bool":
            display = str(current).lower()
        elif ("token" in key.lower() or key == "vin") and current:
            display = _mask_token(current)
        else:
            display = str(current) if current else "(vacío)"

        questionary.print(f"{'─'*60}")
        questionary.print(f"📌 {label}", style="bold")
        questionary.print(f"   {t(desc_key)}")
        questionary.print("")

        while True:
            if ftype == "bool":
                val = questionary.select(
                    f"{label} [{display}]:",
                    choices=[
                        questionary.Choice(title="true", value=True),
                        questionary.Choice(title="false", value=False),
                        questionary.Choice(title=f"--- {t('init.keeping')}: {display} ---", value="__keep__"),
                    ],
                ).ask()
                if val is None:
                    print(f"\n❌ {t('init.cancelled')}")
                    sys.exit(0)
                if val == "__keep__":
                    questionary.print(f"   ✅ {t('init.keeping')}: {display}")
                    questionary.print("")
                    break
                _set_nested(data, key, val)
                questionary.print(f"   ✅ {t('init.saved-field')}: {val}")
                questionary.print("")
                break

            else:
                try:
                    value = questionary.text(f"   ➤ {label} [{display}]:", default="").ask()
                except KeyboardInterrupt:
                    print(f"\n❌ {t('init.cancelled')}")
                    sys.exit(0)

                if value is None:
                    print(f"\n❌ {t('init.cancelled')}")
                    sys.exit(0)

                value = value.strip()

                if value == "":
                    if current not in ("", None):
                        questionary.print(f"   ✅ {t('init.keeping')}: {display}")
                        questionary.print("")
                    break

                if value.lower() == "skip":
                    _set_nested(data, key, "" if ftype == "str" else default)
                    questionary.print(f"   ⏭️  {t('init.skipped')}")
                    questionary.print("")
                    break

                try:
                    if ftype == "int":
                        value = int(value)
                    elif ftype == "float":
                        value = float(value)
                except ValueError:
                    questionary.print(f"   ❌ {t('init.invalid-format')} ({ftype})\n")
                    continue

                if not _validate_field(key, value):
                    continue

                _set_nested(data, key, value)
                questionary.print(f"   ✅ {t('init.saved-field')}")
                questionary.print("")
                break

    # Save
    questionary.print(f"{'='*60}")
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    questionary.print(f"✅ {t('init.saved')}: {config_path}", style="green")
    questionary.print("")
    questionary.print(f"{t('init.summary')}:", style="bold")
    for key, *_ in _FIELDS:
        val = _get_nested(data, key)
        if ("token" in key.lower() or key == "vin") and val:
            val = _mask_token(val)
        questionary.print(f"  {key}: {val}")
    questionary.print("")
    questionary.print(f"🚀 {t('init.ready')}: python tesla_pvpc.py")
    questionary.print(f"   {t('init.try-debug')}")
    questionary.print("")


# =============================================================================
# Quick config editor (--edit)
# =============================================================================


def run_interactive_edit(config_path: str = CONFIG_PATH) -> None:
    """Quick interactive editor: pick a config field, enter a new value, save."""
    from auto_charge.config import Config

    questionary.print(f"\n{'='*60}", style="bold cyan")
    questionary.print(f"  {t('edit.title')}", style="bold")
    questionary.print(f"{'='*60}\n")

    # Load current config
    try:
        cfg = Config(config_path)
        data = cfg.to_dict()
    except FileNotFoundError:
        questionary.print(f"⚠️  {t('show.no-config')}")
        questionary.print(f"   {t('show.use-init')}\n")
        return
    except Exception as e:
        questionary.print(f"❌ Error: {e}\n")
        return

    # Build grouped choices for questionary
    groups = [
        (t("show.section.tessie"), [
            {"key": "tessie_token", "label": t("label.tessie_token")},
            {"key": "vin", "label": t("label.vin")},
        ]),
        (t("show.section.esios"), [
            {"key": "esios_token", "label": t("label.esios_token")},
        ]),
        (t("show.section.charge"), [
            {"key": "min_battery_pct", "label": t("label.min_battery")},
            {"key": "max_price_cents_per_kwh", "label": t("label.max_price")},
            {"key": "max_charger_power_kw", "label": t("label.charger_power")},
            {"key": "battery_capacity_kwh", "label": t("label.battery_capacity")},
            {"key": "target_time", "label": t("label.target_time")},
            {"key": "strict_mode", "label": t("label.strict_mode")},
            {"key": "charging_efficiency", "label": t("label.efficiency")},
            {"key": "check_interval_minutes", "label": t("label.check_interval")},
        ]),
        (t("show.section.telegram"), [
            {"key": "telegram.bot_token", "label": t("label.telegram_bot")},
            {"key": "telegram.chat_id", "label": t("label.telegram_chat")},
        ]),
    ]

    # Build choices list with separators
    choices = []
    for group_title, fields in groups:
        choices.append(questionary.Separator(f"── {group_title} ──"))
        for field in fields:
            current = _get_nested(data, field["key"])
            if ("token" in field["key"].lower() or field["key"] == "vin") and current:
                display = _mask_token(current)
            elif isinstance(current, bool):
                display = "true" if current else "false"
            else:
                display = str(current) if current not in ("", None) else "(vacío)"
            choices.append(
                questionary.Choice(
                    title=f"  {field['label']}: {display}",
                    value=field["key"],
                )
            )
    choices.append(questionary.Separator())
    choices.append(questionary.Choice(title=f"❌  {t('menu.exit')}", value="__cancel__"))

    questionary.print(f"📋 {t('edit.select-field')}\n")

    selected = questionary.select(
        t("edit.field"),
        choices=choices,
    ).ask()

    if selected is None or selected == "__cancel__":
        questionary.print(f"\n⏹️  {t('edit.cancelled')}\n")
        return

    key = selected
    current = _get_nested(data, key)
    current_type = type(current).__name__ if current is not None else "str"
    is_secure = "token" in key.lower() or key == "vin"

    questionary.print("")
    questionary.print(f"{'─'*60}")
    questionary.print(f"✏️  {t('edit.editing')}: {key}", style="bold")
    questionary.print(f"   {t('edit.current')}: {_mask_token(current) if is_secure and current else current}")
    questionary.print(f"   {t('edit.type')}: {current_type}")
    questionary.print("")

    # Handle booleans with select
    if isinstance(current, bool):
        new_val = questionary.select(
            f"{t('edit.new-value')}: ({str(current).lower()})",
            choices=[
                questionary.Choice(title="true", value=True),
                questionary.Choice(title="false", value=False),
                questionary.Choice(title=f"--- {t('init.keeping')}: {str(current).lower()} ---", value="__keep__"),
            ],
        ).ask()
        if new_val is None or new_val == "__keep__":
            questionary.print(f"\n⏹️  {t('edit.unchanged')}\n")
            return
    else:
        # Show current as default for text input
        display_default = str(current) if current not in ("", None) else ""
        new_val = questionary.text(
            f"{t('edit.new-value')} ({t('init.enter-hint')} vacío = mantener):",
            default=display_default,
        ).ask()

        if new_val is None:
            questionary.print(f"\n⏹️  {t('edit.cancelled')}\n")
            return

        new_val = new_val.strip()

        # Empty = keep current
        if new_val == "":
            questionary.print(f"\n✅ {t('edit.unchanged')}\n")
            return

        # Coerce to the right type
        try:
            if current_type == "int":
                new_val = int(new_val)
            elif current_type == "float":
                new_val = float(new_val)
        except ValueError:
            questionary.print(f"   ❌ {t('init.invalid-format')} ({current_type})\n")
            return

        # Validate
        if not _validate_field(key, new_val):
            return

    # Save
    _set_nested(data, key, new_val)
    try:
        with open(config_path, "w") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        display_new = _mask_token(new_val) if is_secure and new_val else str(new_val)
        questionary.print(f"")
        questionary.print(f"✅ {t('edit.saved')}", style="green")
        questionary.print(f"   {key}: {_mask_token(current) if is_secure and current else current} → {display_new}")
        questionary.print("")
        # Offer to edit another
        again = questionary.confirm(t("edit.another"), default=False).ask()
        if again:
            run_interactive_edit(config_path)
        else:
            questionary.print(f"   {t('edit.restart-hint')}\n")
    except Exception as e:
        questionary.print(f"❌ {t('edit.save-error')}: {e}\n")


# =============================================================================
# Live monitor
# =============================================================================

_MONITOR_REFRESH = 5


def live_monitor(get_status_fn: Callable[[], Dict[str, Any]]) -> None:
    """Real-time dashboard. Refreshes every few seconds."""
    import signal

    running = True

    def _stop(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _stop)
    questionary.print(f"\n{t('monitor.title')}", style="bold cyan")
    questionary.print("")

    while running:
        try:
            status = get_status_fn()
        except Exception:
            status = {"error": "Cannot fetch status"}

        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()

        questionary.print(f"  {t('monitor.title')}", style="bold cyan")
        questionary.print(f"  {'─'*50}")
        questionary.print("")

        vehicle = status.get("vehicle", {})
        if vehicle:
            pct = vehicle.get("battery_pct", "?")
            is_c = vehicle.get("is_charging", False)
            is_p = vehicle.get("is_plugged_in", False)
            limit = vehicle.get("charge_limit_pct", "?")

            bar_len = 30
            filled = int(float(pct) * bar_len / 100) if isinstance(pct, (int, float)) else 0
            bar = "█" * filled + "░" * (bar_len - filled)

            questionary.print(f"  🚗 {t('monitor.battery')}: [{bar}] {pct}%", style="bold")
            questionary.print(f"  ⚡ {t('monitor.charging')}: {'✅' if is_c else '⏸️'}")
            questionary.print(f"  🔌 {t('monitor.plugged')}: {'✅' if is_p else '❌'}")
            questionary.print(f"  🎯 {t('monitor.limit')}: {limit}%")
            questionary.print("")

        plan = status.get("plan")
        if plan:
            questionary.print(f"  📋 {t('monitor.plan')}:", style="bold")
            questionary.print(f"     {t('monitor.target')}: {plan.get('target_pct', '?')}%")
            questionary.print(f"     {t('monitor.deadline')}: {plan.get('target_time', '?')}")
            questionary.print(f"     {t('monitor.cost')}: {plan.get('total_cost', '?')} €")
            for s in plan.get("slots", []):
                questionary.print(f"       {s}")
        else:
            questionary.print(f"  📋 {t('monitor.no-plan')}", style="italic")

        questionary.print("")

        prices = status.get("prices_summary", "")
        if prices:
            questionary.print(f"  📊 {t('monitor.prices')}: {prices}")
        else:
            questionary.print(f"  📊 {t('monitor.no-prices')}", style="italic")

        questionary.print("")
        extra = status.get("_extra", "")
        if extra:
            questionary.print(f"  ⚙️  {extra}")
        else:
            questionary.print(f"  🕐 {now_spain().strftime('%H:%M:%S')} | {t('monitor.refreshing')}: {_MONITOR_REFRESH}s", style="dim")

        for _ in range(_MONITOR_REFRESH * 2):
            if not running:
                break
            time.sleep(0.5)

    questionary.print(f"\n{t('monitor.waiting')}...")
