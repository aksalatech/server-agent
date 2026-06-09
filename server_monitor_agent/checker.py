from __future__ import annotations

import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Literal

Status = Literal["up", "down", "unknown"]


@dataclass
class ServiceCheck:
    name: str
    check_type: str
    target: str


@dataclass
class CheckResult:
    name: str
    status: Status
    response_ms: int | None
    message: str


def check_systemd(unit: str) -> tuple[Status, str]:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=5,
        )
        state = result.stdout.strip()
        if result.returncode == 0 and state == "active":
            return "up", state
        return "down", state or result.stderr.strip() or "inactive"
    except FileNotFoundError:
        return "unknown", "systemctl not found"
    except subprocess.TimeoutExpired:
        return "down", "timeout"
    except Exception as exc:  # noqa: BLE001
        return "unknown", str(exc)


def check_tcp(target: str, timeout: float = 3.0) -> tuple[Status, str]:
    host = "127.0.0.1"
    port = 0

    if ":" in target:
        host_part, port_part = target.rsplit(":", 1)
        host = host_part or "127.0.0.1"
        try:
            port = int(port_part)
        except ValueError:
            return "unknown", f"invalid port: {port_part}"
    else:
        try:
            port = int(target)
        except ValueError:
            return "unknown", f"invalid target: {target}"

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "up", f"connected {host}:{port}"
    except OSError as exc:
        return "down", str(exc)


def run_check(service: ServiceCheck) -> CheckResult:
    started = time.perf_counter()
    check_type = service.check_type.lower()

    if check_type == "systemd":
        unit = service.target or service.name
        status, message = check_systemd(unit)
    elif check_type == "tcp":
        status, message = check_tcp(service.target)
    else:
        status, message = "unknown", f"unsupported check type: {service.check_type}"

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return CheckResult(
        name=service.name,
        status=status,
        response_ms=elapsed_ms if status != "unknown" else None,
        message=message,
    )


def run_checks(services: list[ServiceCheck]) -> list[CheckResult]:
    return [run_check(svc) for svc in services]
