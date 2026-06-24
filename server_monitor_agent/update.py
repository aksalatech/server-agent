from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

INSTALL_DIR = Path("/opt/server-monitor-agent")
LAST_CHECK_FILE = Path("/etc/server-monitor-agent/last-update-check.json")
DEFAULT_BRANCH = "main"
AGENT_UNIT = "server-monitor-agent"


@dataclass
class UpdateResult:
    old_commit: str
    new_commit: str
    updated: bool
    message: str


def _run_git(args: list[str], cwd: Path = INSTALL_DIR) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def get_current_commit() -> str:
    if not (INSTALL_DIR / ".git").is_dir():
        return ""
    result = _run_git(["rev-parse", "HEAD"])
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _requirements_changed(old_commit: str, new_commit: str) -> bool:
    if not old_commit or not new_commit or old_commit == new_commit:
        return False
    result = _run_git(["diff", "--name-only", old_commit, new_commit, "--", "requirements.txt"])
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip())


def _install_requirements() -> None:
    pip = INSTALL_DIR / "venv" / "bin" / "pip"
    requirements = INSTALL_DIR / "requirements.txt"
    if not pip.is_file() or not requirements.is_file():
        raise RuntimeError("venv atau requirements.txt tidak ditemukan")

    result = subprocess.run(
        [str(pip), "install", "-r", str(requirements)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    output = (result.stderr or result.stdout or "").strip()
    if result.returncode != 0:
        raise RuntimeError(output or "pip install gagal")


def run_agent_update(branch: str = DEFAULT_BRANCH) -> UpdateResult:
    if not (INSTALL_DIR / ".git").is_dir():
        raise RuntimeError(f"{INSTALL_DIR} bukan git repository")

    old_commit = get_current_commit()
    if not old_commit:
        raise RuntimeError("Tidak dapat membaca commit saat ini")

    fetch = _run_git(["fetch", "origin", branch])
    if fetch.returncode != 0:
        output = (fetch.stderr or fetch.stdout or "").strip()
        raise RuntimeError(output or f"git fetch origin {branch} gagal")

    remote_ref = _run_git(["rev-parse", f"origin/{branch}"])
    if remote_ref.returncode != 0:
        output = (remote_ref.stderr or remote_ref.stdout or "").strip()
        raise RuntimeError(output or f"branch origin/{branch} tidak ditemukan")

    new_commit = remote_ref.stdout.strip()
    if old_commit == new_commit:
        record_last_check()
        short = old_commit[:7]
        return UpdateResult(
            old_commit=old_commit,
            new_commit=new_commit,
            updated=False,
            message=f"Agent sudah versi terbaru ({short})",
        )

    pull = _run_git(["pull", "--ff-only", "origin", branch])
    if pull.returncode != 0:
        output = (pull.stderr or pull.stdout or "").strip()
        raise RuntimeError(output or "git pull gagal")

    pulled_commit = get_current_commit()
    if not pulled_commit:
        raise RuntimeError("Tidak dapat membaca commit setelah pull")

    if _requirements_changed(old_commit, pulled_commit):
        _install_requirements()

    record_last_check()
    return UpdateResult(
        old_commit=old_commit,
        new_commit=pulled_commit,
        updated=True,
        message=f"Diperbarui {old_commit[:7]} → {pulled_commit[:7]}",
    )


def restart_agent_service(timeout: float = 30.0) -> None:
    result = subprocess.run(
        ["systemctl", "restart", AGENT_UNIT],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    output = (result.stderr or result.stdout or "").strip()
    if result.returncode != 0:
        raise RuntimeError(output or f"systemctl restart {AGENT_UNIT} gagal")


def load_last_check() -> datetime | None:
    if not LAST_CHECK_FILE.is_file():
        return None
    try:
        payload = json.loads(LAST_CHECK_FILE.read_text(encoding="utf-8"))
        raw = payload.get("checked_at")
        if not isinstance(raw, str) or not raw.strip():
            return None
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (OSError, ValueError, TypeError):
        return None


def record_last_check() -> None:
    LAST_CHECK_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"checked_at": datetime.now(timezone.utc).isoformat()}
    LAST_CHECK_FILE.write_text(json.dumps(payload), encoding="utf-8")


def should_auto_update(enabled: bool, interval_minutes: int) -> bool:
    if not enabled:
        return False
    interval_minutes = max(15, interval_minutes)
    last_check = load_last_check()
    if last_check is None:
        return True
    elapsed = datetime.now(timezone.utc) - last_check.astimezone(timezone.utc)
    return elapsed.total_seconds() >= interval_minutes * 60
