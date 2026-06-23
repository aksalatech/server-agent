from __future__ import annotations

import os
import shutil
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Status = Literal["up", "down", "unknown"]


@dataclass
class ServiceCheck:
    name: str
    check_type: str
    target: str
    db_user: str | None = None
    db_password: str | None = None


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


def check_http(url: str, timeout: float = 5.0) -> tuple[Status, str]:
    target = url.strip()
    if not target.startswith("http://") and not target.startswith("https://"):
        target = f"https://{target}"

    try:
        context = ssl.create_default_context()
        request = urllib.request.Request(
            target,
            method="GET",
            headers={"User-Agent": "ServerMonitorAgent/1.0"},
        )
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            code = response.getcode()
            if 200 <= code < 400:
                return "up", f"HTTP {code}"
            return "down", f"HTTP {code}"
    except urllib.error.HTTPError as exc:
        if 200 <= exc.code < 400:
            return "up", f"HTTP {exc.code}"
        return "down", f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return "down", str(exc)


def _parse_host_port(target: str, default_port: int) -> tuple[str, int]:
    if target.startswith("socket:"):
        return target, _postgres_port_from_socket(target[7:])

    host = "127.0.0.1"
    port = default_port
    if ":" in target:
        host_part, port_part = target.rsplit(":", 1)
        host = host_part or "127.0.0.1"
        try:
            port = int(port_part)
        except ValueError:
            port = default_port
    return host, port


def _postgres_port_from_socket(socket_path: str) -> int:
    if ".s.PGSQL." in socket_path:
        try:
            return int(socket_path.split(".s.PGSQL.")[-1])
        except ValueError:
            pass
    return 5432


def _postgres_psql_host_port(connection: str) -> tuple[str, int]:
    if connection.startswith("socket:"):
        socket_file = connection[7:]
        return socket_file.rsplit("/", 1)[0], _postgres_port_from_socket(socket_file)
    return _parse_host_port(connection, 5432)


def _postgres_run_as_db_user(cmd: list[str]) -> list[str]:
    if os.geteuid() != 0:
        return cmd
    if shutil.which("runuser"):
        return ["runuser", "-u", "postgres", "--", *cmd]
    if shutil.which("sudo"):
        return ["sudo", "-u", "postgres", *cmd]
    return cmd


def build_postgres_psql_cmd(
    connection: str,
    extra_args: list[str],
    db_user: str | None = None,
) -> list[str]:
    host, port = _postgres_psql_host_port(connection)
    cmd = ["psql", "-h", host, "-p", str(port)]
    if db_user:
        cmd.extend(["-U", db_user])
    cmd.extend(extra_args)
    if not db_user:
        return _postgres_run_as_db_user(cmd)
    return cmd


def _run_command(cmd: list[str], timeout: float = 5.0) -> tuple[Status, str]:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (result.stdout or result.stderr or "").strip()
        if result.returncode == 0:
            return "up", output or "ok"
        return "down", output or f"exit {result.returncode}"
    except FileNotFoundError:
        return "unknown", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return "down", "timeout"
    except Exception as exc:  # noqa: BLE001
        return "unknown", str(exc)


def _split_db_target(target: str) -> tuple[str, str | None]:
    if "#" not in target:
        return target, None
    connection, database = target.rsplit("#", 1)
    return connection, database or None


def _mysql_auth_args(db_user: str | None, db_password: str | None) -> list[str]:
    args: list[str] = []
    if db_user:
        args.extend(["-u", db_user])
    if db_password:
        args.append(f"-p{db_password}")
    return args


def check_mysql(
    target: str,
    db_user: str | None = None,
    db_password: str | None = None,
) -> tuple[Status, str]:
    connection, database_name = _split_db_target(target)

    if connection.startswith("socket:"):
        socket_path = connection[7:]
        status, message = _run_command(
            [
                "mysqladmin",
                *_mysql_defaults_file(),
                *_mysql_auth_args(db_user, db_password),
                "ping",
                f"--socket={socket_path}",
            ]
        )
        if status == "unknown":
            status, message = _run_command(
                ["mysqladmin", "ping", f"--socket={socket_path}"],
            )
    else:
        host, port = _parse_host_port(connection, 3306)
        status, message = _run_command(
            [
                "mysqladmin",
                *_mysql_defaults_file(),
                *_mysql_auth_args(db_user, db_password),
                "ping",
                "-h",
                host,
                "-P",
                str(port),
            ]
        )
        if status == "unknown":
            status, message = _run_command(["mysqladmin", "ping", "-h", host, "-P", str(port)])

    if status != "up":
        if status != "unknown":
            return status, message
        host, port = _parse_host_port(connection, 3306)
        tcp_status, tcp_message = check_tcp(f"{host}:{port}")
        if tcp_status != "up":
            return tcp_status, tcp_message

    if not database_name:
        return "up", message

    try:
        lines = _run_lines(
            [
                "mysql",
                *_mysql_defaults_file(),
                *_mysql_conn_args_for_check(connection),
                *_mysql_auth_args(db_user, db_password),
                "-N",
                "-B",
                "-e",
                f"SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = '{database_name}'",
            ]
        )
        if any(line == database_name for line in lines):
            return "up", f"database {database_name} accessible"
        return "down", f"database {database_name} not found"
    except RuntimeError as exc:
        return "down", str(exc)


def _mysql_defaults_file() -> list[str]:
    cnf = Path("/etc/mysql/debian.cnf")
    if cnf.exists():
        return [f"--defaults-extra-file={cnf}"]
    return []


def _mysql_conn_args_for_check(connection: str) -> list[str]:
    if connection.startswith("socket:"):
        return [f"--socket={connection[7:]}"]
    host, port = _parse_host_port(connection, 3306)
    return [f"--host={host}", f"--port={port}"]


def _run_lines(cmd: list[str], timeout: float = 10.0) -> list[str]:
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        output = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(output or f"command failed: {cmd[0]}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def check_postgres(
    target: str,
    db_user: str | None = None,
    db_password: str | None = None,
) -> tuple[Status, str]:
    connection, database_name = _split_db_target(target)

    pg_env = None
    auth_args: list[str] = []
    if db_user:
        auth_args.extend(["-U", db_user])
    if db_password:
        pg_env = {**os.environ, "PGPASSWORD": db_password}

    if connection.startswith("socket:"):
        socket_dir = connection[7:].rsplit("/", 1)[0]
        port = _postgres_port_from_socket(connection[7:])
        cmd = ["pg_isready", "-h", socket_dir, "-p", str(port), *auth_args]
    else:
        host, port = _parse_host_port(connection, 5432)
        cmd = ["pg_isready", "-h", host, "-p", str(port), *auth_args]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=pg_env,
        )
        ready = result.returncode == 0
        message = (result.stdout or result.stderr or "").strip()
    except FileNotFoundError:
        return "unknown", "pg_isready not found"

    if not ready:
        if connection.startswith("socket:"):
            return "down", message or "postgres socket not ready"
        host, port = _parse_host_port(connection, 5432)
        return check_tcp(f"{host}:{port}")

    if not database_name:
        return "up", message or "ready"

    verify_cmd = build_postgres_psql_cmd(
        connection,
        ["-t", "-A", "-c", "SELECT 1", *(["-d", database_name] if database_name else [])],
        db_user=db_user,
    )

    try:
        result = subprocess.run(
            verify_cmd,
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            env=pg_env,
        )
        if result.returncode == 0:
            return "up", f"database {database_name} accessible"
        output = (result.stderr or result.stdout or "").strip()
        return "down", output or f"database {database_name} unreachable"
    except FileNotFoundError:
        return "up", message or "ready"


def check_redis(target: str, db_user: str | None = None, db_password: str | None = None) -> tuple[Status, str]:
    connection, database_name = _split_db_target(target)
    auth: list[str] = []
    if db_password:
        auth = ["-a", db_password]

    if connection.startswith("socket:"):
        socket_path = connection[7:]
        base = ["redis-cli", "-s", socket_path, *auth]
    else:
        host, port = _parse_host_port(connection, 6379)
        base = ["redis-cli", "-h", host, "-p", str(port), *auth]

    status, message = _run_command([*base, "ping"])
    if status == "up" and "PONG" not in message.upper():
        status = "down"

    if status != "up":
        if status != "unknown":
            return status, message
        host, port = _parse_host_port(connection, 6379)
        return check_tcp(f"{host}:{port}")

    if database_name is None:
        return status, message

    select_status, select_message = _run_command([*base, "-n", database_name, "ping"])
    if select_status == "up" and "PONG" in select_message.upper():
        return "up", f"redis db {database_name} ok"
    return select_status, select_message


def check_mongodb(
    target: str,
    db_user: str | None = None,
    db_password: str | None = None,
) -> tuple[Status, str]:
    connection, database_name = _split_db_target(target)
    host, port = _parse_host_port(connection, 27017)

    auth_args: list[str] = []
    if db_user:
        auth_args.extend(["-u", db_user])
    if db_password:
        auth_args.extend(["-p", db_password])

    for cmd in (
        ["mongosh", "--quiet", "--eval", "db.runCommand({ ping: 1 }).ok"],
        ["mongo", "--quiet", "--eval", "db.runCommand({ ping: 1 }).ok"],
    ):
        status, message = _run_command(
            [*cmd, "--host", host, "--port", str(port), *auth_args],
            timeout=8.0,
        )
        if status == "up" and message.strip() == "1":
            if not database_name:
                return "up", "ping ok"
            list_status, list_message = _run_command(
                [
                    cmd[0],
                    "--quiet",
                    "--host",
                    host,
                    "--port",
                    str(port),
                    *auth_args,
                    "--eval",
                    f"db.getSiblingDB('{database_name}').getName()",
                ],
                timeout=8.0,
            )
            if list_status == "up" and database_name in list_message:
                return "up", f"database {database_name} accessible"
            return "down", f"database {database_name} not found"
        if status != "unknown":
            continue

    tcp_status, tcp_message = check_tcp(f"{host}:{port}")
    if tcp_status == "up" and not database_name:
        return "up", f"tcp open ({tcp_message})"
    return tcp_status, tcp_message


def run_check(service: ServiceCheck) -> CheckResult:
    started = time.perf_counter()
    check_type = service.check_type.lower()

    if check_type == "systemd":
        unit = service.target or service.name
        status, message = check_systemd(unit)
    elif check_type == "tcp":
        status, message = check_tcp(service.target)
    elif check_type == "http":
        status, message = check_http(service.target)
    elif check_type == "mysql":
        status, message = check_mysql(service.target, service.db_user, service.db_password)
    elif check_type == "postgres":
        status, message = check_postgres(service.target, service.db_user, service.db_password)
    elif check_type == "redis":
        status, message = check_redis(service.target, service.db_user, service.db_password)
    elif check_type == "mongodb":
        status, message = check_mongodb(service.target, service.db_user, service.db_password)
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
