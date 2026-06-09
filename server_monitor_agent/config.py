from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .checker import ServiceCheck

CONFIG_DIR = Path("/etc/server-monitor-agent")
CONFIG_FILE = CONFIG_DIR / "config.yaml"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"


@dataclass
class AgentConfig:
    server_url: str = ""
    interval_seconds: int = 30
    services: list[ServiceCheck] = field(default_factory=list)


def _parse_service(item: dict[str, Any]) -> ServiceCheck | None:
    name = str(item.get("name", "")).strip()
    if not name:
        return None

    check_type = str(item.get("type") or item.get("checkType") or "systemd").strip().lower()
    target = str(item.get("target") or item.get("unit") or name).strip()

    if item.get("enabled") is False:
        return None

    return ServiceCheck(name=name, check_type=check_type, target=target)


def load_local_config(path: Path | None = None) -> AgentConfig:
    config_path = path or CONFIG_FILE
    if not config_path.exists():
        return AgentConfig()

    with config_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    services: list[ServiceCheck] = []
    for item in data.get("services", []):
        if not isinstance(item, dict):
            continue
        parsed = _parse_service(item)
        if parsed:
            services.append(parsed)

    return AgentConfig(
        server_url=str(data.get("server_url", "")).rstrip("/"),
        interval_seconds=int(data.get("interval_seconds", 30)),
        services=services,
    )


def load_credentials(path: Path | None = None) -> dict[str, str]:
    cred_path = path or CREDENTIALS_FILE
    if not cred_path.exists():
        return {}

    with cred_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    return {
        "api_key": str(data.get("api_key", "")),
        "server_url": str(data.get("server_url", "")).rstrip("/"),
    }


def save_credentials(api_key: str, server_url: str, path: Path | None = None) -> None:
    cred_path = path or CREDENTIALS_FILE
    cred_path.parent.mkdir(parents=True, exist_ok=True)
    cred_path.write_text(
        json.dumps({"api_key": api_key, "server_url": server_url.rstrip("/")}, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(cred_path, 0o600)


def merge_services(local: list[ServiceCheck], remote: list[ServiceCheck]) -> list[ServiceCheck]:
    merged: dict[str, ServiceCheck] = {svc.name: svc for svc in local}
    for svc in remote:
        merged[svc.name] = svc
    return list(merged.values())


def parse_remote_services(payload: dict[str, Any]) -> list[ServiceCheck]:
    services: list[ServiceCheck] = []
    for item in payload.get("services", []):
        if not isinstance(item, dict):
            continue
        parsed = _parse_service(item)
        if parsed:
            services.append(parsed)
    return services
