from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone
from typing import Any

from .config import CONFIG_DIR
from .traffic import collect_traffic

NET_COUNTERS_FILE = CONFIG_DIR / "net-counters.json"
_SKIP_IFACE_PREFIXES = ("docker", "veth", "br-", "virbr", "tun", "tap", "wg")


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


def _is_skip_interface(name: str) -> bool:
    if name == "lo":
        return True
    return any(name.startswith(prefix) for prefix in _SKIP_IFACE_PREFIXES)


def _read_net_dev() -> dict[str, tuple[int, int]]:
    counters: dict[str, tuple[int, int]] = {}
    try:
        with open("/proc/net/dev", encoding="utf-8") as handle:
            for line in handle.readlines()[2:]:
                if ":" not in line:
                    continue
                name, rest = line.split(":", 1)
                iface = name.strip()
                if _is_skip_interface(iface):
                    continue
                parts = rest.split()
                if len(parts) < 9:
                    continue
                counters[iface] = (int(parts[0]), int(parts[8]))
    except OSError:
        pass
    return counters


def _load_net_state() -> dict[str, Any]:
    try:
        with NET_COUNTERS_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_net_state(state: dict[str, Any]) -> None:
    try:
        NET_COUNTERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        NET_COUNTERS_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def _collect_network() -> dict[str, Any]:
    now = time.time()
    counters = _read_net_dev()
    total_rx = sum(rx for rx, _ in counters.values())
    total_tx = sum(tx for _, tx in counters.values())

    state = _load_net_state()
    prev_rx = state.get("rx_bytes")
    prev_tx = state.get("tx_bytes")
    prev_at = state.get("timestamp")
    prev_ifaces = state.get("interfaces") if isinstance(state.get("interfaces"), dict) else {}

    rx_bps: float | None = None
    tx_bps: float | None = None
    if isinstance(prev_rx, (int, float)) and isinstance(prev_tx, (int, float)) and isinstance(prev_at, (int, float)):
        elapsed = now - prev_at
        if elapsed > 0:
            rx_bps = round(max(0, total_rx - int(prev_rx)) / elapsed, 1)
            tx_bps = round(max(0, total_tx - int(prev_tx)) / elapsed, 1)

    interfaces: list[dict[str, Any]] = []
    for name, (rx_bytes, tx_bytes) in sorted(counters.items()):
        iface_rx_bps: float | None = None
        iface_tx_bps: float | None = None
        prev_iface = prev_ifaces.get(name) if isinstance(prev_ifaces, dict) else None
        if (
            isinstance(prev_iface, dict)
            and isinstance(prev_at, (int, float))
            and isinstance(prev_iface.get("rx_bytes"), (int, float))
            and isinstance(prev_iface.get("tx_bytes"), (int, float))
        ):
            elapsed = now - prev_at
            if elapsed > 0:
                iface_rx_bps = round(max(0, rx_bytes - int(prev_iface["rx_bytes"])) / elapsed, 1)
                iface_tx_bps = round(max(0, tx_bytes - int(prev_iface["tx_bytes"])) / elapsed, 1)

        interfaces.append(
            {
                "name": name,
                "rx_bytes": rx_bytes,
                "tx_bytes": tx_bytes,
                "rx_bytes_per_sec": iface_rx_bps,
                "tx_bytes_per_sec": iface_tx_bps,
            }
        )

    _save_net_state(
        {
            "rx_bytes": total_rx,
            "tx_bytes": total_tx,
            "timestamp": now,
            "interfaces": {name: {"rx_bytes": rx, "tx_bytes": tx} for name, (rx, tx) in counters.items()},
        }
    )

    return {
        "rx_bytes_per_sec": rx_bps,
        "tx_bytes_per_sec": tx_bps,
        "rx_total_bytes": total_rx,
        "tx_total_bytes": total_tx,
        "interfaces": interfaces,
    }


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

    network = _collect_network()
    traffic = collect_traffic()

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
        "network_rx_bytes_per_sec": network["rx_bytes_per_sec"],
        "network_tx_bytes_per_sec": network["tx_bytes_per_sec"],
        "network_rx_total_bytes": network["rx_total_bytes"],
        "network_tx_total_bytes": network["tx_total_bytes"],
        "network_interfaces": network["interfaces"],
        "traffic_inbound": traffic["traffic_inbound"],
        "traffic_outbound": traffic["traffic_outbound"],
        "http_clients": traffic["http_clients"],
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }
