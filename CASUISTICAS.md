# Casuísticas de Planificación — Tesla-PVPC

## 1. Dimensiones de Variación

El planificador de carga (`ChargePlanner.plan()`) varía según estas dimensiones:

| Dimensión | Valores | Efecto |
|-----------|---------|--------|
| `current_hour` vs `target_hour` | antes / después | Ventana intradía vs cross-midnight |
| `current_battery_pct` vs `min_battery_pct` | >= target / < target | No carga vs planifica |
| `strict_mode` | True / False | Incluye horas caras o no |
| Precios mañana disponibles | Sí / No | Merge offset +24 vs truncar a hoy |
| Distribución precios | Todos baratos / Mixto / Todos caros | Horas seleccionadas |
| Estado vehículo | Enchufado / No enchufado | Ejecuta / Adverte |
| Debug / Producción | debug / production | Flujo diferente del daemon |

---

## 2. Escenarios Completos

### A. Ventana intradía (current_hour < target_hour)

#### A1. Normal — sobran horas baratas
- **Hora:** 09:00, **target:** 19:00, **batería:** 60% → 70% (necesita ~3h)
- **Precios:** 24h normales (5–15 c/kWh), todas bajo max_price
- **Resultado:** Slot único con las 3h más baratas consecutivas (si aplica)
- **Assert:** `len(slots) == 1`, coste razonable, `will_reach_target == True`

#### A2. Horas justas — necesitas todas las horas disponibles
- **Hora:** 17:00, **target:** 19:00, **batería:** 50% → 70% (necesita ~7h)
- **Precios:** 24h normales
- **Ventana disponible:** solo 2h (17, 18)
- **Resultado:** Flexible → 2 slots (o 1) con solo ~6kWh, no llega al target
- **Assert:** `will_reach_target == False`, `len(slots) <= 2`

#### A3. Batería ya en target
- **Hora:** 09:00, **target:** 19:00, **batería:** 75% (target: 70%)
- **Resultado:** `kwh_needed <= 0` → plan vacío
- **Assert:** `plan.slots == []`, `plan.expected_final_pct == 75.0`

#### A4. Todos los precios caros
- **Hora:** 09:00, **target:** 19:00, **batería:** 50% → 70%
- **Precios:** Todos > max_price (ej: 20–30 c/kWh, max_price=10)
- **Strict=True:** Incluye horas caras para llegar al target
- **Strict=False:** Plan vacío (no hay horas baratas)
- **Assert (strict):** `len(slots) > 0`, `will_reach_target == True`
- **Assert (flexible):** `plan.slots == []`

#### A5. Solo 1h disponible
- **Hora:** 18:00, **target:** 19:00, **batería:** 50% → 70%
- **Ventana:** solo [18]
- **Resultado:** 1 slot, 1h de carga, no llega al target
- **Assert:** `len(slots) == 1`, `will_reach_target == False`

#### A6. Boundary current_hour == target_hour
- **Hora:** 19:00, **target:** 19:00
- **current_hour == target_hour** → entra en cross-midnight (else) → [19..23] + [24..42]
- **Sin mañana:** truncation guard → solo [19..23] = 5h
- **Resultado:** Plan truncado a hoy, no llega al target
- **Assert:** `len(slots) > 0`, `slot.end_hour <= 24`, `will_reach_target == False`

#### A7. Agrupación de slots no consecutivos
- **Hora:** 09:00, **target:** 19:00, **batería:** 50% → 70% (necesita ~6h)
- **Precios:** baratos en 10, 12, 14, 15, 16, 18 (no consecutivos todos)
- **Resultado:** Múltiples slots separados
- **Assert:** `len(slots) >= 2`

#### A8. Slot único consecutivo
- **Hora:** 09:00, **target:** 19:00, **batería:** 50% → 70%
- **Precios:** baratos en 10, 11, 12, 13, 14, 15 (6h consecutivas)
- **Resultado:** 1 slot de 6h
- **Assert:** `len(slots) == 1`, `slots[0].start_hour == 10`, `slots[0].end_hour == 16`

---

### B. Cross-midnight (current_hour >= target_hour)

#### B1. Cross-midnight con precios de mañana
- **Hora:** 21:00, **target:** 19:00, **batería:** 35% → 70%
- **Precios:** hoy + mañana (merge offset +24)
- **Ventana:** [21, 22, 23] + [24, 25, ..., 42]
- **Resultado:** Slots que cruzan medianoche con horas 24+
- **Assert:** `slots` contiene horas >= 24

#### B2. Cross-midnight SIN precios de mañana (truncation guard)
- **Hora:** 21:00, **target:** 19:00, **batería:** 35% → 70%
- **Precios:** solo hoy (0-23), mañana falló
- **Ventana truncada:** solo [21, 22, 23]
- **Resultado:** Plan solo con horas de hoy, warning de truncation
- **Assert:** Todas las horas del slot < 24, `will_reach_target == False`

#### B3. Cross-midnight con batería muy baja
- **Hora:** 22:00, **target:** 08:00, **batería:** 10% → 80%
- **Precios:** hoy + mañana disponibles
- **Ventana:** [22, 23] + [24, 25, ..., 31] = 10h
- **Necesita:** ~22h → no cabe en ventana
- **Resultado:** Flexible, máximo posible
- **Assert:** `will_reach_target == False`

#### B4. Cross-midnight con current_hour == 23
- **Hora:** 23:00, **target:** 07:00, **batería:** 60% → 70%
- **Ventana:** [23] + [24, 25, ..., 30] = 8h
- **Resultado:** Plan normal cross-midnight
- **Assert:** `len(slots) > 0`, horas incluyen 23 y/o 24+

#### B5. Cross-midnight sin mañana, current_hour == 23
- **Hora:** 23:00, **target:** 07:00, **batería:** 60% → 70%
- **Precios:** solo hoy
- **Ventana truncada:** solo [23] (1h)
- **Resultado:** 1 slot de 1h
- **Assert:** `len(slots) == 1`, `slots[0].start_hour == 23`

#### B6. Cross-midnight con target_hour = 0 (medianoche)
- **Hora:** 22:00, **target:** 00:00, **batería:** 50% → 70%
- **Ventana:** [22, 23] + [24..24] = solo 2h (el range(24, 24) es vacío)
- **Resultado:** Ventana muy pequeña, probablemente no llega
- **Nota:** target_hour=0 es un edge case poco realista pero posible

---

### C. Precios — Casos Especiales

#### C1. Precios centinela en toda la ventana
- **Precios:** Todos = `_MISSING_PRICE_SENTINEL` (500.0)
- **Resultado:** `real_prices` vacío → plan vacío
- **Assert:** `plan.slots == []`

#### C2. Algunos precios centinela + algunos reales
- **Precios:** Mezcla de reales (< 10) y sentinel (500)
- **Resultado:** Solo selecciona horas con precio real
- **Assert:** Ningún slot tiene precio ~500

#### C3. Precios con max_price muy restrictivo
- **max_price = 5 c/kWh**, precios normales 7–15 c/kWh
- **Resultado:** `cheap_hours` vacío → strict añade caros / flexible plan vacío
- **Assert (strict):** usa expensive_hours

#### C4. Precios con max_price muy permisivo
- **max_price = 100 c/kWh**, precios normales 7–15 c/kWh
- **Resultado:** Todas las horas en cheap_hours
- **Assert:** `expensive_hours == []`

#### C5. max_price = 0 (sin horas baratas)
- **max_price = 0 c/kWh**, todos los precios > 0
- **Resultado:** `cheap_hours` siempre vacío
- **Assert (strict):** usa expensive_hours para llegar al target
- **Assert (flexible):** `plan.slots == []`

---

### D. Display y formato de horas

#### D1. `_hour_label` para horas 0-23
- `_hour_label(0)` → `"00:00"`
- `_hour_label(12)` → `"12:00"`
- `_hour_label(23)` → `"23:00"`

#### D2. `_hour_label` para horas 24+
- `_hour_label(24)` → `"+1d 00:00"`
- `_hour_label(25)` → `"+1d 01:00"`
- `_hour_label(47)` → `"+1d 23:00"`
- `_hour_label(48)` → `"+2d 00:00"` (día siguiente + 1)

#### D3. `_format_slot_hours` en dashboard
- Slot `{start: 21, end: 24}` → `"21:00-+1d 00:00"`
- Slot `{start: 24, end: 30}` → `"+1d 00:00-+1d 06:00"`

---

### E. `_slot_covers_hour` — Ejecución del plan

#### E1. Slot de hoy (start < 24)
- `slot(9, 12)`, `current_hour=10` → `9 <= 10 < 12` → **True** ✅
- `slot(9, 12)`, `current_hour=12` → `9 <= 12 < 12` → **False** ✅ (exclusive end)
- `slot(9, 12)`, `current_hour=8` → `9 <= 8 < 12` → **False** ✅

#### E2. Slot de mañana (start >= 24)
- `slot(24, 27)`, `current_hour=0` → `24 <= 24 < 27` → **True** ✅ (mañana 00:00)
- `slot(24, 27)`, `current_hour=2` → `24 <= 26 < 27` → **True** ✅ (mañana 02:00)
- `slot(24, 27)`, `current_hour=3` → `24 <= 27 < 27` → **False** ✅

#### E3. Slot que cruza medianoche (start < 24, end = 24)
- `slot(21, 24)`, `current_hour=21` → `21 <= 21 < 24` → **True** ✅
- `slot(21, 24)`, `current_hour=23` → `21 <= 23 < 24` → **True** ✅
- `slot(21, 24)`, `current_hour=0` → `21 <= 0 < 24` → **False** ✅ (medianoche pasada)

#### E4. Ya cargando y debería cargar (logging gap)
- `charge_now=True`, `is_plugged_in=True`, `is_charging=True`
- **Resultado:** No se logea nada, no se envía comando (no-op silencioso)
- **Assert:** No se llama a `start_charge()`

#### E5. Ya parado y no debería cargar (logging gap)
- `charge_now=False`, `is_plugged_in=True`, `is_charging=False`
- **Resultado:** No se logea nada (no-op silencioso)
- **Assert:** No se llama a `stop_charge()`

---

### F. Progreso y Replan

#### F1. On track
- `expected_by_hour[10] = 45.0`, `actual = 44.0` → déficit = 1.0 < 3.0 → no replan
- **Assert:** `replan()` returns `None`

#### F2. Behind schedule (déficit > 3%)
- `expected_by_hour[14] = 65.0`, `actual = 55.0` → déficit = 10.0 > 3.0 → replan
- **Assert:** `replan()` returns new plan

#### F3. Target reached
- `actual = 72.0`, `min_battery_pct = 70.0` → target reached → stop charging + clear plan
- **Assert:** `_check_progress()` llama a `stop_charge()` y pone `current_plan = None`

#### F4. Déficit pero no se encuentra mejor plan
- Replan no produce un plan mejor → se mantiene el plan actual
- **Assert:** `current_plan` no cambia

#### F5. Múltiples deficits consecutivos
- Varios ticks seguidos con deficit > 3% → múltiples replans
- **Resultado:** El plan se recalcula varias veces, posiblemente con resultados similares
- **Assert:** Cada replan produce un ChargingPlan válido

#### F6. expected_by_hour no tiene la hora actual
- `expected_by_hour.get(current_hour)` returns `None` → no-op
- **Assert:** `_check_progress()` retorna sin hacer nada

---

### G. Daemon — Máquina de estados del día

#### G1. Amanecer (early plan)
- `now.hour < target_hour`, `_today_early_plan_done = False`
- `_debug_mode = False` (nunca en debug)
- → fetch today prices + create plan
- `_today_early_plan_done = True`

#### G2. Atardecer (fetch tomorrow a las 20:15)
- `now.hour >= 20 and now.minute >= 15`, `prices_fetched_today = False`
- → `_fetch_prices(include_tomorrow=True)` → merge offset +24
- `prices_fetched_today = True`

#### G3. Plan cross-midnight
- `now.hour >= target_hour`, `planned_today = False`, `prices` non-empty
- → `_create_plan(current_hour_override=now.hour)`

#### G4. Debug mode — plan inmediato en startup
- `_debug_mode = True`, startup → fetch + plan sin esperar tick
- Además: en `_tick()`, debug mode siempre `include_tomorrow=True`
- Además: en `_tick()`, debug mode salta early plan (G1) y planifica en step 4 aunque sea de día

#### G5. Cambio de día (medianoche)
- `today != _day_tracker` → reset: `prices_fetched_today = False`, `planned_today = False`
- También: `current_plan = None`, `expected_by_hour = {}`, `_today_early_plan_done = False`
- **Assert:** Flags de día anterior se limpian correctamente

#### G6. Precios de mañana fallan en 20:15 → retry
- `prices_fetched_today` se queda `False`
- En el siguiente tick, step 3 vuelve a intentar fetch mañana
- Sigue reintentando hasta que mañana tenga datos
- **Mientras tanto:** step 4 crea plan con solo hoy (truncation guard en planner)

#### G7. Debug mode: fetch mañana incluso de día
- Debug mode a las 09:00: `self._fetch_prices(include_tomorrow=True)`
- En producción esto no pasaría hasta las 20:15
- **Resultado:** Puede fallar si mañana no tiene datos aún (esperado)

---

### H. Estado del vehículo

#### H1. Vehículo no enchufado — warning
- `charge_now=True`, `is_plugged_in=False`
- **Resultado:** Warning: "should be charging but car is NOT plugged in!"
- **Assert:** No se llama a `start_charge()`

#### H2. Vehículo no enchufado — no planear carga
- `run_once()` detecta `not state.is_plugged_in` → warning "not plugged in"
- **Resultado:** No inicia carga aunque el plan diga que toca
- **Assert:** `logger.warning("Vehicle not plugged in.")`

#### H3. Vehicle state fetch fails (None)
- `tessie.get_state()` returns `None`
- **Resultado:** `_enforce_plan()` → return early sin hacer nada
- `_create_plan()` → warning "Cannot create plan: vehicle unreachable"
- **Assert:** No se lanza excepción

#### H4. Charge limit ya está bien → no-op
- `state.charge_limit_pct >= target`
- **Resultado:** `_ensure_charge_limit()` no hace nada
- **Assert:** No se llama a `set_charge_limit()`

#### H5. Charge limit bajo → se ajusta
- `state.charge_limit_pct < target`
- **Resultado:** Se llama a `tessie.set_charge_limit(target)`
- **Assert:** `target = max(int(min_battery_pct), 50)` (mínimo 50%)

---

### I. Config — Casos extremos

#### I1. target_time = "00:00" (medianoche)
- `target_hour = 0`
- `current_hour >= 0` SIEMPRE → cross-midnight siempre (incluso a las 00:01)
- **Resultado:** Nunca entra en intradía, siempre cross-midnight
- `available_window = [current_hour..23] + [24..24)` (range(24,24) = vacío)
- **Assert (mañana):** Ventana [current_hour..23] + [24..24) = solo hoy

#### I2. target_time = "23:59" (casi medianoche)
- `target_hour = 23`
- Intradía solo si `current_hour < 23` (casi todo el día)
- Cross-midnight si `current_hour = 23`
- **Resultado:** Ventana cross-midnight muy pequeña

#### I3. min_battery_pct = 100
- Siempre necesita cargar a tope
- **Resultado:** `kwh_needed` siempre positivo si battery < 100%
- **Assert:** `hours_needed` suele ser grande

#### I4. max_price_cents_per_kwh = 0
- `cheap_hours` siempre vacío (ningún precio real ≤ 0)
- **Strict:** Usa expensive_hours para llegar al target
- **Flexible:** Plan vacío siempre

#### I5. charging_efficiency = 0.5 o 1.0 (extremos)
- **0.5:** Se necesita el doble de horas para la misma energía
- **1.0:** Sin pérdidas, máxima eficiencia
- **Assert:** `hours_needed` varía proporcionalmente

#### I6. check_interval_minutes = 1 (agresivo)
- El daemon se despierta cada minuto
- **Resultado:** Mayor consumo de API, más reactividad
- **Assert:** `sleep_seconds` ≈ 60s

#### I7. Sin config.json (todo defaults)
- `Config()` con valores por defecto
- **Resultado:** target_hour=19, max_price=10, strict=True, etc.
- **Assert:** `cfg.tessie_token == ""` → debug mode

---

### J. `_next_wake_time` — Cálculo de despertador

#### J1. Sin plan activo → solo intervalo
- `self.current_plan = None`
- **Resultado:** Candidates solo incluye el siguiente borde del intervalo
- **Assert:** `next_wake` es el próximo :00/:15/:30/:45

#### J2. Con plan, slot empieza en el futuro → se añade como candidato
- `slot.start_hour > current_hour`
- **Resultado:** Se añade `slot_time` a candidates
- **Assert:** `len(candidates) >= 2`

#### J3. Slot empieza en el pasado → se salta
- `slot.start_hour <= current_hour`
- **Resultado:** No se añade a candidates
- **Assert:** Solo el intervalo está en candidates

#### J4. Slot con start_hour = 24 (mañana 00:00)
- `slot_start >= 24` → `days_ahead = slot_start // 24 = 1`
- `slot_start_clock = 0`
- `slot_time = now + timedelta(days=1)`, hour=0, minute=0
- **Assert:** `slot_time` es mañana a las 00:00

#### J5. Slot con start_hour = 48 (pasado mañana)
- `slot_start >= 24` → `days_ahead = 2`
- `slot_start_clock = 0`
- `slot_time = now + timedelta(days=2)`, hour=0, minute=0
- **Assert:** `slot_time` es pasado mañana a las 00:00

#### J6. Todos los candidatos en el pasado → next_wake = None
- `if slot_time > now` → no añade slots pasados
- `future = [c for c in candidates if c > now]`
- **Resultado:** Se usa fallback `now + timedelta(minutes=interval)`
- **Assert:** `next_wake` nunca es None (usa fallback)

#### J7. Borde de intervalo cruza medianoche
- `next_boundary_minute >= 24*60` → rollover a medianoche +1 día
- **Assert:** `next_wake` es medianoche del día siguiente

---

### K. Cálculos de agrupación de slots

#### K1. Slot de 1 hora (hora única)
- `selected_hours = [14]`
- **Resultado:** `start=14, end=15`
- **Assert:** `len(slots) == 1`, `slots[0].duration_hours == 1.0`

#### K2. Horas que saltan vía 24 (23 y 25 — gap en 24)
- `selected_hours = [23, 25]` (24 no está seleccionada)
- **Resultado:** 2 slots separados: `[23]` y `[25]`
- **Assert:** `len(slots) == 2`

#### K3. remaining_kwh muy pequeño (< 0.1)
- Al final de la distribución, queda muy poco kWh
- **Resultado:** `kwh = min(max_kwh, remaining_kwh)` → valor pequeño
- **Assert:** `slots[-1].kwh_to_deliver > 0`

#### K4. Muchas horas consecutivas (10+)
- `selected_hours` = 10+ horas seguidas
- **Resultado:** 1 slot muy largo
- **Assert:** `len(slots) == 1`, `slots[0].duration_hours >= 10`

---

### L. CLI y flujo de entrada

#### L1. --dashboard sin daemon
- `get_daemon_pid()` returns None
- **Resultado:** Mensaje: "No hay ningún daemon ejecutándose."
- **Assert:** No se abre dashboard

#### L2. --prices con daemon activo
- Lee precios del status file del daemon
- **Resultado:** Filtra claves 24+ (solo muestra 0-23)
- **Assert:** `prices` solo contiene horas 0-23

#### L3. --prices sin daemon
- Fetch directo de ESIOS/REData
- **Resultado:** Muestra precios del día actual
- **Assert:** `source` es "esios" o "redata"

#### L4. --once con vehículo no enchufado
- `state.is_plugged_in == False`
- **Resultado:** Warning + no inicia carga
- **Assert:** `logger.warning("Vehicle not plugged in.")` se muestra

#### L5. -b --dashboard (combo background + dashboard)
- Fork: padre espera status file (max 10s), luego abre dashboard
- Hijo: doble fork, setsid, se vuelve daemon
- **Resultado:** Daemon en background + dashboard en foreground
- **Assert:** Padre termina con `sys.exit(0)` después de dashboard

#### L6. Mata instancias previas al arrancar
- `_kill_existing_instances()` busca otros procesos tesla_pvpc
- SIGTERM → espera 1s → SIGKILL si persisten
- **Assert:** Solo mata procesos con PID diferente al actual

---

### M. Error handling y casos límite

#### M1. ESIOS devuelve < 20 horas (parcial)
- `len(prices) < 20` → warning + no usa datos parciales
- **Resultado:** Cae en REData fallback
- **Assert:** `fetch_daily_prices` retorna datos completos o vacío

#### M2. ESIOS 403 Unauthorized
- Token inválido o expirado
- **Resultado:** Cae en REData fallback
- **Assert:** No interrumpe el flujo

#### M3. Ambos providers fallan para HOY
- ESIOS fail + REData fail → `prices = {}`
- **run_once:** `if not prices: logger.error(...) + return`
- **Daemon:** `_fetch_prices` → `if not today_prices: return` (no actualiza self.prices)
- **Assert en daemon:** `self.prices` mantiene valores anteriores (si los hay)

#### M4. Ambos providers fallan para MAÑANA
- ESIOS fail + REData fail → truncation guard en planner
- **Assert:** Plan solo con horas de hoy

#### M5. Network timeout
- `requests.get(..., timeout=15)` lanza timeout
- **Resultado:** `request.RequestException` capturado → fallback
- **Assert:** No crashea

#### M6. Status file corrupto
- `/tmp/autocharge-status.json` tiene JSON inválido (daemon killed mid-write)
- **Resultado:** `json.JSONDecodeError` capturado → función retorna datos por defecto
- **Assert:** No crashea, muestra datos vacíos

#### M7. Error en el main loop del daemon
- `_tick()` lanza excepción no capturada
- **Resultado:** Capturada en `run()`, sleep 30s, reintenta
- **Assert:** Daemon no muere

---

### N. Acciones y descripciones del daemon

#### N1. Plan activo + próximo slot futuro
- `len(slots) > 0`, primer slot empieza > current_hour
- **Descripción:** `"⚡ N slot(s) activos | próxima carga HH:MM"`

#### N2. Plan activo + slot ya empezado
- Primer slot ya cubre hora actual (enforce ya corriendo)
- **Descripción:** `"⚡ N slot(s) activos | ejecutando plan"`

#### N3. Sin plan, early plan hecho, sin slots
- `_today_early_plan_done = True`, `current_plan = None`
- **Descripción:** `"plan HOY sin slots"`

#### N4. Sin plan, early plan no hecho, antes de target
- `_today_early_plan_done = False`, `now.hour < target_hour`
- **Descripción:** `"planificando HOY..."`

#### N5. Sin plan, prices fetched, antes de target
- `prices_fetched_today = True`, `now.hour < target_hour`
- **Descripción:** `"preparado (esperando ventana nocturna)"`

#### N6. Sin plan, prices fetched, no planned
- `prices_fetched_today = True`, `not self.planned_today`
- **Descripción:** `"planificando cruzando medianoche..."`

#### N7. Sin plan, prices fetched, planned
- `prices_fetched_today = True`, `self.planned_today = True`
- **Descripción:** `"esperando siguiente ciclo"`

#### N8. Sin plan, nada de lo anterior
- `prices_fetched_today = False` (mañana no disponible aún)
- **Descripción:** `"esperando precios mañana (20:15)"`

---

### O. Telegram — Comandos remotos

#### O1. /plan cuando prices ya están
- `_cmd_force_plan()` → refetch prices + recreate plan
- `wants_tomorrow = now_h >= target_hour`
- **Assert:** Se llama a `_fetch_prices(include_tomorrow=True/False)`

#### O2. /start cuando ya está cargando
- `tessie.start_charge()` → True (comando enviado)
- **Assert:** Mensaje "Comando de carga enviado."

#### O3. /stop cuando no está cargando
- `tessie.stop_charge()` → True (comando enviado)
- **Assert:** Mensaje "Comando de parada enviado."

#### O4. /set con clave no permitida
- `key not in allowed` → mensaje de error
- **Assert:** Muestra "Clave no permitida" + lista de permitidas

#### O5. /set sin argumentos
- `not args` → mensaje de uso
- **Assert:** Muestra "Uso: /set <clave> <valor>"

#### O6. /status cuando vehicle state = None
- `tessie.get_state()` returns None
- **Assert:** Mensaje "No se puede contactar con el vehículo"

---

### P. Debug mode — Flujo completo

#### P1. Debug startup: fetch inmediato + plan
- `_debug_mode = True` en `__init__`
- **Flujo:** `_fetch_prices()` → `_create_plan()` sin esperar tick
- **Assert:** `self.prices` y `self.current_plan` se setean en startup

#### P2. Debug _tick: siempre planifica en step 4
- Debug mode salta early plan (step 2 requiere `not self._debug_mode`)
- Step 3: `self._fetch_prices(include_tomorrow=True)` — siempre intenta mañana
- Step 4: `should_plan = True` aunque sea de día
- **Resultado:** Debug mode siempre hace cross-midnight planning

#### P3. Debug mode con --initial-battery
- `DebugTessieClient(config, initial_battery_pct=X)`
- **Assert:** Batería simulada empieza en X%

#### P4. Debug mode: start_charge simulado
- `DebugTessieClient.start_charge()` → log + `_charging = True`
- **Assert:** No se llama a API real

---

### Q. Zona horaria y Daylight Saving

#### Q1. Cambio DST marzo (UTC+1 → UTC+2)
- `get_spain_tz()` usa heurística simplificada
- Último domingo de marzo: cambio a las 02:00 → 03:00
- **Riesgo:** ~7 días de error de 1h si la heurística falla
- **Impacto:** La hora reportada puede diferir 1h de la real

#### Q2. Cambio DST octubre (UTC+2 → UTC+1)
- Último domingo de octubre: cambio a las 03:00 → 02:00
- **Riesgo:** Mismo que Q1, ~7 días de posible error
- **Impacto:** Menor, porque los precios PVPC se publican en hora oficial

#### Q3. now_spain() siempre devuelve datetime con timezone
- **Assert:** `now_spain().tzinfo` no es None
- **Importante:** Todas las comparaciones horarias usan `.hour` que es zona-aware

---

### R. Otros escenarios

#### R1. run_once con --dry-run
- `dry_run=True`, `debug=False`
- **Flujo:** `tessie = ReadOnlyVehicleClient(TessieClient(...))`
- **Assert:** Se llama a `start_charge()` pero el ReadOnlyVehicleClient NO ejecuta la llamada real

#### R2. run_once con plan vacío
- `planner.plan()` devuelve plan sin slots
- **Resultado:** `"No charging slots found."` y return
- **Assert:** No se intenta iniciar/parar carga

#### R3. run_once charge_limit check
- Después del plan, se verifica `state.charge_limit_pct`
- Si `charge_limit_pct < max(50, int(min_battery_pct))` → se ajusta
- **Assert:** `set_charge_limit()` se llama incluso si no hay plan activo

#### R4. _daemonize() en Windows
- `os.fork()` no existe en Windows → `AttributeError`
- **Resultado:** Warning "Background mode no soportado en Windows" + ejecuta en foreground
- **Assert:** No crashea

#### R5. _daemonize() con recursos del sistema agotados
- `os.fork()` lanza `OSError` (poco probable pero posible)
- **Resultado:** Warning + ejecuta en foreground
- **Assert:** No crashea

#### R6. Startup del daemon: signal handlers
- `signal.signal(signal.SIGINT, self._shutdown)`
- `signal.signal(signal.SIGTERM, self._shutdown)`
- **Assert:** `self.running = False` al recibir señal

#### R7. Startup del daemon: initial status write
- `write_status(daemon_pid=os.getpid(), daemon_mode="daemon")`
- **Assert:** /tmp/autocharge-status.json se crea al instanciar el daemon

#### R8. Migración automática de tokens config.json → .env
- Si `config.json` tiene `tessie_token` o `esios_token` y `.env` no
- **Flujo:** `_migrate_tokens()` → escribe token en .env + lo borra de config.json
- **Assert:** Después de migración, config.json no contiene tokens

#### R9. Precios no contiguos (horas saltadas)
- ESIOS devuelve solo horas {0,1,2,3,10,11,12,13} (salta 4-9)
- **Resultado:** `prices.get(h, sentinel)` asigna 500 a horas 4-9
- `real_prices` filtra con `h in prices` → horas 4-9 no cuentan como reales
- **Assert:** Plan ignora horas sin datos (tratadas como carísimas)

#### R10. 
- `_format_slot_hours()` recibe slot dict con keys faltantes
- **Resultado:** `KeyError` se propaga (no hay try/except)
- **Nota:** Solo ocurre si el status file tiene datos corruptos (ver M6)
