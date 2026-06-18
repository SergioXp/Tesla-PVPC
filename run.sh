#!/usr/bin/env bash
# =============================================================================
# Tesla-PVPC - Launcher (Linux / macOS)
# =============================================================================
# Usage:
#   ./run.sh                  # Lanza el daemon
#   ./run.sh --once           # Una ejecución y sale
#   ./run.sh --init           # Wizard de configuración
#   ./run.sh --dry-run        # Lee datos reales, no manda comandos
#   ./run.sh --debug          # Modo simulado
#   ./run.sh --show-config    # Muestra la configuración
#   ./run.sh --help           # Ayuda
# =============================================================================
set -e
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Get version from source of truth (auto_charge/__init__.py)
APP_VERSION=$(python3 -c "from auto_charge import __version__; print(__version__)" 2>/dev/null || echo "0.5.0")

echo "=============================================="
echo "  Tesla-PVPC v${APP_VERSION} ⚡"
echo "=============================================="
echo ""

# --- 1. Check/install uv ---
if ! command -v uv &>/dev/null; then
    echo "📦 uv no encontrado. Instalando uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Add uv to PATH for this session
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

    # Verify uv installed correctly
    if ! command -v uv &>/dev/null; then
        echo "❌ ERROR: uv installation failed. Install manually: https://docs.astral.sh/uv/"
        exit 1
    fi
    echo "✅ uv instalado."
    echo ""
fi

# --- 2. Create venv if missing ---
if [ ! -d ".venv" ]; then
    echo "🐍 Creando entorno virtual con uv..."
    uv venv
    echo "✅ Entorno virtual creado (.venv/)."
    echo ""
fi

# --- 3. Install/update dependencies if needed ---
if [ ! -f ".venv/.deps-installed" ] || [ requirements.txt -nt ".venv/.deps-installed" ] || [ pyproject.toml -nt ".venv/.deps-installed" ]; then
    echo "📦 Instalando dependencias (uv sync)..."
    uv sync
    touch .venv/.deps-installed
    echo "✅ Dependencias instaladas."
    echo ""
fi

# --- 4. Normalize args ---
# Recognizes both short ("dashboard") and long ("--dashboard") forms.
# Tracks whether to kill existing daemon (query commands like --prices/
# --dashboard/--edit/--init/--show-config/--once should not kill).
ARGS=()
NEEDS_KILL=true
for arg in "$@"; do
    norm=""
    case "$arg" in
        init|--init)                     norm="--init";     NEEDS_KILL=false ;;
        once|--once)                     norm="--once";     NEEDS_KILL=false ;;
        debug|--debug)                   norm="--debug" ;;
        dry-run|dry_run|--dry-run)       norm="--dry-run" ;;
        help|--help)                     norm="--help";     NEEDS_KILL=false ;;
        show-config|show_config|--show-config) norm="--show-config"; NEEDS_KILL=false ;;
        edit|--edit)                     norm="--edit";     NEEDS_KILL=false ;;
        background|--background|-b)      norm="--background" ;;
        prices|--prices)                 norm="--prices";   NEEDS_KILL=false ;;
        dashboard|--dashboard)           norm="--dashboard"; NEEDS_KILL=false ;;
        *)                               norm="$arg" ;;
    esac
    ARGS+=( "$norm" )
done

# --- 5. Kill existing daemon only when launching daemon/background mode ---
if [ "$NEEDS_KILL" = true ]; then
    EXISTING_PIDS=$(pgrep -f "python.*tesla_pvpc" 2>/dev/null || true)
    if [ -n "$EXISTING_PIDS" ]; then
        echo "🔫 Cerrando instancias previas de Tesla-PVPC..."
        for OLD_PID in $EXISTING_PIDS; do
            if ps -p "$OLD_PID" > /dev/null 2>&1; then
                kill "$OLD_PID" 2>/dev/null || true
            fi
        done
        sleep 1
        for OLD_PID in $EXISTING_PIDS; do
            if ps -p "$OLD_PID" > /dev/null 2>&1; then
                kill -9 "$OLD_PID" 2>/dev/null || true
                echo "  ✨ Proceso PID $OLD_PID terminado."
            fi
        done
    fi
    unset EXISTING_PIDS
fi

# --- 6. Run ---
echo "🚀 Lanzando Tesla-PVPC v${APP_VERSION}..."
echo ""
uv run python tesla_pvpc.py "${ARGS[@]}"
