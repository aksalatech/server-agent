from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from .checker import run_checks
from .client import AgentClient
from .config import (
    CONFIG_FILE,
    load_credentials,
    load_local_config,
    parse_remote_services,
    save_credentials,
    write_local_config,
)
from .detect import detect_services, services_to_yaml_items

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("server_monitor_agent")


def cmd_register(server_url: str, token: str, config_path: Path | None = None) -> int:
    local = load_local_config(config_path)
    resolved_url = server_url or local.server_url
    if not resolved_url:
        logger.error("server_url wajib diisi via --server-url atau config.yaml")
        return 1

    client = AgentClient(resolved_url, api_key="")
    client.session.headers.pop("Authorization", None)

    try:
        result = client.register(token)
    except Exception as exc:  # noqa: BLE001
        logger.error("Registrasi gagal: %s", exc)
        return 1

    api_key = result.get("api_key")
    if not api_key:
        logger.error("Server tidak mengembalikan api_key")
        return 1

    save_credentials(api_key, resolved_url)
    logger.info("Registrasi berhasil untuk host: %s", result.get("host_name"))
    return 0


def cmd_detect(config_path: Path | None = None, write: bool = False) -> int:
    services = detect_services()
    if not services:
        logger.warning("Tidak ada layanan dikenal yang terdeteksi")
        if not write:
            return 0

    items = services_to_yaml_items(services)
    for item in items:
        if item.get("type") == "systemd":
            logger.info("  %s (systemd: %s)", item["name"], item.get("unit"))
        else:
            logger.info("  %s (tcp: %s)", item["name"], item.get("target"))

    if write:
        target = config_path or CONFIG_FILE
        local = load_local_config(target if target.exists() else None)
        write_local_config(
            server_url=local.server_url,
            interval_seconds=local.interval_seconds,
            services=items,
            path=target,
        )
        logger.info("Config diperbarui: %s (%d service)", target, len(items))

    return 0


def _report_detected_services(client: AgentClient) -> None:
    try:
        detected = detect_services()
        items = services_to_yaml_items(detected)
        payload = []
        for item in items:
            entry: dict[str, str] = {
                "name": str(item["name"]),
                "type": str(item["type"]),
            }
            if item.get("type") == "systemd":
                entry["unit"] = str(item.get("unit") or item["name"])
                entry["target"] = entry["unit"]
            else:
                entry["target"] = str(item.get("target") or item["name"])
            payload.append(entry)

        error = ""
        if not payload:
            error = "Tidak ada layanan dikenal yang terdeteksi"

        client.send_detect_report(payload, error=error)
        logger.info("Laporan deteksi service terkirim (%d service)", len(payload))
    except Exception as exc:  # noqa: BLE001
        logger.error("Gagal mengirim laporan deteksi: %s", exc)


def cmd_run(config_path: Path | None = None, once: bool = False) -> int:
    creds = load_credentials()
    api_key = creds.get("api_key", "")
    server_url = creds.get("server_url", "")

    if not api_key or not server_url:
        logger.error("Credentials belum ada. Jalankan perintah register terlebih dahulu.")
        return 1

    local = load_local_config(config_path)
    client = AgentClient(server_url, api_key)

    while True:
        try:
            remote_payload = client.fetch_config()
            if remote_payload.get("detect_requested"):
                _report_detected_services(client)

            remote_services = parse_remote_services(remote_payload)
            interval = int(remote_payload.get("interval_seconds") or local.interval_seconds)
            # Hanya pantau service yang dikonfigurasi di dashboard (database).
            services = remote_services

            if not services:
                logger.warning("Tidak ada service dikonfigurasi di dashboard")

            results = run_checks(services)
            payload = [
                {
                    "name": r.name,
                    "status": r.status,
                    "response_ms": r.response_ms,
                    "message": r.message,
                }
                for r in results
            ]

            response = client.send_heartbeat(payload)
            logger.info(
                "Heartbeat terkirim (%s services, host_id=%s)",
                len(payload),
                response.get("host_id"),
            )
            for result in results:
                logger.info("  %s: %s (%s)", result.name, result.status, result.message)

        except Exception as exc:  # noqa: BLE001
            logger.error("Loop monitoring gagal: %s", exc)

        if once:
            return 0

        sleep_for = max(10, interval if "interval" in locals() else local.interval_seconds)
        time.sleep(sleep_for)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Server Monitor Agent")
    sub = parser.add_subparsers(dest="command", required=True)

    register = sub.add_parser("register", help="Daftarkan agent dengan registration token")
    register.add_argument("--server-url", default="", help="URL server monitoring")
    register.add_argument("--token", required=True, help="Registration token dari dashboard")
    register.add_argument("--config", type=Path, default=None, help="Path config.yaml")

    detect = sub.add_parser("detect", help="Deteksi layanan systemd yang aktif")
    detect.add_argument("--config", type=Path, default=None, help="Path config.yaml")
    detect.add_argument("--write", action="store_true", help="Tulis hasil ke config.yaml")

    run = sub.add_parser("run", help="Jalankan loop monitoring")
    run.add_argument("--config", type=Path, default=None, help="Path config.yaml")
    run.add_argument("--once", action="store_true", help="Jalankan sekali lalu keluar")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "register":
        return cmd_register(args.server_url, args.token, args.config)
    if args.command == "detect":
        return cmd_detect(args.config, write=args.write)
    if args.command == "run":
        config_path = args.config or (CONFIG_FILE if CONFIG_FILE.exists() else None)
        return cmd_run(config_path, once=args.once)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
