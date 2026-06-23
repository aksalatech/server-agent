from __future__ import annotations

import subprocess
from typing import Any

from .checker import check_tcp

# systemd unit name -> (display name, check type, target)
KNOWN_UNITS: dict[str, tuple[str, str, str]] = {
    "nginx": ("nginx", "systemd", "nginx"),
    "apache2": ("apache", "systemd", "apache2"),
    "httpd": ("apache", "systemd", "httpd"),
    "caddy": ("caddy", "systemd", "caddy"),
    "mysql": ("mysql", "systemd", "mysql"),
    "mysqld": ("mysql", "systemd", "mysqld"),
    "mariadb": ("mariadb", "systemd", "mariadb"),
    "postgresql": ("postgres", "systemd", "postgresql"),
    "redis-server": ("redis", "systemd", "redis-server"),
    "redis": ("redis", "systemd", "redis"),
    "docker": ("docker", "systemd", "docker"),
    "ssh": ("ssh", "systemd", "ssh"),
    "sshd": ("ssh", "systemd", "sshd"),
}

TCP_BY_UNIT: dict[str, tuple[str, str]] = {
    "nginx": ("nginx-http", "127.0.0.1:80"),
    "apache2": ("apache-http", "127.0.0.1:80"),
    "httpd": ("apache-http", "127.0.0.1:80"),
    "caddy": ("caddy-http", "127.0.0.1:80"),
    "mysql": ("mysql-tcp", "127.0.0.1:3306"),
    "mysqld": ("mysql-tcp", "127.0.0.1:3306"),
    "mariadb": ("mariadb-tcp", "127.0.0.1:3306"),
    "postgresql": ("postgres-tcp", "127.0.0.1:5432"),
    "redis-server": ("redis-tcp", "127.0.0.1:6379"),
    "redis": ("redis-tcp", "127.0.0.1:6379"),
}


def _enabled_units() -> set[str]:
    units: set[str] = set()

    try:
        enabled = subprocess.run(
            [
                "systemctl",
                "list-unit-files",
                "--type=service",
                "--state=enabled",
                "--no-legend",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        for line in enabled.stdout.splitlines():
            unit = line.split()[0] if line.strip() else ""
            if unit.endswith(".service"):
                units.add(unit[: -len(".service")])
    except (OSError, subprocess.TimeoutExpired):
        pass

    # Sertakan juga service yang sedang active (meski tidak enabled di boot).
    try:
        active = subprocess.run(
            [
                "systemctl",
                "list-units",
                "--type=service",
                "--state=active",
                "--no-legend",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        for line in active.stdout.splitlines():
            unit = line.split()[0] if line.strip() else ""
            if unit.endswith(".service"):
                units.add(unit[: -len(".service")])
    except (OSError, subprocess.TimeoutExpired):
        pass

    return units


def detect_services() -> list[dict[str, str]]:
    enabled = _enabled_units()
    services: list[dict[str, str]] = []
    seen_names: set[str] = set()
    matched_units: set[str] = set()

    for unit in sorted(enabled):
        if unit not in KNOWN_UNITS:
            continue
        name, check_type, target = KNOWN_UNITS[unit]
        if name in seen_names:
            continue
        services.append({"name": name, "type": check_type, "unit": target})
        seen_names.add(name)
        matched_units.add(unit)

    for unit in matched_units:
        if unit not in TCP_BY_UNIT:
            continue
        tcp_name, tcp_target = TCP_BY_UNIT[unit]
        if tcp_name in seen_names:
            continue
        # Hanya tambahkan cek TCP jika port benar-benar menerima koneksi.
        # Banyak MariaDB/PostgreSQL hanya listen via Unix socket, bukan TCP localhost.
        status, _ = check_tcp(tcp_target)
        if status != "up":
            continue
        services.append({"name": tcp_name, "type": "tcp", "target": tcp_target})
        seen_names.add(tcp_name)

    return services


def services_to_yaml_items(services: list[dict[str, str]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for svc in services:
        item: dict[str, Any] = {"name": svc["name"], "type": svc["type"]}
        if svc["type"] == "systemd":
            item["unit"] = svc.get("unit", svc["name"])
        else:
            item["target"] = svc["target"]
        items.append(item)
    return items
