from __future__ import annotations

import json
import subprocess
from typing import Any


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


def detect_pm2_apps() -> list[dict[str, Any]]:
    apps: list[dict[str, Any]] = []

    try:
        result = subprocess.run(
            ["pm2", "jlist"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return apps

        payload = json.loads(result.stdout)
        if not isinstance(payload, list):
            return apps

        for app in payload:
            if not isinstance(app, dict):
                continue
            name = str(app.get("name") or "").strip()
            if not name:
                continue

            env = app.get("pm2_env") if isinstance(app.get("pm2_env"), dict) else {}
            monit = app.get("monit") if isinstance(app.get("monit"), dict) else {}
            status = str(env.get("status") or "").strip()
            mode = str(env.get("exec_mode") or env.get("mode") or "").strip()

            apps.append(
                {
                    "name": name,
                    "type": "pm2",
                    "target": name,
                    "mode": mode,
                    "status": status,
                    "cpu": float(monit.get("cpu") or 0),
                    "memory": int(monit.get("memory") or 0),
                    "restarts": int(env.get("restart_time") or 0),
                    "uptime_ms": int(env.get("pm_uptime") or 0),
                }
            )
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, TypeError, ValueError):
        pass

    apps.sort(key=lambda item: str(item["name"]).lower())
    return apps
