from __future__ import annotations

import json
import re
import subprocess
import glob
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import CONFIG_DIR

LOG_OFFSETS_FILE = CONFIG_DIR / "log-offsets.json"
MAX_LINES_PER_HEARTBEAT = 40
MAX_LINE_LENGTH = 2000

LARAVEL_LOG_GLOBS = [
    "/var/www/html/*/storage/logs/laravel*.log",
    "/var/www/*/storage/logs/laravel*.log",
    "/home/*/storage/logs/laravel*.log",
    "/srv/*/storage/logs/laravel*.log",
]

NGINX_LOG_FILES = [
    "/var/log/nginx/error.log",
    "/var/log/nginx/access.log",
]

APACHE_LOG_FILES = [
    "/var/log/apache2/error.log",
    "/var/log/apache2/access.log",
    "/var/log/httpd/error_log",
    "/var/log/httpd/access_log",
]

CADDY_LOG_FILES = [
    "/var/log/caddy/access.log",
    "/var/log/caddy/error.log",
]


@dataclass
class AppLogLine:
    source: str
    level: str | None
    message: str
    service_name: str


def collect_application_logs() -> list[dict[str, Any]]:
    state = _load_offsets()
    entries: list[AppLogLine] = []

    collectors = (
        _collect_nginx_logs,
        _collect_apache_logs,
        _collect_caddy_logs,
        _collect_laravel_logs,
        _collect_pm2_logs,
    )

    for collector in collectors:
        if len(entries) >= MAX_LINES_PER_HEARTBEAT:
            break
        try:
            remaining = MAX_LINES_PER_HEARTBEAT - len(entries)
            entries.extend(collector(state, remaining))
        except Exception:
            continue

    _save_offsets(state)
    return [
        {
            "source": entry.source,
            "level": entry.level,
            "message": entry.message,
            "service_name": entry.service_name,
        }
        for entry in entries[:MAX_LINES_PER_HEARTBEAT]
    ]


def _load_offsets() -> dict[str, dict[str, int]]:
    if not LOG_OFFSETS_FILE.exists():
        return {}
    try:
        data = json.loads(LOG_OFFSETS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_offsets(state: dict[str, dict[str, int]]) -> None:
    LOG_OFFSETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_OFFSETS_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _tail_file(path: Path, state: dict[str, dict[str, int]], limit: int) -> list[str]:
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
                if not line.strip():
                    continue
                lines.append(line[:MAX_LINE_LENGTH])
                if len(lines) >= limit:
                    break
            state[key] = {"inode": inode, "offset": handle.tell()}
    except OSError:
        return []

    return lines


def _journal_lines(unit: str, state: dict[str, dict[str, int]], limit: int) -> list[str]:
    if limit <= 0:
        return []

    key = f"journal:{unit}"
    cursor = str(state.get(key, {}).get("cursor", ""))

    command = [
        "journalctl",
        "-u",
        unit,
        "--no-pager",
        "-o",
        "json",
        "-n",
        str(max(limit, 1)),
    ]
    if cursor:
        command.extend(["--after-cursor", cursor])

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    if result.returncode != 0:
        return []

    lines: list[str] = []
    last_cursor = cursor
    for raw in result.stdout.splitlines():
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue

        message = str(entry.get("MESSAGE") or "").strip()
        if message:
            lines.append(message[:MAX_LINE_LENGTH])

        next_cursor = entry.get("__CURSOR__")
        if isinstance(next_cursor, str) and next_cursor:
            last_cursor = next_cursor

    if last_cursor:
        state[key] = {"cursor": last_cursor}

    return lines[-limit:]


def _detect_level(line: str) -> str | None:
    lowered = line.lower()
    if any(token in lowered for token in (" error", "[error]", ".error:", " err ", "critical", "fatal")):
        return "error"
    if any(token in lowered for token in (" warn", "[warn]", ".warning:", "warning")):
        return "warn"
    if any(token in lowered for token in (" info", "[info]", ".info:", " notice")):
        return "info"
    return None


def _collect_file_logs(
    source: str,
    paths: list[str],
    state: dict[str, dict[str, int]],
    limit: int,
    journal_unit: str | None = None,
) -> list[AppLogLine]:
    entries: list[AppLogLine] = []
    remaining = limit

    for raw_path in paths:
        if remaining <= 0:
            break
        path = Path(raw_path)
        for line in _tail_file(path, state, remaining):
            entries.append(
                AppLogLine(
                    source=source,
                    level=_detect_level(line),
                    message=line,
                    service_name=f"{source}:{path.name}",
                )
            )
            remaining -= 1

    if remaining > 0 and journal_unit:
        for line in _journal_lines(journal_unit, state, remaining):
            entries.append(
                AppLogLine(
                    source=source,
                    level=_detect_level(line),
                    message=line,
                    service_name=f"{source}:journal",
                )
            )
            remaining -= 1

    return entries


def _collect_nginx_logs(state: dict[str, dict[str, int]], limit: int) -> list[AppLogLine]:
    return _collect_file_logs("nginx", NGINX_LOG_FILES, state, limit, journal_unit="nginx")


def _collect_apache_logs(state: dict[str, dict[str, int]], limit: int) -> list[AppLogLine]:
    entries = _collect_file_logs("apache", APACHE_LOG_FILES, state, limit, journal_unit="apache2")
    if len(entries) < limit:
        entries.extend(
            _collect_file_logs("apache", [], state, limit - len(entries), journal_unit="httpd")
        )
    return entries


def _collect_caddy_logs(state: dict[str, dict[str, int]], limit: int) -> list[AppLogLine]:
    return _collect_file_logs("caddy", CADDY_LOG_FILES, state, limit, journal_unit="caddy")


def _collect_laravel_logs(state: dict[str, dict[str, int]], limit: int) -> list[AppLogLine]:
    entries: list[AppLogLine] = []
    remaining = limit
    seen: set[str] = set()

    for pattern in LARAVEL_LOG_GLOBS:
        if remaining <= 0:
            break
        for raw in sorted(glob.glob(pattern)):
            path = Path(raw)
            key = str(path)
            if key in seen:
                continue
            seen.add(key)

            project_name = path.parent.parent.parent.name
            for line in _tail_file(path, state, remaining):
                entries.append(
                    AppLogLine(
                        source="laravel",
                        level=_detect_laravel_level(line),
                        message=line,
                        service_name=f"laravel:{project_name}",
                    )
                )
                remaining -= 1
                if remaining <= 0:
                    break

    return entries


def _detect_laravel_level(line: str) -> str | None:
    match = re.search(r"\.(DEBUG|INFO|NOTICE|WARNING|ERROR|CRITICAL|ALERT|EMERGENCY):", line)
    if not match:
        return _detect_level(line)
    return match.group(1).lower()


def _collect_pm2_logs(state: dict[str, dict[str, int]], limit: int) -> list[AppLogLine]:
    entries: list[AppLogLine] = []
    remaining = limit

    try:
        result = subprocess.run(
            ["pm2", "jlist"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return entries

    if result.returncode != 0 or not result.stdout.strip():
        return entries

    try:
        apps = json.loads(result.stdout)
    except json.JSONDecodeError:
        return entries

    if not isinstance(apps, list):
        return entries

    for app in apps:
        if remaining <= 0:
            break
        if not isinstance(app, dict):
            continue

        name = str(app.get("name") or "pm2-app")
        env = app.get("pm2_env") if isinstance(app.get("pm2_env"), dict) else {}
        paths = []
        for key in ("pm_err_log_path", "pm_out_log_path"):
            value = env.get(key)
            if isinstance(value, str) and value.strip():
                paths.append(value.strip())

        for raw_path in paths:
            if remaining <= 0:
                break
            path = Path(raw_path)
            stream = "err" if "err" in path.name else "out"
            for line in _tail_file(path, state, remaining):
                level = _detect_level(line) or ("error" if stream == "err" else "info")
                entries.append(
                    AppLogLine(
                        source="pm2",
                        level=level,
                        message=line,
                        service_name=f"pm2:{name}",
                    )
                )
                remaining -= 1

    return entries
