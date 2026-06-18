"""Shared status file: daemon writes, CLI commands read.

Lives at /tmp/autocharge-status.json so the daemon (running in background)
can share its state with --prices, --dashboard, etc.
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

from auto_charge.utils import now_spain

STATUS_PATH = "/tmp/autocharge-status.json"


def write_status(**kwargs: Any) -> None:
    """Write daemon state to the shared status file."""
    data: Dict[str, Any] = {"timestamp": now_spain().isoformat()}
    data.update(kwargs)

    # Convert non-serializable objects
    if "prices" in data and isinstance(data["prices"], dict):
        # Prices is {hour: price_cents}
        data["prices"] = {str(k): float(v) for k, v in data["prices"].items()}

    if "expected_by_hour" in data and isinstance(data["expected_by_hour"], dict):
        data["expected_by_hour"] = {str(k): round(float(v), 1) for k, v in data["expected_by_hour"].items()}

    try:
        with open(STATUS_PATH, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    except (IOError, PermissionError):
        pass  # Non-critical, don't crash the daemon


def read_status() -> Dict[str, Any]:
    """Read daemon state from the shared status file."""
    if not os.path.exists(STATUS_PATH):
        return {}
    try:
        with open(STATUS_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def get_daemon_pid() -> Optional[int]:
    """Return the PID of the running daemon, or None."""
    status = read_status()
    pid = status.get("daemon_pid")
    if pid and _pid_exists(pid):
        return pid
    return None


def _pid_exists(pid: int) -> bool:
    """Check if a process with the given PID is running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, PermissionError):
        return False


def status_age_seconds() -> Optional[float]:
    """How old is the status file in seconds? Returns None if no status."""
    if not os.path.exists(STATUS_PATH):
        return None
    try:
        age = (datetime.now().timestamp() - os.path.getmtime(STATUS_PATH))
        return age
    except OSError:
        return None
