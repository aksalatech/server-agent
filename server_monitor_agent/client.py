from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import requests


class AgentClient:
    def __init__(self, server_url: str, api_key: str, timeout: int = 15) -> None:
        self.server_url = server_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "server-monitor-agent/0.1.0",
            }
        )

    def register(self, registration_token: str) -> dict[str, Any]:
        response = self.session.post(
            f"{self.server_url}/api/agent/v1/register",
            json={"registration_token": registration_token},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def fetch_config(self) -> dict[str, Any]:
        response = self.session.get(
            f"{self.server_url}/api/agent/v1/config",
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def send_heartbeat(
        self,
        services: list[dict[str, Any]],
        system_metrics: dict[str, Any] | None = None,
        app_logs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        hostname = socket.gethostname()
        ip_address = _detect_ip()

        payload: dict[str, Any] = {
            "hostname": hostname,
            "ip_address": ip_address,
            "services": services,
        }
        if system_metrics:
            payload["system_metrics"] = system_metrics
        if app_logs:
            payload["app_logs"] = app_logs

        response = self.session.post(
            f"{self.server_url}/api/agent/v1/heartbeat",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def report_restart_job(
        self,
        job_id: int,
        status: str,
        message: str = "",
    ) -> dict[str, Any]:
        response = self.session.post(
            f"{self.server_url}/api/agent/v1/restart-job",
            json={
                "job_id": str(job_id),
                "status": status,
                "message": message,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def send_detect_report(self, services: list[dict[str, Any]], error: str = "") -> dict[str, Any]:
        payload: dict[str, Any] = {"services": services}
        if error:
            payload["error"] = error

        response = self.session.post(
            f"{self.server_url}/api/agent/v1/detect-report",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def send_domains_report(self, domains: list[dict[str, Any]], error: str = "") -> dict[str, Any]:
        payload: dict[str, Any] = {"domains": domains}
        if error:
            payload["error"] = error

        response = self.session.post(
            f"{self.server_url}/api/agent/v1/domains-report",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def send_databases_report(
        self, databases: list[dict[str, Any]], error: str = ""
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"databases": databases}
        if error:
            payload["error"] = error

        response = self.session.post(
            f"{self.server_url}/api/agent/v1/databases-report",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def report_backup_job(
        self,
        job_id: int,
        status: str,
        message: str = "",
        file_path: Path | None = None,
    ) -> dict[str, Any]:
        data = {
            "job_id": str(job_id),
            "status": status,
            "message": message,
        }

        files = None
        if file_path is not None:
            files = {
                "file": (
                    file_path.name,
                    file_path.open("rb"),
                    "application/octet-stream",
                )
            }

        response = self.session.post(
            f"{self.server_url}/api/agent/v1/backup-job",
            data=data,
            files=files,
            timeout=max(self.timeout, 300),
        )
        if files:
            files["file"][1].close()
        response.raise_for_status()
        return response.json()

    def download_backup_for_restore(self, job_id: int, destination: Path) -> None:
        response = self.session.get(
            f"{self.server_url}/api/agent/v1/backup-job",
            params={"job_id": job_id},
            timeout=max(self.timeout, 300),
            stream=True,
        )
        response.raise_for_status()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)


def _detect_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
