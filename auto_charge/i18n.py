"""Internationalization: ES/EN translations for all user-facing text."""

from typing import Dict

# Language state (set at startup)
_current_lang: str = "es"


def set_lang(lang: str) -> None:
    """Set the current language ('es' or 'en')."""
    global _current_lang
    if lang in _TR:
        _current_lang = lang


def t(key: str, **kwargs) -> str:
    """Translate a key to the current language. Supports .format(**kwargs)."""
    text = _TR.get(_current_lang, {}).get(key)
    if text is None:
        text = _TR.get("en", {}).get(key, key)  # fallback to English or raw key
    if kwargs:
        return text.format(**kwargs)
    return text


# =============================================================================
# Translations
# =============================================================================

_TR: Dict[str, Dict[str, str]] = {
    "es": {
        # CLI help
        "cli.description": "Tesla-PVPC - Carga inteligente del Tesla con precios PVPC España",
        "cli.epilog": """Ejemplos:
  %(prog)s                    Lanzar como daemon 24/7
  %(prog)s --once             Planificar y ejecutar una vez
  %(prog)s --init             Wizard interactivo de configuración
  %(prog)s --show-config      Mostrar configuración actual
  %(prog)s --debug            Forzar modo debug (vehículo simulado)
  %(prog)s --dry-run          Leer coche real sin enviar comandos
  %(prog)s --config ruta      Usar config.json en otra ruta
  %(prog)s --verbose          Logs detallados (DEBUG)""",
        "cli.once": "Ejecutar un ciclo (planificar + ejecutar) y salir.",
        "cli.config": "Ruta al archivo config.json.",
        "cli.verbose": "Activar logging detallado (DEBUG).",
        "cli.debug": "Forzar modo debug con vehículo simulado.",
        "cli.init-battery": "Batería inicial en modo debug (default: 35).",
        "cli.init": "Wizard interactivo de configuración paso a paso.",
        "cli.show-config": "Mostrar la configuración completa actual.",
        "cli.dry-run": "Leer datos reales del coche pero NO enviar comandos de carga.",
        "cli.lang": "Idioma: es (español) o en (inglés). Default: es.",
        "cli.edit": "Editar un campo de configuración interactivamente.",
        "cli.background": "Lanzar el daemon en segundo plano y devolver la terminal.",
        "cli.prices": "Mostrar los precios de la luz actuales (desde el daemon o directo).",
        "cli.dashboard": "Panel de control del daemon en ejecución.",
        "cli.version": "Mostrar versión del script.",
        "daemon.background": "🔄 Lanzando daemon en segundo plano...",
        "cli.no-config1": "ERROR: No se encontró configuración.",
        "cli.no-config2": "Usa --init para configurar el script interactivamente.",
        "cli.no-config3": "O copia .env.example a .env y rellena tus tokens.",
        "cli.config-error": "ERROR cargando configuración:",

        # Main menu
        "menu.title": "Tesla-PVPC ⚡",
        "menu.subtitle": "Carga inteligente del Tesla con precios de la luz en España",
        "menu.start-daemon": "Iniciar daemon 24/7",
        "menu.run-once": "Ejecutar una vez",
        "menu.configure": "Configuración",
        "menu.show-config": "Ver configuración actual",
        "menu.monitor": "Monitor en vivo",
        "menu.exit": "Salir",
        "menu.choose": "¿Qué quieres hacer?",
        "menu.edit-config": "Editar configuración",

        # Monitor
        "monitor.title": "Monitor en vivo - Ctrl+C para salir",
        "monitor.battery": "Batería",
        "monitor.charging": "Carga",
        "monitor.plugged": "Enchufado",
        "monitor.limit": "Límite",
        "monitor.plan": "Plan actual",
        "monitor.target": "Objetivo",
        "monitor.deadline": "Plazo",
        "monitor.cost": "Coste estimado",
        "monitor.slots": "Horarios",
        "monitor.prices": "Precios electricidad",
        "monitor.no-plan": "Sin plan activo",
        "monitor.no-prices": "Sin datos de precios",
        "monitor.refreshing": "Actualizando",
        "monitor.waiting": "Esperando siguiente ciclo",

        # Init wizard
        "init.title": "Tesla-PVPC - Wizard de Configuración",
        "init.intro": "Te guiaré paso a paso para configurar el script.",
        "init.enter-hint": "Pulsa Enter para mantener el valor actual (entre corchetes).",
        "init.skip-hint": "Escribe 'saltar' para dejar el campo vacío (solo opcionales).",
        "init.existing": "Configuración existente encontrada",
        "init.existing-values": "Los valores actuales se muestran entre [corchetes]",
        "init.new-config": "Se creará nueva configuración",
        "init.cancelled": "Setup cancelado.",
        "init.saved": "Configuración guardada en",
        "init.summary": "Resumen de la configuración",
        "init.ready": "Ya puedes ejecutar",
        "init.try-debug": "Prueba primero con --debug si no tienes token de Tessie.",
        "init.keeping": "Manteniendo",
        "init.skipped": "Campo omitido.",
        "init.saved-field": "Guardado.",
        "init.invalid-format": "Formato inválido.",
        "init.out-of-range": "Valor fuera de rango. Inténtalo de nuevo.",
        "init.bool-error": "Debe ser 'true' o 'false'.",
        "init.corrupt-warn": "No se pudo leer la configuración. Se usarán valores por defecto.",

        # Init field descriptions
        "init.desc.tessie_token": "Token de la API de Tessie. Consíguelo en https://tessie.com/developer",
        "init.desc.vin": "Número de bastidor (VIN) de tu Tesla. Lo encuentras en la app de Tesla o en Tessie > Vehículo.",
        "init.desc.esios_token": "Token para la API de precios de la luz. Solicítalo gratis: consultasios@ree.es",
        "init.desc.max_price": "Precio máximo que estás dispuesto a pagar (céntimos/kWh).",
        "init.desc.charger_power": "Potencia máxima de tu cargador en kW: 3.3, 7.4, 11, 22...",
        "init.desc.battery_capacity": "Capacidad total de la batería en kWh: M3 SR~57, M3 LR~75, MS~100.",
        "init.desc.min_battery": "Porcentaje mínimo de batería que necesitas tener.",
        "init.desc.target_time": "Hora límite (formato 24h) para alcanzar el % objetivo. Ej: 19:00.",
        "init.desc.strict_mode": "true = fuerza carga aunque sea cara para llegar al objetivo. false = solo horas baratas.",
        "init.desc.efficiency": "Eficiencia real de carga (0-1). Típico: 0.85-0.95.",
        "init.desc.check_interval": "Cada cuántos minutos comprueba batería y ajusta el plan (1-120).",
        "init.desc.telegram_bot": "Token del bot de Telegram para notificaciones (opcional). Crea uno con @BotFather.",
        "init.desc.telegram_chat": "ID del chat de Telegram para notificaciones (opcional).",

        # Show config
        "show.title": "Tesla-PVPC - Configuración Actual",
        "show.source": "Fuente",
        "show.path": "Ruta",
        "show.no-config": "No se encontró configuración. Mostrando valores por defecto.",
        "show.use-init": "Usa --init para crear la configuración.",
        "show.section.tessie": "Tessie API",
        "show.section.esios": "ESIOS (Red Eléctrica)",
        "show.section.charge": "Carga",
        "show.section.telegram": "Telegram",
        "show.telegram-active": "Telegram activo",
        "show.telegram-inactive": "Telegram inactivo",
        "show.debug-on": "sin token Tessie → vehículo simulado",
        "show.debug-off": "vehículo real",
        "show.not-configured": "no configurado",
        "show.mode": "Modo",

        # Labels
        "label.tessie_token": "Token Tessie",
        "label.vin": "VIN del vehículo",
        "label.esios_token": "Token ESIOS",
        "label.max_price": "Precio máximo (c€/kWh)",
        "label.charger_power": "Potencia cargador (kW)",
        "label.battery_capacity": "Capacidad batería (kWh)",
        "label.min_battery": "Batería mínima (%)",
        "label.target_time": "Hora objetivo (HH:MM)",
        "label.strict_mode": "Modo estricto (true/false)",
        "label.efficiency": "Eficiencia de carga (0-1)",
        "label.check_interval": "Intervalo revisión (min)",
        "label.telegram_bot": "Telegram Bot Token",
        "label.telegram_chat": "Telegram Chat ID",

        # Dry-run / debug
        "dryrun.active": "Modo DRY-RUN: lee datos reales del coche, no envía comandos.",
        "dryrun.ignored": "--dry-run ignorado: el modo debug ya simula todo.",
        "debug.active": "Modo DEBUG: vehículo simulado, logging extenso.",
        "debug.forced": "Modo debug forzado. Batería simulada al {pct}%.",
        "debug.using-sim": "Usando vehículo simulado.",

        # Daemon
        "daemon.starting": "Iniciando daemon Tesla-PVPC (24/7).",
        "daemon.running-once": "Ejecutando en modo --once.",
        "daemon.config-loaded": "Configuración cargada de .env",

        # Status
        "status.vehicle": "Estado del vehículo",
        "status.battery": "Batería",
        "status.plugged": "Enchufado",
        "status.not-plugged": "No enchufado",
        "status.charging": "Cargando",
        "status.stopped": "Parado",
        "status.limit": "Límite",
        "status.power": "Potencia",
        "status.current-plan": "Plan actual",
        "status.no-plan": "Sin plan activo",
        "status.unreachable": "No se puede contactar con el vehículo.",

        # Edit
        "edit.title": "Tesla-PVPC - Editor de Configuración",
        "edit.select-field": "Selecciona el campo que quieres modificar:",
        "edit.field": "Campo",
        "edit.cancelled": "Editor cancelado.",
        "edit.editing": "Editando",
        "edit.current": "Valor actual",
        "edit.type": "Tipo",
        "edit.new-value": "Nuevo valor",
        "edit.unchanged": "Valor sin cambios.",
        "edit.saved": "Configuración guardada.",
        "edit.another": "¿Editar otro campo?",
        "edit.restart-hint": "Reinicia el daemon con ./run.sh para que los cambios surtan efecto.",
        "edit.save-error": "Error al guardar",
    },

    "en": {
        # CLI help
        "cli.description": "Tesla-PVPC - Smart Tesla charging via Spanish PVPC prices",
        "cli.epilog": """Examples:
  %(prog)s                    Run as 24/7 daemon
  %(prog)s --once             Plan and execute once
  %(prog)s --init             Interactive setup wizard
  %(prog)s --show-config      Show current configuration
  %(prog)s --debug            Force debug mode (simulated vehicle)
  %(prog)s --dry-run          Read real car data, don't send commands
  %(prog)s --config path      Use custom config.json path
  %(prog)s --verbose          Debug logging""",
        "cli.once": "Run a single planning+enforcement cycle and exit.",
        "cli.config": "Path to config.json file.",
        "cli.verbose": "Enable debug logging.",
        "cli.debug": "Force debug mode with simulated vehicle.",
        "cli.init-battery": "Starting battery % in debug mode (default: 35).",
        "cli.init": "Interactive setup wizard step by step.",
        "cli.show-config": "Display full current configuration.",
        "cli.dry-run": "Read real car data but do NOT send charge commands.",
        "cli.lang": "Language: es (Spanish) or en (English). Default: es.",
        "cli.edit": "Edit a config field interactively.",
        "cli.background": "Launch the daemon in background and return the terminal.",
        "cli.prices": "Show current electricity prices (from daemon or live).",
        "cli.dashboard": "Control panel for the running daemon.",
        "cli.version": "Show script version.",
        "daemon.background": "🔄 Launching daemon in background...",
        "cli.no-config1": "ERROR: No configuration found.",
        "cli.no-config2": "Use --init to configure the script interactively.",
        "cli.no-config3": "Or copy .env.example to .env and fill in your tokens.",
        "cli.config-error": "ERROR loading configuration:",

        # Main menu
        "menu.title": "Tesla-PVPC ⚡",
        "menu.subtitle": "Smart Tesla charging with Spanish electricity prices",
        "menu.start-daemon": "Start 24/7 daemon",
        "menu.run-once": "Run once",
        "menu.configure": "Settings",
        "menu.show-config": "Show configuration",
        "menu.monitor": "Live monitor",
        "menu.exit": "Exit",
        "menu.choose": "What would you like to do?",
        "menu.edit-config": "Edit configuration",

        # Monitor
        "monitor.title": "Live Monitor - Ctrl+C to exit",
        "monitor.battery": "Battery",
        "monitor.charging": "Charging",
        "monitor.plugged": "Plugged in",
        "monitor.limit": "Limit",
        "monitor.plan": "Current plan",
        "monitor.target": "Target",
        "monitor.deadline": "Deadline",
        "monitor.cost": "Est. cost",
        "monitor.slots": "Schedule",
        "monitor.prices": "Electricity prices",
        "monitor.no-plan": "No active plan",
        "monitor.no-prices": "No price data",
        "monitor.refreshing": "Refreshing",
        "monitor.waiting": "Waiting for next cycle",

        # Init wizard
        "init.title": "Tesla-PVPC - Setup Wizard",
        "init.intro": "I'll guide you step by step to configure the script.",
        "init.enter-hint": "Press Enter to keep the current value (shown in brackets).",
        "init.skip-hint": "Type 'skip' to leave the field empty (optional fields only).",
        "init.existing": "Existing configuration found",
        "init.existing-values": "Current values are shown in [brackets]",
        "init.new-config": "New configuration will be created",
        "init.cancelled": "Setup cancelled.",
        "init.saved": "Configuration saved to",
        "init.summary": "Configuration summary",
        "init.ready": "You can now run",
        "init.try-debug": "Try --debug first if you don't have a Tessie token.",
        "init.keeping": "Keeping",
        "init.skipped": "Field skipped.",
        "init.saved-field": "Saved.",
        "init.invalid-format": "Invalid format.",
        "init.out-of-range": "Value out of range. Try again.",
        "init.bool-error": "Must be 'true' or 'false'.",
        "init.corrupt-warn": "Could not read configuration. Using defaults.",

        # Init field descriptions
        "init.desc.tessie_token": "Tessie API token. Get it at https://tessie.com/developer",
        "init.desc.vin": "Your Tesla's VIN. Find it in the Tesla app or Tessie > Vehicle.",
        "init.desc.esios_token": "Token for the electricity price API. Request it free: consultasios@ree.es",
        "init.desc.max_price": "Maximum price you're willing to pay (cents/kWh).",
        "init.desc.charger_power": "Max power of your charger in kW: 3.3, 7.4, 11, 22...",
        "init.desc.battery_capacity": "Total battery capacity in kWh: M3 SR~57, M3 LR~75, MS~100.",
        "init.desc.min_battery": "Minimum battery percentage you need.",
        "init.desc.target_time": "Deadline time (24h format) to reach target %. Example: 19:00.",
        "init.desc.strict_mode": "true = charge expensive hours if needed. false = only cheap hours.",
        "init.desc.efficiency": "Real charging efficiency (0-1). Typical: 0.85-0.95.",
        "init.desc.check_interval": "How often to check battery and adjust plan (1-120 min).",
        "init.desc.telegram_bot": "Telegram bot token for notifications (optional). Create with @BotFather.",
        "init.desc.telegram_chat": "Telegram chat ID for notifications (optional).",

        # Show config
        "show.title": "Tesla-PVPC - Current Configuration",
        "show.source": "Source",
        "show.path": "Path",
        "show.no-config": "No configuration found. Showing defaults.",
        "show.use-init": "Use --init to create the configuration.",
        "show.section.tessie": "Tessie API",
        "show.section.esios": "ESIOS (Grid Operator)",
        "show.section.charge": "Charging",
        "show.section.telegram": "Telegram",
        "show.telegram-active": "Telegram active",
        "show.telegram-inactive": "Telegram inactive",
        "show.debug-on": "no Tessie token → simulated vehicle",
        "show.debug-off": "real vehicle",
        "show.not-configured": "not set",
        "show.mode": "Mode",

        # Labels
        "label.tessie_token": "Tessie Token",
        "label.vin": "Vehicle VIN",
        "label.esios_token": "ESIOS Token",
        "label.max_price": "Max price (c€/kWh)",
        "label.charger_power": "Charger power (kW)",
        "label.battery_capacity": "Battery capacity (kWh)",
        "label.min_battery": "Min battery (%)",
        "label.target_time": "Target time (HH:MM)",
        "label.strict_mode": "Strict mode (true/false)",
        "label.efficiency": "Charging efficiency (0-1)",
        "label.check_interval": "Check interval (min)",
        "label.telegram_bot": "Telegram Bot Token",
        "label.telegram_chat": "Telegram Chat ID",

        # Dry-run / debug
        "dryrun.active": "DRY-RUN mode: reading real car data, blocking all write commands.",
        "dryrun.ignored": "--dry-run ignored: debug mode already simulates everything.",
        "debug.active": "DEBUG mode: simulated vehicle, extensive logging.",
        "debug.forced": "Debug mode forced. Simulated battery at {pct}%.",
        "debug.using-sim": "Using simulated vehicle.",

        # Daemon
        "daemon.starting": "Starting Tesla-PVPC daemon (24/7).",
        "daemon.running-once": "Running in --once mode.",
        "daemon.config-loaded": "Configuration loaded from .env",

        # Status
        "status.vehicle": "Vehicle status",
        "status.battery": "Battery",
        "status.plugged": "Plugged in",
        "status.not-plugged": "Not plugged",
        "status.charging": "Charging",
        "status.stopped": "Stopped",
        "status.limit": "Limit",
        "status.power": "Power",
        "status.current-plan": "Current plan",
        "status.no-plan": "No active plan",
        "status.unreachable": "Cannot contact vehicle.",

        # Edit
        "edit.title": "Tesla-PVPC - Configuration Editor",
        "edit.select-field": "Select the field you want to modify:",
        "edit.field": "Field",
        "edit.cancelled": "Editor cancelled.",
        "edit.editing": "Editing",
        "edit.current": "Current value",
        "edit.type": "Type",
        "edit.new-value": "New value",
        "edit.unchanged": "Value unchanged.",
        "edit.saved": "Configuration saved.",
        "edit.another": "Edit another field?",
        "edit.restart-hint": "Restart the daemon with ./run.sh for changes to take effect.",
        "edit.save-error": "Error saving",
    },
}
