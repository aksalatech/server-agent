from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


def _nvm_pm2_candidates() -> list[str]:
    candidates: list[str] = []
    homes = {Path.home()}
    homes.add(Path("/root"))
    try:
        import pwd

        homes.add(Path(pwd.getpwuid(os.getuid()).pw_dir))
    except (ImportError, KeyError):
        pass

    for home in homes:
        versions_dir = home / ".nvm" / "versions" / "node"
        if not versions_dir.is_dir():
            continue
        for pm2_path in sorted(versions_dir.glob("*/bin/pm2"), reverse=True):
            candidates.append(str(pm2_path))
    return candidates


def resolve_pm2_bin() -> str | None:
    """Locate pm2 CLI (systemd often lacks NVM in PATH)."""
    explicit = os.environ.get("PM2_BIN", "").strip()
    if explicit and Path(explicit).is_file():
        return explicit

    candidates = _nvm_pm2_candidates()
    candidates.extend(
        [
            "/usr/local/bin/pm2",
            "/usr/bin/pm2",
            "/snap/bin/pm2",
        ]
    )

    for candidate in candidates:
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)

    extra_dirs: list[str] = []
    for candidate in candidates:
        extra_dirs.append(str(Path(candidate).parent))
    for home in {Path.home(), Path("/root")}:
        nvm_versions = home / ".nvm" / "versions" / "node"
        if nvm_versions.is_dir():
            for node_bin in nvm_versions.glob("*/bin"):
                extra_dirs.append(str(node_bin))
    extra_dirs.extend(["/usr/local/bin", "/usr/bin", "/snap/bin"])

    path_env = os.pathsep.join(dict.fromkeys(extra_dirs + (os.environ.get("PATH", "").split(os.pathsep))))
    return shutil.which("pm2", path=path_env)


def _nvm_node_bin_dirs() -> list[str]:
    dirs: list[str] = []
    homes = {Path.home(), Path("/root")}
    try:
        import pwd

        homes.add(Path(pwd.getpwuid(os.getuid()).pw_dir))
    except (ImportError, KeyError):
        pass

    for home in homes:
        versions_dir = home / ".nvm" / "versions" / "node"
        if not versions_dir.is_dir():
            continue
        for node_bin in versions_dir.glob("*/bin"):
            dirs.append(str(node_bin))
    return dirs


def resolve_node_bin(near_pm2: str | None = None) -> str | None:
    explicit = os.environ.get("NODE_BIN", "").strip()
    if explicit and Path(explicit).is_file():
        return explicit

    if near_pm2:
        sibling = Path(near_pm2).resolve().parent / "node"
        if sibling.is_file() and os.access(sibling, os.X_OK):
            return str(sibling)

    extra_dirs = _nvm_node_bin_dirs()
    extra_dirs.extend(["/usr/local/bin", "/usr/bin", "/snap/bin"])
    path_env = os.pathsep.join(dict.fromkeys(extra_dirs + os.environ.get("PATH", "").split(os.pathsep)))
    return shutil.which("node", path=path_env)


def _augmented_path_env(pm2_bin: str) -> dict[str, str]:
    env = dict(os.environ)
    extra_dirs = [str(Path(pm2_bin).resolve().parent), *_nvm_node_bin_dirs(), "/usr/local/bin", "/usr/bin", "/snap/bin"]
    existing = env.get("PATH", "").split(os.pathsep)
    env["PATH"] = os.pathsep.join(dict.fromkeys(extra_dirs + existing))
    return env


def _build_pm2_command(pm2_bin: str, args: list[str]) -> tuple[list[str], dict[str, str]]:
    env = _augmented_path_env(pm2_bin)
    pm2_path = Path(pm2_bin).resolve()
    node_bin = resolve_node_bin(str(pm2_path))
    if node_bin:
        return [node_bin, str(pm2_path), *args], env
    return [pm2_bin, *args], env


def run_pm2(args: list[str], timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    pm2_bin = resolve_pm2_bin()
    if not pm2_bin:
        raise FileNotFoundError("pm2 CLI tidak ditemukan (cek NVM/PATH atau set PM2_BIN)")

    cmd, env = _build_pm2_command(pm2_bin, args)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )


def pm2_jlist(timeout: float = 15.0) -> list[dict[str, Any]]:
    result = run_pm2(["jlist"], timeout=timeout)
    if result.returncode != 0 or not result.stdout.strip():
        stderr = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(stderr or "pm2 jlist gagal")

    payload = json.loads(result.stdout)
    if not isinstance(payload, list):
        raise RuntimeError("pm2 jlist mengembalikan format tidak valid")

    return [item for item in payload if isinstance(item, dict)]


def parse_pm2_app(app: dict[str, Any]) -> dict[str, Any] | None:
    name = str(app.get("name") or "").strip()
    if not name:
        return None

    env = app.get("pm2_env") if isinstance(app.get("pm2_env"), dict) else {}
    monit = app.get("monit") if isinstance(app.get("monit"), dict) else {}
    status = str(env.get("status") or "").strip()
    mode = str(env.get("exec_mode") or env.get("mode") or "").strip()

    return {
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
