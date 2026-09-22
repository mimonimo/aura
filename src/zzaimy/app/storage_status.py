"""Read-only filesystem capacity for the actual document storage location."""
from datetime import datetime, timezone
from pathlib import Path
import shutil


def snapshot(path: Path) -> dict:
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return {"available": False, "checked_at": checked_at}
    return {
        "available": True,
        "checked_at": checked_at,
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "used_percent": round(100 * usage.used / usage.total, 1) if usage.total else 0,
        "low_space": usage.free < max(5 * 1024**3, usage.total * .1),
    }
