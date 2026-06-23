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
