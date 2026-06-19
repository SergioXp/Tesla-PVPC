#!/usr/bin/env python3
"""Tesla-PVPC - Optimize Tesla charging with Spanish electricity prices."""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from typing import Any, Dict, Optional

from auto_charge import __version__
from auto_charge.config import CONFIG_PATH, DEFAULT_CONFIG, SECRET_KEYS
from auto_charge.daemon import AutoChargeDaemon
from auto_charge.i18n import set_lang, t
from auto_charge.utils import logger, mask_token, now_spain


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=t("cli.description"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=t("cli.epilog"),
    )
    parser.add_argument("--once", action="store_true", help=t("cli.once"))
    parser.add_argument("--config", type=str, default=CONFIG_PATH, help=t("cli.config"))
    parser.add_argument("--verbose", "-v", action="store_true", help=t("cli.verbose"))
    parser.add_argument("--debug", action="store_true", help=t("cli.debug"))
    parser.add_argument("--initial-battery", type=float, default=35.0, metavar="PCT", help=t("cli.init-battery"))
    parser.add_argument("--init", action="store_true", help=t("cli.init"))
    parser.add_argument("--show-config", action="store_true", help=t("cli.show-config"))
    parser.add_argument("--dry-run", action="store_true", help=t("cli.dry-run"))
    parser.add_argument("--edit", action="store_true", help=t("cli.edit"))
    parser.add_argument("--background", "-b", action="store_true", help=t("cli.background"))
    parser.add_argument("--prices", action="store_true", help=t("cli.prices"))
    parser.add_argument("--dashboard", action="store_true", help=t("cli.dashboard"))
    parser.add_argument("--version", action="store_true", help=t("cli.version"))
    parser.add_argument("--lang", type=str, default="es", choices=["es", "en"], help=t("cli.lang"))
    return parser.parse_args()


def run_once(config, debug: bool = False, dry_run: bool = False, initial_battery: float = 35.0) -> None:
    from auto_charge.prices import PriceProvider
    from auto_charge.tessie import TessieClient, ReadOnlyVehicleClient
    from auto_charge.debug_tessie import DebugTessieClient
    from auto_charge.planner import ChargePlanner
    from auto_charge.utils import tomorrow_str, today_str

    price_provider = PriceProvider(config)
    if debug or config.debug_mode:
        logger.info(f"🐛 {t('debug.using-sim')}")
        tessie = DebugTessieClient(config, initial_battery_pct=initial_battery)
    else:
        tessie = TessieClient(config)
        if dry_run:
            tessie = ReadOnlyVehicleClient(tessie)
    planner = ChargePlanner(config)

    now = now_spain()
    current_hour = now.hour
    target_hour = config.target_hour
    today = today_str()
    tomorrow = tomorrow_str()

    # Always fetch today's prices
    prices = price_provider.fetch_daily_prices(today)
    if not prices:
        logger.error(f"Failed to fetch prices for today ({today}). Exiting.")
        return

    # If planning window wraps past midnight, also fetch tomorrow's prices (offset +24)
    if current_hour >= target_hour:
        logger.info(f"🌙 Planning window wraps to tomorrow ({tomorrow}). Fetching tomorrow prices too...")
        tomorrow_prices = price_provider.fetch_daily_prices(tomorrow)
        if tomorrow_prices:
            # Merge with offset 24 (tomorrow hour 0 → 24, hour 1 → 25, etc.)
            for h, price in tomorrow_prices.items():
                prices[h + 24] = price
            logger.info(f"Merged {len(tomorrow_prices)} tomorrow prices (offset +24).")
        else:
            logger.warning(f"Could not fetch tomorrow prices ({tomorrow}). Will use today prices only.")

    state = tessie.get_state()
    if state is None:
        logger.error("Failed to get vehicle state. Exiting.")
        return

    plan = planner.plan(
        prices=prices,
        current_battery_pct=state.battery_pct,
        current_hour=current_hour,
        date_str=today,
    )

    if not plan.slots:
        logger.info("No charging slots found.")
        return

    logger.info(plan.summary())

    if state.is_plugged_in:
        from auto_charge.daemon import AutoChargeDaemon
        charge_now = any(
            AutoChargeDaemon._slot_covers_hour(s, now.hour)
            for s in plan.slots
        )
        if charge_now and not state.is_charging:
            logger.info(f"Hour {now.hour}: starting charge.")
            tessie.start_charge()
        elif not charge_now and state.is_charging:
            logger.info(f"Hour {now.hour}: stopping charge.")
            tessie.stop_charge()
        else:
            logger.info(f"Hour {now.hour}: no action needed.")
    else:
        logger.warning("Vehicle not plugged in.")

    target = max(int(config.min_battery_pct), 50)
    if state.charge_limit_pct < target:
        logger.info(f"Setting charge limit to {target}%.")
        tessie.set_charge_limit(target)


def _kill_existing_instances() -> None:
    """Kill any other running instances of tesla_pvpc.py.

    Uses SIGTERM first, then waits briefly, then SIGKILL if still alive.
    Only kills processes with a different PID (not itself).
    """
    my_pid = os.getpid()

    try:
        # Find all Python processes running tesla_pvpc (excluding this one)
        result = subprocess.run(
            ["pgrep", "-f", r"python.*tesla_pvpc"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return  # No other instances found

        pids = [
            int(p.strip())
            for p in result.stdout.strip().split("\n")
            if p.strip() and int(p.strip()) != my_pid
        ]

        if not pids:
            return

        logger.info(f"🔫 Cerrando {len(pids)} instancia(s) previa(s) de Tesla-PVPC: {pids}")

        # SIGTERM first
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass  # Already dead

        # Wait briefly for graceful shutdown
        time.sleep(1)

        # SIGKILL any that are still alive
        for pid in pids:
            try:
                os.kill(pid, 0)  # Check if alive
                os.kill(pid, signal.SIGKILL)
                logger.info(f"  ✨ Instancia PID {pid} terminada.")
            except (ProcessLookupError, PermissionError):
                pass  # Already dead or no permission

    except (subprocess.TimeoutExpired, FileNotFoundError):
        # pgrep might not exist on some systems; fall back silently
        pass
    except Exception as e:
        logger.warning(f"Error al buscar/cerrar instancias previas: {e}")


def _build_monitor_status_fn(config, daemon) -> callable:
    """Factory: creates the status callback used by live_monitor."""
    def get_status():
        state = daemon.tessie.get_state()
        vehicle = {}
        if state:
            vehicle = {
                "battery_pct": state.battery_pct,
                "is_charging": state.is_charging,
                "is_plugged_in": state.is_plugged_in,
                "charge_limit_pct": state.charge_limit_pct,
                "charger_power_kw": state.charger_power_kw,
            }

        plan_info = {}
        if daemon.current_plan:
            cp = daemon.current_plan
            plan_info = {
                "target_pct": cp.target_pct,
                "expected_pct": cp.expected_final_pct,
                "target_time": config.target_time,
                "total_cost": f"{cp.total_cost_eur:.3f}",
                "slots": [str(s) for s in cp.slots],
            }

        prices_summary = ""
        if daemon.prices:
            mn = min(daemon.prices.values())
            mx = max(daemon.prices.values())
            avg = sum(daemon.prices.values()) / len(daemon.prices)
            prices_summary = f"min={mn:.1f}  max={mx:.1f}  avg={avg:.1f} c/kWh"

        return {"vehicle": vehicle, "plan": plan_info, "prices_summary": prices_summary}

    return get_status


def main() -> None:
    args = parse_args()

    # Set language
    set_lang(args.lang)

    # --- --version ---
    if args.version:
        print(f"Tesla-PVPC v{__version__}")
        print("Carga inteligente del Tesla con precios PVPC de España.")
        print("MIT License - github.com/SergioXp/Tesla-PVPC")
        return

    if args.verbose:
        from auto_charge.utils import setup_logger
        setup_logger(level=10)

    # --- --show-config ---
    if args.show_config:
        show_config(args.config)
        return

    # --- --init ---
    if args.init:
        from auto_charge.interactive import run_interactive_init
        run_interactive_init(args.config)
        return

    # --- --edit ---
    if args.edit:
        from auto_charge.interactive import run_interactive_edit
        run_interactive_edit(args.config)
        return

    # --- --prices ---
    if args.prices:
        show_prices()
        return

    # --- --dashboard (solo, sin --background) ---
    if args.dashboard and not args.background:
        show_dashboard()
        return

    # --- Check if any action flag is set ---
    has_action_flag = (args.once or args.debug or args.dry_run
                        or args.initial_battery != 35.0
                        or args.config != CONFIG_PATH)

    # --- If no flags: check for running daemon first, else show menu ---
    if not has_action_flag and sys.stdin.isatty():
        # Check if daemon is running in background
        from auto_charge.status import get_daemon_pid
        daemon_pid = get_daemon_pid()
        if daemon_pid:
            print(f"\n⚡  Daemon activo (PID {daemon_pid})")
            print(f"📋  Usa --dashboard, --prices, --show-config para ver el estado\n")

        # If --dashboard is set, skip the menu entirely
        if args.dashboard:
            if daemon_pid:
                # Daemon ya corriendo: solo abrimos el dashboard
                show_dashboard()
                return
            # Si no hay daemon, -b --dashboard: cae al modo daemon
            if args.background:
                choice = "daemon"
            else:
                choice = "exit"
        else:
            from auto_charge.interactive import main_menu
            choice = main_menu()

        if choice == "exit":
            print(f"\n👋 {t('menu.exit')}.\n")
            return
        elif choice == "init":
            from auto_charge.interactive import run_interactive_init
            run_interactive_init(args.config)
            return
        elif choice == "show":
            show_config(args.config)
            return
        elif choice == "edit":
            from auto_charge.interactive import run_interactive_edit
            run_interactive_edit(args.config)
            return
        elif choice == "monitor":
            # Check if daemon is running; if so, read its status file
            from auto_charge.status import get_daemon_pid
            if get_daemon_pid():
                show_dashboard()
                return
            # No daemon running: load config and create a one-shot monitor
            try:
                from auto_charge.config import Config
                config = Config(args.config)
            except Exception as e:
                print(f"{t('cli.config-error')} {e}")
                sys.exit(1)
            daemon = AutoChargeDaemon(config)
            daemon._fetch_prices()
            daemon._create_plan()
            from auto_charge.interactive import live_monitor
            live_monitor(_build_monitor_status_fn(config, daemon))
            return
        elif choice == "once":
            args.once = True
        elif choice == "daemon":
            pass  # fall through to daemon mode below
        else:
            return

    # --- Load config ---
    try:
        from auto_charge.config import Config
        config = Config(args.config)
    except FileNotFoundError:
        print(t("cli.no-config1"))
        print(t("cli.no-config2"))
        print(t("cli.no-config3"))
        sys.exit(1)
    except Exception as e:
        print(f"{t('cli.config-error')} {e}")
        sys.exit(1)

    debug = args.debug or config.debug_mode

    if args.dry_run and debug:
        logger.info(f"ℹ️  {t('dryrun.ignored')}")

    if args.once:
        logger.info(t("daemon.running-once"))
        run_once(config, debug=debug, dry_run=args.dry_run, initial_battery=args.initial_battery)
    else:
        # --- Check if daemon already running (don't kill if --background) ---
        if not args.background:
            _kill_existing_instances()

        if debug:
            logger.info(t("debug.active"))
        logger.info(t("daemon.starting"))
        daemon = AutoChargeDaemon(config)

        if args.dry_run and not debug:
            from auto_charge.tessie import ReadOnlyVehicleClient
            daemon.tessie = ReadOnlyVehicleClient(daemon.tessie)
            logger.info(f"🛡️  {t('dryrun.active')}")

        if args.debug or args.initial_battery != 35.0:
            from auto_charge.debug_tessie import DebugTessieClient
            daemon._debug_mode = True
            daemon.tessie = DebugTessieClient(config, initial_battery_pct=args.initial_battery)
            logger.info(t("debug.forced", pct=f"{args.initial_battery:.0f}"))

        if args.background:
            logger.info(t("daemon.background"))
            if args.dashboard:
                # Combo -b --dashboard: fork daemon, luego abre dashboard
                try:
                    pid = os.fork()
                    if pid > 0:
                        # Padre: esperar a que el daemon escriba estado (max 10s)
                        for _ in range(20):
                            time.sleep(0.5)
                            try:
                                with open("/tmp/autocharge-status.json") as _f:
                                    _st = json.load(_f)
                                    if _st.get("prices_summary") or _st.get("plan") or _st.get("vehicle"):
                                        break
                            except (IOError, json.JSONDecodeError):
                                pass
                        show_dashboard()
                        sys.exit(0)
                    # Hijo: detached daemon
                    os.setsid()
                    pid2 = os.fork()
                    if pid2 > 0:
                        sys.exit(0)
                    # Nieto: actualizar PID en status file (el daemon real)
                    from auto_charge.status import write_status
                    write_status(daemon_pid=os.getpid(), daemon_mode="daemon")
                except (AttributeError, OSError):
                    logger.warning("Background mode no disponible en este sistema. Ejecutando en primer plano.")
            else:
                _daemonize()

        daemon.run()


# =========================================================================
# show_config (i18n-aware)
# =========================================================================


def _daemonize() -> None:
    """Fork the process to the background (Unix only)."""
    try:
        pid = os.fork()
        if pid > 0:
            # Parent process: exit (terminal returns)
            sys.stdout.write(f"  PID: {pid}\n")
            sys.stdout.write(f"  Usa --prices, --dashboard, --show-config para ver el estado\n")
            sys.stdout.write(f"  Para pararlo: kill {pid} o pkill -f tesla_pvpc\n")
            sys.stdout.flush()
            sys.exit(0)
        # Child process: continue
        os.setsid()
        # Second fork to fully detach
        pid2 = os.fork()
        if pid2 > 0:
            sys.exit(0)
    except AttributeError:
        # os.fork() doesn't exist on Windows
        logger.warning("Background mode no soportado en Windows. Ejecutando en primer plano.")
    except OSError:
        logger.warning("No se pudo lanzar en background. Ejecutando en primer plano.")


def show_prices() -> None:
    """Display today's electricity prices from the running daemon status."""
    from auto_charge.status import read_status, status_age_seconds

    max_price_limit: Optional[float] = None
    prices = None
    date = ""
    source = ""
    age = None

    # Try daemon status first
    status = read_status()
    if status and status.get("prices"):
        # Filter out 24+ keys (tomorrow merged prices) — only show 0-23
        raw = {int(k): float(v) for k, v in status["prices"].items()}
        prices = {h: v for h, v in raw.items() if 0 <= h <= 23}
        date = status.get("prices_date", "desconocido")
        source = "daemon"
        age = status_age_seconds()
        max_price_limit = status.get("config", {}).get("max_price")

    if not prices:
        # No daemon, fetch directly
        try:
            from auto_charge.config import Config
            cfg = Config()
            from auto_charge.prices import PriceProvider
            pp = PriceProvider(cfg)
            today = now_spain().strftime("%Y-%m-%d")
            prices = pp.fetch_daily_prices(today)
            if not prices:
                print("\n❌ No se pudieron obtener precios.\n")
                return
            date = today
            source = pp.last_source or "desconocido"
            max_price_limit = cfg.max_price_cents_per_kwh
            age = None
        except Exception as e:
            print(f"\n❌ Error: {e}\n")
            return

    _print_prices_table(prices, date, source, max_price_limit=max_price_limit, age=age)


def _print_prices_table(
    prices: dict,
    date: str,
    source: str,
    max_price_limit: Optional[float] = None,
    age: Optional[float] = None,
) -> None:
    """Pretty-print hourly prices in €/kWh.

    Args:
        prices: dict of {hour: price_cents_per_kwh}
        date: date string for display
        source: data source label
        max_price_limit: max price in cents/kWh (optional, from config)
        age: seconds since last daemon update (optional)
    """
    # Convert c€/kWh → €/kWh
    prices_eur = {h: v / 100.0 for h, v in prices.items()}
    vals_eur = list(prices_eur.values())

    avg_eur = sum(vals_eur) / len(vals_eur)
    min_eur = min(vals_eur)
    max_eur = max(vals_eur)
    max_bar = max_eur or 0.001

    cheapest_hour = min(prices_eur, key=prices_eur.get)
    expensive_hour = max(prices_eur, key=prices_eur.get)

    # Max price limit en €/kWh
    max_price_eur = max_price_limit / 100.0 if max_price_limit is not None else None
    hours_below = sum(1 for v in prices_eur.values() if v <= (max_price_eur or float("inf")))
    total_hours = len(vals_eur)

    # --- Header ---
    print()
    now = now_spain()
    current_hour = now.hour
    header = f"📊  Precios de la luz — {date}  ({source})"
    print(header)
    if max_price_eur is not None:
        pct_str = f" ({hours_below/total_hours*100:.0f}% del día)" if total_hours else ""
        print(f"     Límite: ≤ {max_price_eur:.3f} €/kWh  →  {hours_below}h disponibles{pct_str}")
    print()

    # --- Hour list ---
    for h in sorted(prices_eur.keys()):
        p = prices_eur[h]
        bar_len = max(1, int(p / max_bar * 10))
        bar = "█" * bar_len

        # Current hour marker
        now_marker = "◀" if h == current_hour else " "

        # Price-limit markers
        flags = []
        if max_price_eur is not None:
            flags.append("✓" if p <= max_price_eur else "↑")
        if h == cheapest_hour:
            flags.append("← MÍN")
        if h == expensive_hour:
            flags.append("← MÁX")

        flag_str = "  " + " ".join(flags) if flags else ""
        print(f"  {h:02d}:00 {now_marker} {p:.3f} €/kWh  {bar:12}{flag_str}")

    # --- Footer summary ---
    print()
    print(f"  {'─' * 46}")
    print(f"  Mín:   {min_eur:.3f} €/kWh  ({cheapest_hour:02d}:00)")
    print(f"  Máx:   {max_eur:.3f} €/kWh  ({expensive_hour:02d}:00)")
    print(f"  Media:  {avg_eur:.3f} €/kWh")
    if age is not None:
        print(f"  (actualizado hace {int(age)}s por el daemon)")
    print()


def show_dashboard() -> None:
    """Live dashboard que lee el status file del daemon y refresca cada 5s."""
    from auto_charge.status import get_daemon_pid

    pid = get_daemon_pid()
    if not pid:
        print(f"\n⚠️  No hay ningún daemon ejecutándose.")
        print(f"   Lánzalo con: ./run.sh -b\n")
        return

    from auto_charge.status import read_status, status_age_seconds
    from auto_charge.interactive import live_monitor

    def _dash_status():
        status = read_status()
        if not status:
            return {"vehicle": {}, "plan": None, "prices_summary": ""}

        veh = status.get("vehicle", {}) or {}
        plan_raw = status.get("plan")
        plan = None
        if plan_raw:
            plan = {
                "target_pct": plan_raw.get("target_pct"),
                "expected_pct": plan_raw.get("expected_pct"),
                "target_time": status.get("config", {}).get("target_time", "?"),
                "total_cost": plan_raw.get("total_cost_eur", "?"),
                "slots": [_format_slot_hours(s) for s in plan_raw.get("slots", [])],
            }

        ps = status.get("prices_summary", {})
        prices_summary = ""
        if ps:
            prices_summary = f"min={ps.get('min')}  max={ps.get('max')}  avg={ps.get('avg')} c/kWh"

        age = status_age_seconds()
        extra = f"PID {pid} | {'Real' if status.get('daemon_mode') != 'debug' else 'Simulado'}"
        if age is not None:
            extra += f" | hace {int(age)}s"

        return {"vehicle": veh, "plan": plan, "prices_summary": prices_summary,
                "_extra": extra}

    live_monitor(_dash_status)


def _format_slot_hours(s: dict) -> str:
    """Format a slot dict (with raw hour offsets) into a display string.

    Handles 24+ hour offsets (tomorrow) by showing them as +1d HH:00.
    Reuses ChargingSlot._hour_label from planner.py.
    """
    from auto_charge.planner import ChargingSlot
    return (
        f"{ChargingSlot._hour_label(s['start'])}-"
        f"{ChargingSlot._hour_label(s['end'])} ({s['kwh']:.1f}kWh)"
    )


def show_config(config_path: str = CONFIG_PATH) -> None:
    print()
    print("=" * 60)
    print(f"  {t('show.title')}")
    print("=" * 60)
    print()

    from auto_charge.config import Config

    try:
        cfg = Config(config_path)
        data = cfg.to_dict()
        source = []
        if os.path.exists(config_path):
            source.append("config.json")
        if os.path.exists(os.path.join(os.path.dirname(config_path), ".env")):
            source.append(".env")
        print(f"📁 {t('show.source')}: {', '.join(source) if source else 'defaults'}")
        print(f"📁 {t('show.path')}:   {config_path}")
    except FileNotFoundError:
        data = dict(DEFAULT_CONFIG)
        print(f"⚠️  {t('show.no-config')}")
        print(f"   {t('show.use-init')}")
    print()

    sections = [
        (f"🔌 {t('show.section.tessie')}", ["tessie_token", "vin"]),
        (f"📊 {t('show.section.esios')}", ["esios_token"]),
        (f"⚡ {t('show.section.charge')}", [
            "max_price_cents_per_kwh", "max_charger_power_kw", "battery_capacity_kwh",
            "min_battery_pct", "target_time", "strict_mode",
            "charging_efficiency", "check_interval_minutes",
        ]),
        (f"🤖 {t('show.section.telegram')}", ["telegram"]),
    ]

    for section_title, keys in sections:
        print(f"{section_title}:")
        for key in keys:
            if key == "telegram":
                tg = data.get("telegram", {})
                bot = tg.get("bot_token", "")
                cid = tg.get("chat_id", "")
                label = t("show.not-configured")
                bot_src = "🔒 .env" if bot else ""
                cid_src = "🔒 .env" if cid else ""
                print(f"  telegram.bot_token = {mask_token(bot) if bot else f'({label})'} {bot_src}")
                print(f"  telegram.chat_id   = {cid or f'({label})'} {cid_src}")
                if bot and cid:
                    print(f"  → {t('show.telegram-active')} ✅")
                else:
                    print(f"  → {t('show.telegram-inactive')} ⏸️")
            else:
                val = data.get(key, "")
                if ("token" in key.lower() or key == "vin") and val:
                    display = mask_token(val)
                    src = "🔒 .env" if key in SECRET_KEYS else ""
                else:
                    display = str(val)
                    src = "📄 config.json"
                print(f"  {key} = {display}  {src}")
        print()

    print(f"🐛 {t('show.mode')}:")
    has_token = bool(data.get("tessie_token"))
    print(f"  debug_mode = {not has_token}", end="")
    if not has_token:
        print(f" ({t('show.debug-on')})")
    else:
        print(f" ({t('show.debug-off')})")
    print()





if __name__ == "__main__":
    main()
