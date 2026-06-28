from __future__ import annotations

import json
import subprocess
from typing import Any

from .pm2_cli import parse_pm2_app, pm2_jlist, resolve_pm2_bin


def detect_docker_containers() -> list[dict[str, str]]:
    containers: list[dict[str, str]] = []

    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--no-trunc", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            return containers

        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue

            raw_name = str(row.get("Names") or row.get("Name") or "").strip()
            name = raw_name.lstrip("/") or str(row.get("ID") or "").strip()
            container_id = str(row.get("ID") or name).strip()
            if not name:
                continue

            containers.append(
                {
                    "name": name,
                    "type": "docker",
                    "target": container_id,
                    "image": str(row.get("Image") or "").strip(),
                    "state": str(row.get("State") or "").strip(),
                    "status": str(row.get("Status") or "").strip(),
                    "ports": str(row.get("Ports") or "").strip(),
                }
            )
    except (OSError, subprocess.TimeoutExpired):
        pass

    containers.sort(key=lambda item: item["name"].lower())
    return containers


def detect_pm2_apps() -> tuple[list[dict[str, Any]], str | None]:
    """Return (apps, error_message). error is set only when discovery fails."""
    if not resolve_pm2_bin():
        return [], "pm2 CLI tidak ditemukan (install NVM/PM2 atau set PM2_BIN di environment service)"

    try:
        raw_apps = pm2_jlist()
    except (OSError, subprocess.TimeoutExpired):
        return [], "pm2 jlist timeout atau gagal dijalankan"
    except (json.JSONDecodeError, RuntimeError) as exc:
        return [], str(exc) or "pm2 jlist gagal"

    apps: list[dict[str, Any]] = []
    for app in raw_apps:
        parsed = parse_pm2_app(app)
        if parsed:
            apps.append(parsed)

    apps.sort(key=lambda item: str(item["name"]).lower())
    return apps, None
