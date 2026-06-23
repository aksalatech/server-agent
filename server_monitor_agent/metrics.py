from __future__ import annotations

import os
import shutil
import time
from datetime import datetime, timezone
from typing import Any


def _read_meminfo() -> dict[str, int]:
    info: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    info[parts[0].rstrip(":")] = int(parts[1])
    except OSError:
        pass
    return info


def _cpu_percent(sample_seconds: float = 0.15) -> float | None:
    def read_idle_total() -> tuple[int, int] | None:
        try:
            with open("/proc/stat", encoding="utf-8") as handle:
                line = handle.readline()
        except OSError:
            return None
        if not line.startswith("cpu "):
            return None
        fields = [int(value) for value in line.split()[1:]]
        if len(fields) < 4:
            return None
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
        return idle, sum(fields)

    first = read_idle_total()
    if first is None:
        return None
    time.sleep(sample_seconds)
    second = read_idle_total()
    if second is None:
        return None

    idle_delta = second[0] - first[0]
    total_delta = second[1] - first[1]
    if total_delta <= 0:
        return None
    return round(100 * (1 - idle_delta / total_delta), 1)


def collect_system_metrics() -> dict[str, Any]:
    mem = _read_meminfo()
    total_kb = mem.get("MemTotal", 0)
    avail_kb = mem.get("MemAvailable", mem.get("MemFree", 0))
    used_kb = max(0, total_kb - avail_kb)
    swap_total_kb = mem.get("SwapTotal", 0)
    swap_free_kb = mem.get("SwapFree", 0)

    disk_total = disk_used = 0
    disk_percent = 0.0
    try:
        disk = shutil.disk_usage("/")
        disk_total = disk.total
        disk_used = disk.used
        disk_percent = round(100 * disk.used / disk.total, 1) if disk.total else 0.0
    except OSError:
        pass

    uptime_seconds = 0
    try:
        with open("/proc/uptime", encoding="utf-8") as handle:
            uptime_seconds = int(float(handle.read().split()[0]))
    except (OSError, ValueError, IndexError):
        pass

    load_1 = load_5 = load_15 = 0.0
    try:
        load_1, load_5, load_15 = os.getloadavg()
    except OSError:
        pass

    cpu_percent = _cpu_percent()
    cpu_cores = os.cpu_count() or 1

    return {
        "cpu_percent": cpu_percent,
        "cpu_cores": cpu_cores,
        "load_1": round(load_1, 2),
        "load_5": round(load_5, 2),
        "load_15": round(load_15, 2),
        "memory_total_mb": round(total_kb / 1024) if total_kb else 0,
        "memory_used_mb": round(used_kb / 1024) if total_kb else 0,
        "memory_percent": round(100 * used_kb / total_kb, 1) if total_kb else 0.0,
        "swap_total_mb": round(swap_total_kb / 1024),
        "swap_used_mb": round(max(0, swap_total_kb - swap_free_kb) / 1024),
        "disk_total_gb": round(disk_total / (1024**3), 2),
        "disk_used_gb": round(disk_used / (1024**3), 2),
        "disk_percent": disk_percent,
        "uptime_seconds": uptime_seconds,
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }
