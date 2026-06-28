from __future__ import annotations

import subprocess


def restart_systemd_unit(unit: str, timeout: float = 60.0) -> str:
    name = unit.strip()
    if not name:
        raise ValueError("unit systemd kosong")

    if not all(part.replace("-", "").replace("@", "").replace("_", "").isalnum() for part in name.split(".")):
        raise ValueError(f"unit tidak valid: {name}")

    result = subprocess.run(
        ["systemctl", "restart", name],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    output = (result.stderr or result.stdout or "").strip()
    if result.returncode != 0:
        raise RuntimeError(output or f"systemctl restart {name} gagal")

    verify = subprocess.run(
        ["systemctl", "is-active", name],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    state = verify.stdout.strip()
    if verify.returncode != 0 or state != "active":
        raise RuntimeError(state or output or f"service {name} tidak active setelah restart")

    return f"{name} active"


def restart_docker_container(target: str, timeout: float = 120.0) -> str:
    name = target.strip()
    if not name:
        raise ValueError("target container kosong")

    result = subprocess.run(
        ["docker", "restart", name],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    output = (result.stderr or result.stdout or "").strip()
    if result.returncode != 0:
        raise RuntimeError(output or f"docker restart {name} gagal")

    verify = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", name],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    state = verify.stdout.strip().lower()
    if verify.returncode != 0 or state != "true":
        raise RuntimeError(output or f"container {name} tidak running setelah restart")

    return f"{name} running"


def restart_pm2_app(target: str, timeout: float = 60.0) -> str:
    name = target.strip()
    if not name:
        raise ValueError("target PM2 kosong")

    result = subprocess.run(
        ["pm2", "restart", name],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    output = (result.stderr or result.stdout or "").strip()
    if result.returncode != 0:
        raise RuntimeError(output or f"pm2 restart {name} gagal")

    return f"{name} restarted"
