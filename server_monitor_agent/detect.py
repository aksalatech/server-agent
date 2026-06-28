from __future__ import annotations

import subprocess
from typing import Any


def _enabled_units() -> set[str]:
    """Return enabled or currently active systemd service unit names."""
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


def _list_all_service_units() -> list[dict[str, str]]:
    """Enumerate all systemd service units on the host."""
    units: list[dict[str, str]] = []

    try:
        result = subprocess.run(
            [
                "systemctl",
                "list-units",
                "--type=service",
                "--all",
                "--no-legend",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 4)
            if len(parts) < 4:
                continue
            unit_file = parts[0]
            if not unit_file.endswith(".service"):
                continue
            unit_name = unit_file[: -len(".service")]
            active = parts[2]
            sub = parts[3]
            units.append(
                {
                    "name": unit_name,
                    "type": "systemd",
                    "unit": unit_name,
                    "state": f"{active}/{sub}",
                }
            )
    except (OSError, subprocess.TimeoutExpired):
        pass

    units.sort(key=lambda item: item["name"].lower())
    return units


def detect_services() -> list[dict[str, str]]:
    return _list_all_service_units()


def services_to_yaml_items(services: list[dict[str, str]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for svc in services:
        item: dict[str, Any] = {"name": svc["name"], "type": svc["type"]}
        if svc["type"] == "systemd":
            item["unit"] = svc.get("unit", svc["name"])
        else:
            item["target"] = svc["target"]
        if svc.get("state"):
            item["state"] = svc["state"]
        items.append(item)
    return items
