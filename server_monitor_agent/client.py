from __future__ import annotations

import socket
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

    def send_heartbeat(self, services: list[dict[str, Any]]) -> dict[str, Any]:
        hostname = socket.gethostname()
        ip_address = _detect_ip()

        response = self.session.post(
            f"{self.server_url}/api/agent/v1/heartbeat",
            json={
                "hostname": hostname,
                "ip_address": ip_address,
                "services": services,
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


def _detect_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
