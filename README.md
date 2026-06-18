🇪🇸 ¿Buscas la versión en español? → [**README.es.md**](README.es.md)

---

# Tesla-PVPC ⚡

Smart Tesla charging using Spain's hourly PVPC electricity prices.

Automatically charge your Tesla using Spain's hourly electricity prices (PVPC). The daemon fetches prices from [ESIOS/REData](https://api.esios.ree.es/) daily, calculates the cheapest charging schedule, and controls your Tesla via the [Tessie API](https://tessie.com/developer).

## Features

- 📊 **Daily price fetching** — Gets hourly PVPC prices from Red Eléctrica (ESIOS API) with automatic fallback to REData public data
- 🧠 **Smart scheduling** — Optimizes charging plan to pick the cheapest hours while guaranteeing your target battery % by deadline
- 🔄 **Live replanning** — Checks progress every N minutes, recalculates schedule if falling behind
- 🖥️ **CLI Dashboard** — `--dashboard` live TUI, `--prices` hourly table with MÍN/MÁX and price-limit highlighting
- 🕐 **Clock-aligned wake** — Wakes at `:00/:15/:30/:45` and at slot boundaries, not relative to start time
- 🤖 **Telegram bot** — Notifications, status checks, and live config changes via Telegram commands
- 🔌 **Tessie-powered** — Uses the Tessie API (requires a Tessie subscription) for reliable Tesla communication
- 🌍 **Cross-platform** — Runs on Linux, macOS, and Windows as a 24/7 daemon or scheduled task

## How It Works

```
┌──────────┐     ┌───────────┐     ┌──────────┐
│  ESIOS   │────▶│ Daemon    │────▶│  Tessie  │
│ (prices) │     │ (planner) │     │  (Tesla) │
└──────────┘     └─────┬─────┘     └──────────┘
                       │
                  ┌────▼─────┐
                  │ Telegram  │
                  │ (bot)     │
                  └───────────┘
```

1. At **20:15**, the daemon fetches tomorrow's hourly electricity prices from Red Eléctrica de España
2. At **21:00**, it creates an optimal charging plan using the cheapest available hours
3. During the day, it **enforces** the plan: starts charging at the right times, stops when done
4. Every N minutes, it **checks progress** — if the car is falling behind, it recalculates
5. While the daemon runs, you can use CLI commands to inspect state without stopping it

## Quick Start

### 1. Prerequisites

- **Python 3.10+**
- **Tessie subscription** ([tessie.com](https://tessie.com)) — get your API token at [tessie.com/developer](https://tessie.com/developer)
- **ESIOS token** — request at [api.esios.ree.es](https://api.esios.ree.es/) by emailing `consultasios@ree.es` with your name (optional: falls back to REData public data)
- (Optional) **Telegram bot token** — create one via [@BotFather](https://t.me/BotFather)

### 2. Install

**Option A: uv (recommended)**

```bash
git clone https://github.com/SergioXp/Tesla-PVPC.git
cd Tesla-PVPC
uv venv                # Create virtual environment
uv sync                # Install dependencies + project
```

**Option B: pip (traditional)**

```bash
git clone https://github.com/SergioXp/Tesla-PVPC.git
cd Tesla-PVPC
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
```

Edit `.env` with your tokens (non-sensitive config like prices, times, etc. go in `config.json` via `--init`):

```bash
TESSIE_TOKEN=tk_f4k3_t0k3n_xxxx
VIN=LRW00000000000000
ESIOS_TOKEN=0000fake0000token0000
TELEGRAM_BOT_TOKEN=0000000000:ABCfakeDEF000000
TELEGRAM_CHAT_ID=000000000
```

Then configure the non-sensitive settings with:
```bash
./run.sh --init
```

### 4. Run

**First time — configure interactively:**
```bash
./run.sh --init          # Linux/macOS
run.bat --init            # Windows
```

**24/7 daemon (foreground):**
```bash
./run.sh
```

**Daemon in background (terminal free):**
```bash
./run.sh -b
# PID: 12345
# Use --prices, --dashboard, --show-config to inspect
```

**One-shot (single cycle, then exit):**
```bash
./run.sh --once
```

**Debug mode (no Tesla required):**
```bash
./run.sh --debug
./run.sh --debug --initial-battery 50
```

**Dry run (read real car, don't send commands):**
```bash
./run.sh --dry-run
```

### 5. CLI Reference (all arguments)

| Argument | Alias | Description |
|----------|-------|-------------|
| _(no args)_ | | Start 24/7 daemon in foreground (or interactive menu) |
| `--init` | | Interactive setup wizard (creates config.json + .env) |
| `--once` | | Run a single planning + enforcement cycle, then exit |
| `--background` | `-b` | Start daemon in background, terminal returns immediately |
| `--dashboard` | | Live TUI dashboard (Ctrl+C to exit, reads daemon status) |
| `--prices` | | Show hourly electricity prices table (from daemon or live) |
| `--show-config` | | Display current configuration with source (`.env` / `config.json`) |
| `--edit` | | Edit a single config field interactively |
| `--debug` | | Force debug mode with simulated vehicle (no Tesla needed) |
| `--initial-battery PCT` | | Starting battery % in debug mode (default: 35) |
| `--dry-run` | | Read real car data, log what would happen, **block all write commands** |
| `--verbose` | `-v` | Enable detailed debug logging |
| `--version` | | Show script version (`v0.5.0`) and exit |
| `--lang es\|en` | | Set interface language: `es` (Spanish) or `en` (English). Default: `es` |
| `--config PATH` | | Use a custom config.json path (default: `./config.json`) |
| `--help` | `-h` | Show full help with all arguments and examples |

**Combinations:**

| Command | Effect |
|---------|--------|
| `-b --dashboard` | Start daemon in background + open dashboard immediately |
| `--debug --initial-battery 50` | Debug mode with battery starting at 50% |
| `--once --verbose --dry-run` | One-shot: see everything the script would do, without touching the car |
| `--once --verbose --debug` | One-shot with simulated vehicle and full logs |

**Example `--prices` output:**
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

### 6. Debug Mode (no Tesla required)

If you don't set `TESSIE_TOKEN`, the script runs in **debug mode** with a simulated vehicle. Perfect for testing without a real car:

```bash
# Auto-detected when TESSIE_TOKEN is empty
./run.sh

# Or force it
./run.sh --debug --initial-battery 50

# One-shot test
./run.sh --once --verbose
```

## Configuration Reference

| Variable / Field | Description |
|-------|-------------|
| `TESSIE_TOKEN` | Your Tessie API token (from tessie.com/developer) |
| `VIN` | Your Tesla's VIN |
| `ESIOS_TOKEN` | ESIOS API token (optional, falls back to REData) |
| `MAX_PRICE_CENTS_PER_KWH` | Maximum price (c€/kWh) you're willing to pay |
| `MAX_CHARGER_POWER_KW` | Your charger's max power in kW (e.g., 3.3, 7.4, 11) |
| `BATTERY_CAPACITY_KWH` | Your Tesla's battery capacity in kWh (e.g., 75 for Model 3 LR) |
| `MIN_BATTERY_PCT` | Minimum battery % needed |
| `TARGET_TIME` | Deadline to reach `min_battery_pct` (HH:MM format) |
| `STRICT_MODE` | `true` = charge expensive hours if needed. `false` = only cheap hours |
| `CHARGING_EFFICIENCY` | Charging efficiency factor (0-1). Default: `0.9` |
| `CHECK_INTERVAL_MINUTES` | How often to check progress and enforce plan |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from @BotFather (optional) |
| `TELEGRAM_CHAT_ID` | Telegram chat ID for notifications (optional) |

## Telegram Commands

Once your Telegram bot is configured, control the daemon remotely:

| Command | Description |
|---------|-------------|
| `/status` | Battery %, charging state, current plan |
| `/plan` | Force replanning now |
| `/startcharge` | Start charging immediately |
| `/stopcharge` | Stop charging immediately |
| `/config` | Show current configuration |
| `/set <key> <value>` | Change a config value (e.g., `/set min_battery_pct 80`) |
| `/help` | Show all commands |

## Running as a Background Service

### Linux (systemd)

```bash
# Edit install/tesla-pvpc.service with your paths first
sudo cp install/tesla-pvpc.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tesla-pvpc
sudo systemctl status tesla-pvpc
```

### macOS (launchd)

```bash
# Edit the plist file paths first (replace REPLACE_WITH_USER)
cp install/com.tesla-pvpc.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.tesla-pvpc.plist
launchctl start com.tesla-pvpc.daemon
```

### Windows

See [`install/windows-instructions.txt`](install/windows-instructions.txt) for Task Scheduler, NSSM, and Startup folder options.

## How the Algorithm Works

1. **Calculate need**: `kWh_needed = (target_pct - current_pct) / 100 * battery_capacity`
2. **Calculate hours**: `hours_needed = ceil(kWh_needed / charger_power_kw)`
3. **Filter & sort**: Take all hours from now until target time, filter by max price, sort cheapest first
4. **Allocate**: Assign hours from cheapest to most expensive until `hours_needed` are covered
5. **Strict mode**: If not enough cheap hours, add expensive ones to guarantee target
6. **Flexible mode**: Only use cheap hours — may not reach target
7. **Progress tracking**: Expected battery % calculated per hour; replan if >3% behind

## Built with Vibecoding 🤖

This project was built using **vibecoding** — AI-assisted development where human creativity
meets AI acceleration. Every line was crafted through an iterative dialogue between the
developer and AI agents, making complex software accessible to anyone with a vision.

## License

MIT — see [LICENSE](LICENSE) file.

## Contributing

Pull requests welcome! Open an issue first to discuss what you'd like to change.
