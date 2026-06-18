# Tesla-PVPC ⚡

Carga inteligente del Tesla con precios PVPC de España.

Automáticamente carga tu Tesla usando los precios horarios de la luz en España (PVPC). El daemon obtiene los precios de [ESIOS/REData](https://api.esios.ree.es/) a diario, calcula el plan de carga más barato y controla tu Tesla mediante la [API de Tessie](https://tessie.com/developer).

🇬🇧 Looking for English? → [**README.md**](README.md)

---

## Características

- 📊 **Precios diarios** — Obtiene los precios PVPC de Red Eléctrica (API ESIOS) con fallback automático a datos públicos REData
- 🧠 **Planificación inteligente** — Optimiza el plan de carga usando las horas más baratas, garantizando el % de batería objetivo antes de la hora límite
- 🔄 **Replanificación en vivo** — Comprueba el progreso cada N minutos y recalcula si se está quedando atrás
- 🖥️ **Dashboard CLI** — `--dashboard` TUI en vivo, `--prices` tabla horaria con MÍN/MÁX y resaltado de límite de precio
- 🕐 **Despertar alineado al reloj** — Despierta en `:00/:15/:30/:45` y al inicio de cada bloque de carga
- 🤖 **Bot de Telegram** — Notificaciones, consultas de estado y cambios de configuración en vivo por Telegram
- 🔌 **Con Tessie** — Usa la API de Tessie (requiere suscripción Tessie) para comunicación fiable con el Tesla
- 🌍 **Multiplataforma** — Funciona en Linux, macOS y Windows como daemon 24/7 o tarea programada

## Cómo funciona

```
┌──────────┐     ┌───────────┐     ┌──────────┐
│  ESIOS   │────▶│  Daemon   │────▶│  Tessie  │
│ (precios)│     │(planifica)│     │  (Tesla) │
└──────────┘     └─────┬─────┘     └──────────┘
                       │
                  ┌────▼─────┐
                  │ Telegram  │
                  │  (bot)    │
                  └───────────┘
```

1. A las **20:15**, el daemon obtiene los precios horarios del día siguiente de Red Eléctrica
2. A las **21:00**, crea un plan de carga óptimo usando las horas más baratas disponibles
3. Durante el día, **ejecuta el plan**: inicia la carga a la hora indicada, la detiene cuando toca
4. Cada N minutos, **comprueba el progreso** — si el coche va retrasado, recalcula
5. Mientras el daemon corre, puedes usar comandos CLI para inspeccionar el estado sin pararlo

## Inicio rápido

### 1. Requisitos

- **Python 3.10+**
- **Suscripción Tessie** ([tessie.com](https://tessie.com)) — consigue tu token en [tessie.com/developer](https://tessie.com/developer)
- **Token ESIOS** — solicítalo en [api.esios.ree.es](https://api.esios.ree.es/) escribiendo a `consultasios@ree.es` (opcional: fallback a datos públicos REData)
- (Opcional) **Token de Telegram** — crea un bot con [@BotFather](https://t.me/BotFather)

### 2. Instalación

**Opción A: uv (recomendado)**

```bash
git clone https://github.com/SergioXp/Tesla-PVPC.git
cd Tesla-PVPC
uv venv                # Crea el entorno virtual
uv sync                # Instala dependencias + proyecto
```

**Opción B: pip (tradicional)**

```bash
git clone https://github.com/SergioXp/Tesla-PVPC.git
cd Tesla-PVPC
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configuración

```bash
cp .env.example .env
```

Edita `.env` con tus tokens (la configuración no sensible como precios, horarios, etc. va en `config.json` mediante `--init`):

```bash
TESSIE_TOKEN=tk_f4k3_t0k3n_xxxx
VIN=LRW00000000000000
ESIOS_TOKEN=0000fake0000token0000
TELEGRAM_BOT_TOKEN=0000000000:ABCfakeDEF000000
TELEGRAM_CHAT_ID=000000000
```

Luego configura los ajustes no sensibles con:
```bash
./run.sh --init
```

### 4. Ejecución

**Primera vez — configuración interactiva:**
```bash
./run.sh --init          # Linux/macOS
run.bat --init            # Windows
```

**Daemon 24/7 (en primer plano):**
```bash
./run.sh
```

**Daemon en segundo plano (terminal libre):**
```bash
./run.sh -b
# PID: 12345
# Usa --prices, --dashboard, --show-config para ver el estado
```

**Una sola ejecución (un ciclo y sale):**
```bash
./run.sh --once
```

**Modo debug (sin Tesla):**
```bash
./run.sh --debug
./run.sh --debug --initial-battery 50
```

**Dry run (lee coche real, no envía comandos):**
```bash
./run.sh --dry-run
```

### 5. Comandos CLI (mientras el daemon corre)

| Comando | Descripción |
|---------|-------------|
| `./run.sh -b` | Iniciar daemon en segundo plano |
| `./run.sh --dashboard` | Dashboard TUI en vivo (Ctrl+C para salir) |
| `./run.sh --prices` | Mostrar tabla de precios horarios |
| `./run.sh --show-config` | Mostrar configuración actual |
| `./run.sh --edit` | Editar un campo de configuración |
| `./run.sh --init` | Asistente de configuración completo |
| `./run.sh -b --dashboard` | Daemon en bg + dashboard inmediato |

**Ejemplo de salida `--prices`:**
```
📊  Precios de la luz — 2026-06-18  (REData público)
     Límite: ≤ 0.100 €/kWh  →  13h disponibles (54% del día)

  00:00  0.082 €/kWh  ████████████       ✓
  02:00  0.062 €/kWh  ██████████     ← MÍN ✓
  14:00 ◀ 0.063 €/kWh  ██████████        ✓
  20:00  0.145 €/kWh  ██████████████████ ← MÁX ↑

  ─────────────────────────────────────────────
  Mín:   0.062 €/kWh  (02:00)
  Máx:   0.145 €/kWh  (20:00)
  Media:  0.094 €/kWh
```

### 6. Modo Debug (sin Tesla)

Si no configuras `TESSIE_TOKEN`, el script se ejecuta en **modo debug** con un vehículo simulado. Perfecto para probar la lógica sin coche real:

```bash
# Se detecta automáticamente si TESSIE_TOKEN está vacío
./run.sh

# O forzarlo
./run.sh --debug --initial-battery 50

# Prueba de un ciclo
./run.sh --once --verbose
```

## Referencia de configuración

| Variable / Campo | Descripción |
|-------|-------------|
| `TESSIE_TOKEN` | Token de la API de Tessie (de tessie.com/developer) |
| `VIN` | Número de bastidor (VIN) de tu Tesla |
| `ESIOS_TOKEN` | Token de ESIOS (opcional, fallback a REData) |
| `MAX_PRICE_CENTS_PER_KWH` | Precio máximo que pagas (c€/kWh) |
| `MAX_CHARGER_POWER_KW` | Potencia máxima del cargador en kW (ej. 3.3, 7.4, 11) |
| `BATTERY_CAPACITY_KWH` | Capacidad de la batería en kWh (ej. 75 para Model 3 LR) |
| `MIN_BATTERY_PCT` | % mínimo de batería necesario |
| `TARGET_TIME` | Hora límite para alcanzar el % objetivo (formato HH:MM) |
| `STRICT_MODE` | `true` = carga horas caras si es necesario. `false` = solo horas baratas |
| `CHARGING_EFFICIENCY` | Factor de eficiencia de carga (0-1). Por defecto: `0.9` |
| `CHECK_INTERVAL_MINUTES` | Cada cuántos minutos comprueba progreso y ajusta el plan |
| `TELEGRAM_BOT_TOKEN` | Token del bot de Telegram de @BotFather (opcional) |
| `TELEGRAM_CHAT_ID` | ID del chat de Telegram para notificaciones (opcional) |

## Comandos de Telegram

Una vez configurado el bot de Telegram, puedes controlar el daemon remotamente:

| Comando | Descripción |
|---------|-------------|
| `/status` | % batería, estado de carga, plan actual |
| `/plan` | Forzar replanificación ahora |
| `/startcharge` | Iniciar carga inmediatamente |
| `/stopcharge` | Detener carga inmediatamente |
| `/config` | Mostrar configuración actual |
| `/set <clave> <valor>` | Cambiar un valor de configuración (ej. `/set min_battery_pct 80`) |
| `/help` | Mostrar todos los comandos |

## Ejecutar como servicio

### Linux (systemd)

```bash
# Edita install/tesla-pvpc.service con tus rutas primero
sudo cp install/tesla-pvpc.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tesla-pvpc
sudo systemctl status tesla-pvpc
```

### macOS (launchd)

```bash
# Edita las rutas en el archivo plist primero (reemplaza REPLACE_WITH_USER)
cp install/com.tesla-pvpc.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.tesla-pvpc.plist
launchctl start com.tesla-pvpc.daemon
```

### Windows

Consulta [`install/windows-instructions.txt`](install/windows-instructions.txt) para opciones con el Programador de Tareas, NSSM y carpeta de Inicio.

## Cómo funciona el algoritmo

1. **Calcular necesidad**: `kWh_needed = (target_pct - current_pct) / 100 * battery_capacity`
2. **Calcular horas**: `hours_needed = ceil(kWh_needed / charger_power_kw)`
3. **Filtrar y ordenar**: Toma todas las horas desde ahora hasta la hora límite, filtra por precio máximo, ordena de más barata a más cara
4. **Asignar**: Asigna horas de más barata a más cara hasta cubrir `hours_needed`
5. **Modo estricto**: Si no hay suficientes horas baratas, añade horas caras para garantizar el objetivo
6. **Modo flexible**: Solo usa horas baratas — puede no alcanzar el objetivo
7. **Seguimiento**: % de batería esperado calculado por hora; replanifica si >3% por detrás

## Hecho con Vibecoding 🤖

Este proyecto ha sido desarrollado con **vibecoding** — desarrollo asistido por IA donde la
creatividad humana se combina con la aceleración de la inteligencia artificial. Cada línea
se ha creado mediante un diálogo iterativo entre el desarrollador y agentes de IA,
haciendo que el software complejo sea accesible para cualquiera con una visión.

## Licencia

MIT — ver archivo [LICENSE](LICENSE).

## Contribuir

¡Pull requests bienvenidos! Abre un issue primero para discutir lo que te gustaría cambiar.
