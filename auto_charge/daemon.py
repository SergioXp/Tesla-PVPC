"""Daemon: 24/7 orchestrator for Tesla-PVPC.

Fetches electricity prices, plans optimal charging, enforces the schedule,
and provides Telegram-based remote control.
"""

import os
import signal
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

from auto_charge.i18n import t as _t
from auto_charge.status import write_status

from auto_charge.config import Config
from auto_charge.prices import PriceProvider
from auto_charge.planner import ChargePlanner, ChargingPlan
from auto_charge.telegram_bot import TelegramBot, build_bot
from auto_charge.tessie import TessieClient
from auto_charge.debug_tessie import DebugTessieClient
from auto_charge.utils import get_spain_tz, logger, mask_token, now_spain, today_str, tomorrow_str


class AutoChargeDaemon:
    """Main daemon that orchestrates everything."""

    def __init__(self, config: Config):
        self.cfg = config

        # Price provider (ESIOS → REData fallback)
        self.price_provider = PriceProvider(config)

        # Clients (use debug client if no Tessie token)
        self._debug_mode = config.debug_mode
        if self._debug_mode:
            logger.info("🐛 DEBUG MODE active: using simulated vehicle. All actions logged.")
            self.tessie = DebugTessieClient(config)
        else:
            self.tessie = TessieClient(config)

        # State
        self.current_plan: Optional[ChargingPlan] = None
        self.prices: Dict[int, float] = {}
        self.prices_date: str = ""  # YYYY-MM-DD the prices are for
        self.prices_fetched_today: bool = False
        self.planned_today: bool = False
        self.expected_by_hour: Dict[int, float] = {}  # hour → expected battery %
        self.last_state_time: Optional[datetime] = None
        self.running = True
        self._day_tracker: str = ""  # Track which day we're on
        self._today_early_plan_done: bool = False  # Whether we already planned for today's remaining hours

        # Planner
        self.planner = ChargePlanner(config)

        # Telegram bot (wired with callbacks)
        self.telegram = self._build_telegram()

        # Signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        # Write initial status (PID so other commands know we're running)
        write_status(daemon_pid=os.getpid(), daemon_mode="daemon")

    def _shutdown(self, signum: int = 0, frame: object = None) -> None:
        logger.info("Shutting down Tesla-PVPC daemon...")
        self.running = False

    # ------------------------------------------------------------------
    # Telegram callbacks
    # ------------------------------------------------------------------

    def _build_telegram(self) -> TelegramBot:
        return build_bot(
            self.cfg,
            get_status_fn=self._cmd_status,
            force_plan_fn=self._cmd_force_plan,
            start_charge_fn=self._cmd_start_charge,
            stop_charge_fn=self._cmd_stop_charge,
            set_config_fn=self._cmd_set,
        )

    def _cmd_status(self) -> str:
        state = self.tessie.get_state()
        if state is None:
            return "❌ No se puede contactar con el vehículo (Tessie)."

        lines = [
            f"🚗 *Estado del vehículo*",
            f"🔋 Batería: {state.battery_pct:.1f}%",
            f"🔌 {'Enchufado' if state.is_plugged_in else 'No enchufado'} | "
            f"{'Cargando ⚡' if state.is_charging else 'Parado ⏸️'}",
            f"🎯 Límite: {state.charge_limit_pct:.0f}%",
        ]

        if state.charger_power_kw > 0:
            lines.append(f"⚡ Potencia: {state.charger_power_kw:.1f} kW")

        if self.current_plan:
            lines.append("")
            lines.append(f"📋 *Plan actual*")
            lines.append(f"  Meta: {self.current_plan.target_pct:.0f}% a las {self.cfg.target_time}")
            lines.append(f"  Esperado: {self.current_plan.expected_final_pct:.1f}%")
            lines.append(f"  Coste: {self.current_plan.total_cost_eur:.3f} €")
            for s in self.current_plan.slots:
                lines.append(f"  ▸ {s.start_hour:02d}:00-{s.end_hour:02d}:00 ({s.kwh_to_deliver:.1f}kWh)")
        else:
            lines.append("")
            lines.append("📋 Sin plan activo.")

        return "\n".join(lines)

    def _cmd_force_plan(self) -> str:
        self._fetch_prices()
        self._create_plan()
        if self.current_plan:
            return f"✅ Plan recalculado:\n{self.current_plan.summary()}"
        return "⚠️ No se pudo crear un plan."

    def _cmd_start_charge(self) -> str:
        success = self.tessie.start_charge()
        return "✅ Comando de carga enviado." if success else "❌ Error al enviar comando de carga."

    def _cmd_stop_charge(self) -> str:
        success = self.tessie.stop_charge()
        return "✅ Comando de parada enviado." if success else "❌ Error al enviar comando de parada."

    def _cmd_set(self, chat_id: str, args: str) -> str:
        if not args:
            return "Uso: `/set <clave> <valor>`\nEj: `/set max_price_cents_per_kwh 8`"

        parts = args.split(maxsplit=1)
        if len(parts) < 2:
            return "Uso: `/set <clave> <valor>`\nEj: `/set min_battery_pct 80`"

        key, value = parts[0], parts[1]

        # Coerce types
        try:
            if value.lower() in ("true", "false"):
                value = value.lower() == "true"
            elif "." in value:
                value = float(value)
            else:
                value = int(value)
        except ValueError:
            pass  # Keep as string

        allowed = {
            "max_price_cents_per_kwh",
            "max_charger_power_kw",
            "min_battery_pct",
            "strict_mode",
            "charging_efficiency",
            "target_time",
            "check_interval_minutes",
        }

        if key not in allowed:
            return f"❌ Clave no permitida: `{key}`\nPermitidas: {', '.join(sorted(allowed))}"

        try:
            self.cfg.set(key, value)
            # Reload the planner with new config
            self.planner = ChargePlanner(self.cfg)
            return f"✅ `{key}` = `{value}`\nLos cambios se aplicarán en el próximo ciclo."
        except Exception as e:
            return f"❌ Error: {e}"

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Run the daemon loop forever (or until shutdown)."""
        logger.info("=" * 50)
        logger.info("Tesla-PVPC daemon started.")
        logger.info(f"Target: {self.cfg.min_battery_pct}% by {self.cfg.target_time}")
        logger.info(f"Charger: {self.cfg.max_charger_power_kw}kW | Battery: {self.cfg.battery_capacity_kwh}kWh")
        logger.info(f"Max price: {self.cfg.max_price_cents_per_kwh}c/kWh | Strict: {self.cfg.strict_mode}")
        logger.info(f"Efficiency: {self.cfg.charging_efficiency:.0%} | Check: every {self.cfg.check_interval_minutes}min")
        logger.info(f"Debug: {'ON (simulated)' if self._debug_mode else 'OFF (real vehicle)'}")
        logger.info(f"Telegram: {'enabled' if self.cfg.telegram_enabled else 'disabled'}")
        logger.info("=" * 50)

        # In debug mode, fetch prices immediately and create a plan right away
        if self._debug_mode:
            logger.info("[DEBUG] Immediate startup: fetching prices and creating plan now...")
            self._fetch_prices()
            if self.prices:
                self._create_plan()

        if self.cfg.telegram_enabled:
            self.telegram.send_message("🚀 Tesla-PVPC daemon *iniciado*.")

        while self.running:
            try:
                self._tick()
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                time.sleep(30)

    def _tick(self) -> None:
        """One iteration of the daemon loop."""
        now = now_spain()
        today = today_str()
        interval = self.cfg.check_interval_minutes

        # Reset daily flags if the day changed
        if today != self._day_tracker:
            logger.info(f"New day: {today}. Resetting daily flags.")
            self._day_tracker = today
            self.prices_fetched_today = False
            self.planned_today = False
            self.current_plan = None
            self.expected_by_hour = {}
            self._today_early_plan_done = False

        # --- 1. Poll Telegram for commands ---
        if self.cfg.telegram_enabled:
            self.telegram.poll()

        # --- Debug: log current tick ---
        if self._debug_mode:
            logger.info(f"[DEBUG] Tick at {now.strftime('%H:%M:%S')} | Day: {today}{' (NEW DAY)' if today != self._day_tracker else ''}")

        # --- 2a. EARLY: Fetch today's prices and plan for TODAY's remaining hours ---
        # Only do this once per day, when we're before the tomorrow-plan window (20:15)
        if (not self._today_early_plan_done
                and not self._debug_mode
                and now.hour < 20
                and now.hour < self.cfg.target_hour):
            logger.info(f"📅 Planificando para HOY ({today}) desde las {now.hour:02d}:00 hasta las {self.cfg.target_time}...")
            today_prices = self.price_provider.fetch_daily_prices(today)
            if today_prices and len(today_prices) >= 20:
                self.prices = today_prices
                self.prices_date = today
                self._create_plan(current_hour_override=now.hour)
                if self.current_plan:
                    source_label = {
                        "esios": "ESIOS",
                        "redata": "REData (mayorista)",
                    }.get(self.price_provider.last_source, "desconocido")
                    logger.info(f"📋 Plan para HOY desde las {now.hour:02d}:00: {len(self.current_plan.slots)} bloque(s)")
                    if self.cfg.telegram_enabled:
                        self.telegram.send_message(
                            f"📋 *Plan de HOY para {today}* (fuente: {source_label})\n"
                            f"🕐 Desde las {now.hour:02d}:00 hasta las {self.cfg.target_time}\n"
                            f"{self.current_plan.summary()}"
                        )
            else:
                logger.warning(f"No se pudieron cargar precios de HOY ({today}). "
                              "Se esperará a las 20:15 para los precios de mañana.")
            self._today_early_plan_done = True

        # --- 2. Fetch tomorrow's prices at 20:15 (or any time in debug) ---
        if self._debug_mode and not self.prices_fetched_today:
            self._fetch_prices()
        elif now.hour >= 20 and now.minute >= 15 and not self.prices_fetched_today:
            self._fetch_prices()

        # --- 3. Plan for tomorrow at 21:00 if prices are ready (or immediately in debug) ---
        should_plan = now.hour >= 21 and not self.planned_today and bool(self.prices)
        if self._debug_mode and not self.planned_today and self.prices:
            should_plan = True
            logger.info("[DEBUG] Debug mode: triggering plan creation immediately.")
        if should_plan:
            self._create_plan(current_hour_override=0)

        # --- 4. Enforce the current plan ---
        if self.current_plan:
            if self._debug_mode:
                logger.info(f"[DEBUG] Enforcing plan: {len(self.current_plan.slots)} slot(s) active")
            self._enforce_plan(now)
            self._check_progress(now)

        # --- 5. Set charge limit (only when there's an active plan) ---
        if self.current_plan:
            self._ensure_charge_limit()

        # --- Write status for external commands (--prices, --dashboard) ---
        self._write_status()

        # --- 6. Sleep (aligned to clock + slot boundaries) ---
        next_wake = self._next_wake_time(now, interval)
        if next_wake is None:
            next_wake = now + timedelta(minutes=interval)
        sleep_seconds = (next_wake - now).total_seconds()
        next_action = self._next_action_description(now)
        logger.info(f"💤 Despertando a las {next_wake.strftime('%H:%M')} ({next_action})")
        # Sleep in small increments for responsive Telegram polling
        elapsed = 0
        while elapsed < sleep_seconds and self.running:
            chunk = min(10, sleep_seconds - elapsed)
            time.sleep(chunk)
            elapsed += chunk
            if self.cfg.telegram_enabled:
                self.telegram.poll()
        logger.info(f"⏰ {next_wake.strftime('%H:%M')} — despertando para nuevo ciclo.")

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def _fetch_prices(self) -> None:
        """Fetch tomorrow's electricity prices from ESIOS."""
        tomorrow = tomorrow_str()

        if self._debug_mode:
            logger.info(f"[DEBUG] _fetch_prices() called → target date: {tomorrow}")

        if self.prices_date == tomorrow and self.prices:
            logger.info("Prices for tomorrow already loaded.")
            self.prices_fetched_today = True
            return

        prices = self.price_provider.fetch_daily_prices(tomorrow)
        if prices and len(prices) >= 20:
            self.prices = prices
            self.prices_date = tomorrow
            self.prices_fetched_today = True

            source_label = {
                "esios": "ESIOS oficial",
                "redata": "REData público (proxy mayorista)",
            }.get(self.price_provider.last_source, self.price_provider.last_source)

            if self._debug_mode:
                cheapest = min(prices.values())
                most_expensive = max(prices.values())
                avg = sum(prices.values()) / len(prices)
                logger.info(
                    f"[DEBUG] Prices loaded from {source_label} for {tomorrow}: "
                    f"min={cheapest:.1f}, max={most_expensive:.1f}, avg={avg:.1f} c/kWh"
                )
                for h in sorted(prices.keys()):
                    logger.info(f"[DEBUG]   Hour {h:02d}:00 → {prices[h]:.2f} c/kWh")
            else:
                logger.info(f"💰 Precios cargados desde {source_label} ({len(prices)}h).")

            if self.cfg.telegram_enabled:
                cheapest = min(prices.values())
                most_expensive = max(prices.values())
                avg = sum(prices.values()) / len(prices)
                self.telegram.send_message(
                    f"📊 *Precios para {tomorrow} ({source_label})*\n"
                    f"Mín: {cheapest:.1f} c/kWh\n"
                    f"Máx: {most_expensive:.1f} c/kWh\n"
                    f"Media: {avg:.1f} c/kWh"
                )
        else:
            logger.error(
                f"Failed to fetch prices for {tomorrow} (got {len(prices)} hours). "
                "Will retry next cycle."
            )

    def _create_plan(self, current_hour_override: Optional[int] = None) -> None:
        """Create a charging plan.

        Args:
            current_hour_override: If set (e.g. current hour), plans from that hour until
                                   target_time. If None or 0, plans from midnight (tomorrow plan).
        """
        if self._debug_mode:
            logger.info("[DEBUG] _create_plan() called → getting vehicle state...")

        state = self.tessie.get_state()
        if state is None:
            logger.warning("Cannot create plan: vehicle unreachable.")
            return

        current_pct = state.battery_pct
        start_hour = current_hour_override if current_hour_override is not None else 0

        label = "HOY" if start_hour > 0 else "MAÑANA"

        if self._debug_mode:
            logger.info(
                f"[DEBUG] Creating {label} plan: battery={current_pct:.1f}%, "
                f"from_hour={start_hour}, "
                f"target={self.cfg.min_battery_pct:.0f}%, "
                f"max_price={self.cfg.max_price_cents_per_kwh}c/kWh, "
                f"power={self.cfg.max_charger_power_kw}kW, "
                f"capacity={self.cfg.battery_capacity_kwh}kWh, "
                f"efficiency={self.cfg.charging_efficiency:.0%}, "
                f"deadline={self.cfg.target_time}, "
                f"strict={self.cfg.strict_mode}"
            )

        # Plan from start_hour until target_time
        plan = self.planner.plan(
            prices=self.prices,
            current_battery_pct=current_pct,
            current_hour=start_hour,
            date_str=self.prices_date,
        )

        if plan.slots:
            self.current_plan = plan
            self.planned_today = True
            self._compute_expected_by_hour(plan, current_pct, self.prices_date)

            if self._debug_mode:
                logger.info(f"[DEBUG] {label} plan created successfully:")
                logger.info(plan.summary())
                logger.info(f"[DEBUG] Expected battery by hour (progress tracker):")
                for h in range(24):
                    ep = self.expected_by_hour.get(h)
                    if ep is not None and h <= self.cfg.target_hour:
                        charging = "⚡" if any(s.start_hour <= h < s.end_hour for s in plan.slots) else "  "
                        logger.info(f"[DEBUG]   {h:02d}:00 → {ep:.1f}% {charging}")
            else:
                logger.info(f"📋 Plan de {label} creado: {len(plan.slots)} bloque(s) → "
                           f"{plan.expected_final_pct:.1f}% (coste: {plan.total_cost_eur:.3f} €)")

            if self.cfg.telegram_enabled:
                self.telegram.send_message(
                    f"📋 *Plan de carga para {self.prices_date}*\n{plan.summary()}"
                )
        else:
            logger.info(f"No charging needed or no cheap hours available ({label}).")
            self.current_plan = None
            self.planned_today = True

    def _compute_expected_by_hour(
        self,
        plan: ChargingPlan,
        starting_pct: float,
        date_str: str,
    ) -> None:
        """Pre-compute expected battery % at each hour for progress tracking."""
        self.expected_by_hour = {}
        capacity = self.cfg.battery_capacity_kwh
        pct = starting_pct

        for h in range(24):
            # Check if we're charging this hour
            charging = False
            for slot in plan.slots:
                if slot.start_hour <= h < slot.end_hour:
                    charging = True
                    break

            if charging:
                pct += (self.cfg.max_charger_power_kw * self.cfg.charging_efficiency / capacity) * 100.0

            self.expected_by_hour[h] = min(pct, 100.0)

    def _enforce_plan(self, now: datetime) -> None:
        """Start or stop charging based on the current plan."""
        state = self.tessie.get_state()
        if state is None:
            return

        current_hour = now.hour

        charge_now = False
        for slot in self.current_plan.slots:
            if slot.start_hour <= current_hour < slot.end_hour:
                charge_now = True
                break

        if charge_now and state.is_plugged_in:
            if not state.is_charging:
                logger.info(f"Hour {current_hour}: should be charging → sending START.")
                self.tessie.start_charge()
                if self.cfg.telegram_enabled:
                    self.telegram.send_message(
                        f"⚡ Carga *iniciada* a las {current_hour:02d}:00 "
                        f"(batería: {state.battery_pct:.1f}%)"
                    )
        elif charge_now and not state.is_plugged_in:
            logger.warning(f"Hour {current_hour}: should be charging but car is NOT plugged in!")
            if self.cfg.telegram_enabled:
                self.telegram.send_message(
                    f"⚠️ *{current_hour:02d}:00* — Debería estar cargando pero el coche *no está enchufado*!"
                )
        elif not charge_now and state.is_charging:
            logger.info(f"Hour {current_hour}: should NOT be charging → sending STOP.")
            self.tessie.stop_charge()
            if self.cfg.telegram_enabled:
                self.telegram.send_message(
                    f"⏸️ Carga *detenida* a las {current_hour:02d}:00 "
                    f"(batería: {state.battery_pct:.1f}%)"
                )

    def _check_progress(self, now: datetime) -> None:
        """Check if battery progress is on track; replan if behind."""
        current_hour = now.hour
        expected = self.expected_by_hour.get(current_hour)
        if expected is None:
            return

        state = self.tessie.get_state()
        if state is None:
            return

        actual_pct = state.battery_pct
        deficit = expected - actual_pct

        if self._debug_mode:
            logger.info(
                f"[DEBUG] Progress check hour {current_hour}: "
                f"expected={expected:.1f}%, actual={actual_pct:.1f}%, "
                f"deficit={deficit:.1f}% | target={self.cfg.min_battery_pct:.0f}%"
            )
        if actual_pct >= self.cfg.min_battery_pct:
            logger.info(f"Target {self.cfg.min_battery_pct}% reached! ({actual_pct:.1f}%)")
            if self.current_plan:
                # Stop charging and clear plan
                self.tessie.stop_charge()
                self.current_plan = None
                if self.cfg.telegram_enabled:
                    self.telegram.send_message(
                        f"✅ *Objetivo alcanzado*: {actual_pct:.1f}% 🎉"
                    )
            return

        if deficit > 3.0:
            logger.warning(
                f"Behind schedule at hour {current_hour}: "
                f"expected {expected:.1f}%, actual {actual_pct:.1f}% (deficit: {deficit:.1f}%)"
            )

            new_plan = self.planner.replan(
                prices=self.prices,
                current_battery_pct=actual_pct,
                current_hour=current_hour,
                date_str=self.prices_date,
                expected_pct=expected,
            )

            if new_plan:
                self.current_plan = new_plan
                self._compute_expected_by_hour(new_plan, actual_pct, self.prices_date)
                if self.cfg.telegram_enabled:
                    self.telegram.send_message(
                        f"🔄 *Plan recalculado* (déficit de {deficit:.1f}%)\n{new_plan.summary()}"
                    )

    def _ensure_charge_limit(self) -> None:
        """Make sure the car's charge limit is at least our target."""
        state = self.tessie.get_state()
        if state is None:
            return

        target = max(int(self.cfg.min_battery_pct), 50)
        if state.charge_limit_pct < target:
            logger.info(f"Charge limit {state.charge_limit_pct:.0f}% < target {target}% → adjusting.")
            self.tessie.set_charge_limit(target)

    def _write_status(self) -> None:
        """Write current daemon state to /tmp/autocharge-status.json."""
        veh_state = None
        try:
            s = self.tessie.get_state()
            if s:
                veh_state = {
                    "battery_pct": round(s.battery_pct, 1),
                    "is_charging": s.is_charging,
                    "is_plugged_in": s.is_plugged_in,
                    "charge_limit_pct": s.charge_limit_pct,
                    "charger_power_kw": s.charger_power_kw,
                }
        except Exception:
            pass

        plan_data = None
        if self.current_plan:
            plan_data = {
                "target_pct": self.current_plan.target_pct,
                "expected_pct": round(self.current_plan.expected_final_pct, 1),
                "total_kwh": round(self.current_plan.total_kwh, 1),
                "total_cost_eur": round(self.current_plan.total_cost_eur, 3),
                "slots": [{"start": s.start_hour, "end": s.end_hour, "price": round(s.price_cents_per_kwh, 1), "kwh": round(s.kwh_to_deliver, 1)} for s in self.current_plan.slots],
            }

        prices_summary = {}
        if self.prices:
            vals = list(self.prices.values())
            prices_summary = {
                "min": round(min(vals), 1),
                "max": round(max(vals), 1),
                "avg": round(sum(vals) / len(vals), 1),
                "count": len(vals),
                "date": self.prices_date,
            }

        write_status(
            daemon_pid=os.getpid(),
            daemon_mode="debug" if self._debug_mode else "daemon",
            vehicle=veh_state,
            plan=plan_data,
            prices_summary=prices_summary,
            prices=self.prices if self.prices else {},
            prices_date=self.prices_date,
            expected_by_hour=self.expected_by_hour,
            config={
                "target_time": self.cfg.target_time,
                "min_battery_pct": self.cfg.min_battery_pct,
                "max_price": self.cfg.max_price_cents_per_kwh,
                "strict_mode": self.cfg.strict_mode,
                "efficiency": self.cfg.charging_efficiency,
                "charger_power": self.cfg.max_charger_power_kw,
                "battery_capacity": self.cfg.battery_capacity_kwh,
            },
        )

    def _next_wake_time(self, now: datetime, interval_minutes: int) -> Optional[datetime]:
        """Calculate the next time the daemon should wake up.

        Considers:
        1. Next aligned interval boundary (e.g. :00/:15/:30/:45 if interval=15)
        2. Next slot start time (if there's an active plan)
        Returns the earliest of the two, or None if can't calculate.
        """
        candidates: list[datetime] = []

        # 1. Next interval-aligned boundary
        # Round current minute up to the next multiple of interval
        current_minute = now.hour * 60 + now.minute
        next_boundary_minute = ((current_minute // interval_minutes) + 1) * interval_minutes
        if next_boundary_minute >= 24 * 60:
            # Roll over to midnight next day
            candidates.append((now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0))
        else:
            boundary_hour = next_boundary_minute // 60
            boundary_min = next_boundary_minute % 60
            candidates.append(now.replace(hour=boundary_hour, minute=boundary_min, second=0, microsecond=0))

        # 2. Next slot start time (if plan active and slot starts after now)
        if self.current_plan:
            current_hour = now.hour
            for slot in self.current_plan.slots:
                if slot.start_hour > current_hour:
                    slot_time = now.replace(hour=slot.start_hour, minute=0, second=0, microsecond=0)
                    if slot_time > now:
                        candidates.append(slot_time)
                    break  # Only the next slot matters

        if not candidates:
            return None

        # Earliest candidate that's after now
        future = [c for c in candidates if c > now]
        return min(future) if future else None

    def _next_action_description(self, now: datetime) -> str:
        """Describe what will happen next tick (for sleep logging)."""
        parts = []
        if self.current_plan:
            parts.append(f"⚡ {len(self.current_plan.slots)} slot(s) activos")
            # Show next charging slot if not currently charging
            current_hour = now.hour
            next_slot = None
            for slot in self.current_plan.slots:
                if slot.start_hour > current_hour:
                    next_slot = slot
                    break
            if next_slot:
                parts.append(f"próxima carga {next_slot.start_hour:02d}:00")
            else:
                parts.append("ejecutando plan")
        else:
            if self._today_early_plan_done and self.current_plan is None:
                parts.append(f"plan HOY sin slots disponibles")
            elif not self._today_early_plan_done and now.hour < 20 and now.hour < self.cfg.target_hour:
                parts.append(f"planificando HOY...")
            elif self.prices_fetched_today:
                if now.hour < 21:
                    parts.append(f"plan MAÑANA a las 21:00")
                else:
                    parts.append("esperando plan")
            else:
                parts.append(f"precios MAÑANA a las 20:15")
        return " | ".join(parts) if parts else _t("daemon.waiting")


def run_daemon(config_path: Optional[str] = None) -> None:
    """Entry point: load config and run the daemon."""
    from auto_charge.config import CONFIG_PATH

    path = config_path or CONFIG_PATH
    try:
        cfg = Config(path)
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    daemon = AutoChargeDaemon(cfg)
    daemon.run()
