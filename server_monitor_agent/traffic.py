from __future__ import annotations

import json
import re
import socket
import subprocess
from pathlib import Path
from typing import Any

from .config import CONFIG_DIR
from .service_logs import (
    APACHE_LOG_FILES,
    CADDY_LOG_FILES,
    NGINX_LOG_FILES,
)

PEER_STATE_FILE = CONFIG_DIR / "peer-traffic-state.json"
HTTP_OFFSET_FILE = CONFIG_DIR / "http-traffic-offsets.json"

MAX_PEERS = 30
MAX_HTTP_CLIENTS = 25
MAX_ACCESS_LINES = 500
RATE_HISTORY_MAX = 20

COMMON_SERVER_PORTS = {
    22,
    80,
    443,
    3000,
    3306,
    5432,
    6379,
    8080,
    8443,
    9000,
    27017,
}

ACCESS_LOG_FILES = [
    "/var/log/nginx/access.log",
    "/var/log/apache2/access.log",
    "/var/log/httpd/access_log",
    "/var/log/caddy/access.log",
    *NGINX_LOG_FILES,
    *APACHE_LOG_FILES,
    *CADDY_LOG_FILES,
]

_COMBINED_LOG_RE = re.compile(
    r'^(\S+)\s+\S+\s+\S+\s+\[[^\]]+\]\s+"([A-Z][A-Z0-9]*)\s+([^\s"]+)\s+HTTP/[^"]+"\s+\d+'
)
_QUOTED_STRINGS_RE = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"')


def collect_traffic() -> dict[str, Any]:
    inbound, outbound = _collect_socket_peers()
    http_clients = _collect_http_clients()
    return {
        "traffic_inbound": inbound,
        "traffic_outbound": outbound,
        "http_clients": http_clients,
    }


def _parse_port(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    try:
        return socket.getservbyname(value)
    except OSError:
        return None


def _normalize_peer(host: str) -> str:
    host = host.strip("[]")
    if host.startswith("::ffff:"):
        return host[7:]
    return host


def _is_loopback(host: str) -> bool:
    return _normalize_peer(host) in {"127.0.0.1", "::1", "localhost"}


def _classify_direction(local_port: int | None, remote_port: int | None) -> str:
    if local_port in COMMON_SERVER_PORTS:
        return "inbound"
    if remote_port in COMMON_SERVER_PORTS:
        return "outbound"
    if local_port is not None and remote_port is not None:
        if local_port > remote_port:
            return "outbound"
        if local_port < remote_port:
            return "inbound"
    return "inbound"


def _split_host_port(value: str) -> tuple[str, str]:
    if value.startswith("["):
        end = value.find("]")
        if end == -1:
            return value, ""
        host = value[1:end]
        port = value[end + 2 :] if len(value) > end + 2 and value[end + 1] == ":" else ""
        return host, port
    if ":" in value:
        host, port = value.rsplit(":", 1)
        return host, port
    return value, ""


def _parse_ss_connections() -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["ss", "-H", "-ti", "state", "established"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    if result.returncode != 0:
        return []

    connections: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None

    for raw_line in result.stdout.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        if not line.startswith((" ", "\t")):
            parts = line.split()
            if len(parts) < 4:
                pending = None
                continue

            local_host, local_port_raw = _split_host_port(parts[2])
            remote_host, remote_port_raw = _split_host_port(parts[3])
            pending = {
                "local_host": local_host,
                "local_port": _parse_port(local_port_raw),
                "remote_host": remote_host,
                "remote_port": _parse_port(remote_port_raw),
                "bytes_sent": 0,
                "bytes_received": 0,
            }
            connections.append(pending)
            continue

        if pending is None:
            continue

        for key in ("bytes_sent", "bytes_received"):
            match = re.search(rf"{key}:(\d+)", line)
            if match:
                pending[key] = int(match.group(1))

    return connections


def _load_peer_state() -> dict[str, Any]:
    if not PEER_STATE_FILE.exists():
        return {}
    try:
        data = json.loads(PEER_STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_peer_state(state: dict[str, Any]) -> None:
    try:
        PEER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        PEER_STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def _resolve_hostname(ip: str, cache: dict[str, str]) -> str:
    if ip in cache:
        return cache[ip]
    try:
        name, _, _ = socket.gethostbyaddr(ip)
        short = name.rstrip(".")
        cache[ip] = short
        return short
    except OSError:
        cache[ip] = ip
        return ip


def _rate_stats(samples: list[dict[str, Any]], key: str) -> tuple[float, float, float]:
    values = [float(item.get(key) or 0) for item in samples if isinstance(item, dict)]
    if not values:
        return 0.0, 0.0, 0.0
    current = values[-1]
    average = sum(values) / len(values)
    peak = max(values)
    return current, average, peak


def _collect_socket_peers() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    import time

    now = time.time()
    state = _load_peer_state()
    prev_at = state.get("timestamp")
    prev_peers = state.get("peers") if isinstance(state.get("peers"), dict) else {}
    rate_history = state.get("rate_history") if isinstance(state.get("rate_history"), dict) else {}
    hostname_cache = state.get("hostname_cache") if isinstance(state.get("hostname_cache"), dict) else {}

    inbound_agg: dict[str, dict[str, Any]] = {}
    outbound_agg: dict[str, dict[str, Any]] = {}

    for conn in _parse_ss_connections():
        remote = _normalize_peer(str(conn["remote_host"]))
        local = _normalize_peer(str(conn["local_host"]))
        if _is_loopback(remote) and _is_loopback(local):
            continue

        direction = _classify_direction(conn.get("local_port"), conn.get("remote_port"))
        if direction == "inbound" and _is_loopback(remote):
            continue
        peer = remote if direction == "inbound" else remote
        bucket = inbound_agg if direction == "inbound" else outbound_agg

        entry = bucket.setdefault(
            peer,
            {
                "peer": peer,
                "rx_bytes": 0,
                "tx_bytes": 0,
                "rx_bytes_per_sec": 0.0,
                "tx_bytes_per_sec": 0.0,
                "connections": 0,
                "local_ports": set(),
            },
        )

        conn_key = f"{conn['local_host']}:{conn.get('local_port')}->{conn['remote_host']}:{conn.get('remote_port')}"
        prev = prev_peers.get(conn_key) if isinstance(prev_peers.get(conn_key), dict) else {}

        rx = int(conn.get("bytes_received") or 0)
        tx = int(conn.get("bytes_sent") or 0)
        entry["rx_bytes"] += rx
        entry["tx_bytes"] += tx
        entry["connections"] += 1
        if conn.get("local_port") is not None:
            entry["local_ports"].add(int(conn["local_port"]))

        if isinstance(prev_at, (int, float)) and isinstance(prev, dict):
            elapsed = now - float(prev_at)
            if elapsed > 0:
                prev_rx = int(prev.get("rx_bytes") or 0)
                prev_tx = int(prev.get("tx_bytes") or 0)
                entry["rx_bytes_per_sec"] += max(0, rx - prev_rx) / elapsed
                entry["tx_bytes_per_sec"] += max(0, tx - prev_tx) / elapsed

        prev_peers[conn_key] = {"rx_bytes": rx, "tx_bytes": tx}

    def finalize(agg: dict[str, dict[str, Any]], direction: str) -> list[dict[str, Any]]:
        rows = []
        for entry in agg.values():
            peer = entry["peer"]
            history_key = f"{direction}:{peer}"
            samples = rate_history.get(history_key)
            if not isinstance(samples, list):
                samples = []
            samples.append(
                {
                    "t": now,
                    "rx": round(entry["rx_bytes_per_sec"], 1),
                    "tx": round(entry["tx_bytes_per_sec"], 1),
                }
            )
            rate_history[history_key] = samples[-RATE_HISTORY_MAX:]

            rx_now, rx_avg, rx_peak = _rate_stats(rate_history[history_key], "rx")
            tx_now, tx_avg, tx_peak = _rate_stats(rate_history[history_key], "tx")
            hostname = _resolve_hostname(peer, hostname_cache)

            rows.append(
                {
                    "peer": peer,
                    "hostname": hostname,
                    "rx_bytes": entry["rx_bytes"],
                    "tx_bytes": entry["tx_bytes"],
                    "rx_bytes_per_sec": round(rx_now, 1),
                    "tx_bytes_per_sec": round(tx_now, 1),
                    "rx_rate_avg": round(rx_avg, 1),
                    "tx_rate_avg": round(tx_avg, 1),
                    "rx_rate_peak": round(rx_peak, 1),
                    "tx_rate_peak": round(tx_peak, 1),
                    "connections": entry["connections"],
                    "local_ports": sorted(entry["local_ports"]),
                }
            )
        rows.sort(
            key=lambda row: (
                row["rx_bytes_per_sec"] + row["tx_bytes_per_sec"]
                if direction == "in"
                else row["tx_bytes_per_sec"] + row["rx_bytes_per_sec"]
            ),
            reverse=True,
        )
        return rows[:MAX_PEERS]

    inbound_rows = finalize(inbound_agg, "in")
    outbound_rows = finalize(outbound_agg, "out")

    _save_peer_state(
        {
            "timestamp": now,
            "peers": prev_peers,
            "rate_history": rate_history,
            "hostname_cache": hostname_cache,
        }
    )

    return inbound_rows, outbound_rows


def _load_http_offsets() -> dict[str, dict[str, int]]:
    if not HTTP_OFFSET_FILE.exists():
        return {}
    try:
        data = json.loads(HTTP_OFFSET_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_http_offsets(state: dict[str, dict[str, int]]) -> None:
    try:
        HTTP_OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
        HTTP_OFFSET_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def _tail_access_lines(path: Path, state: dict[str, dict[str, int]], limit: int) -> list[str]:
    if limit <= 0 or not path.is_file():
        return []

    key = str(path)
    try:
        stat = path.stat()
    except OSError:
        return []

    inode = int(stat.st_ino)
    saved = state.get(key, {})
    offset = int(saved.get("offset", 0))
    saved_inode = int(saved.get("inode", 0))
    if saved_inode and saved_inode != inode:
        offset = 0
    if offset > stat.st_size:
        offset = 0

    lines: list[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            for raw in handle:
                line = raw.rstrip("\n")
                if line.strip():
                    lines.append(line)
                if len(lines) >= limit:
                    break
            state[key] = {"inode": inode, "offset": handle.tell()}
    except OSError:
        return []

    return lines


def _parse_caddy_access(line: str) -> dict[str, str] | None:
    if not line.startswith("{"):
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    request = data.get("request")
    if not isinstance(request, dict):
        return None

    remote = request.get("remote_addr") or request.get("client_ip") or ""
    if isinstance(remote, str) and ":" in remote:
        remote = remote.rsplit(":", 1)[0]

    method = request.get("method") if isinstance(request.get("method"), str) else "GET"
    uri = request.get("uri") if isinstance(request.get("uri"), str) else ""
    if not uri and isinstance(request.get("url"), str):
        uri = request["url"]

    headers = request.get("headers")
    user_agent = ""
    if isinstance(headers, dict):
        raw = headers.get("User-Agent") or headers.get("user-agent")
        if isinstance(raw, str):
            user_agent = raw
        elif isinstance(raw, list) and raw and isinstance(raw[0], str):
            user_agent = raw[0]

    if not remote:
        return None

    return {
        "client_ip": remote.strip("[]"),
        "method": method.upper(),
        "url": uri,
        "user_agent": user_agent[:300],
    }


def _parse_combined_access(line: str) -> dict[str, str] | None:
    match = _COMBINED_LOG_RE.match(line)
    if not match:
        return None

    quoted = _QUOTED_STRINGS_RE.findall(line)
    user_agent = quoted[1] if len(quoted) >= 2 else ""

    return {
        "client_ip": match.group(1),
        "method": match.group(2).upper(),
        "url": match.group(3),
        "user_agent": user_agent[:300],
    }


def _parse_access_line(line: str) -> dict[str, str] | None:
    parsed = _parse_caddy_access(line)
    if parsed:
        return parsed
    return _parse_combined_access(line)


def _collect_http_clients() -> list[dict[str, Any]]:
    state = _load_http_offsets()
    clients: dict[str, dict[str, Any]] = {}
    seen_files: set[str] = set()

    for raw_path in ACCESS_LOG_FILES:
        if raw_path.endswith("error.log") or raw_path.endswith("error_log"):
            continue
        path_key = raw_path
        if path_key in seen_files:
            continue
        seen_files.add(path_key)

        path = Path(raw_path)
        if not path.is_file():
            continue

        for line in _tail_access_lines(path, state, MAX_ACCESS_LINES):
            parsed = _parse_access_line(line)
            if not parsed:
                continue

            client_ip = parsed["client_ip"]
            user_agent = parsed.get("user_agent") or "–"
            key = f"{client_ip}|{user_agent}"

            entry = clients.setdefault(
                key,
                {
                    "client_ip": client_ip,
                    "user_agent": user_agent,
                    "requests": 0,
                    "last_method": parsed.get("method") or "GET",
                    "last_url": parsed.get("url") or "/",
                },
            )
            entry["requests"] += 1
            entry["last_method"] = parsed.get("method") or entry["last_method"]
            entry["last_url"] = parsed.get("url") or entry["last_url"]

    _save_http_offsets(state)

    rows = sorted(clients.values(), key=lambda row: row["requests"], reverse=True)
    return rows[:MAX_HTTP_CLIENTS]
