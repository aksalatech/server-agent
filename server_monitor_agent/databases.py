from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from .checker import (
    Status,
    check_mongodb,
    check_mysql,
    check_postgres,
    check_redis,
    check_systemd,
)
from .detect import _enabled_units

ProbeFn = Callable[[str], tuple[Status, str]]

MYSQL_SKIP_DATABASES = {
    "information_schema",
    "mysql",
    "performance_schema",
    "sys",
}

POSTGRES_SKIP_DATABASES = {
    "template0",
    "template1",
}

DATABASE_DEFINITIONS: dict[str, dict[str, str | int]] = {
    "mariadb": {
        "name": "mariadb",
        "engine": "mysql",
        "port": 3306,
        "systemd_unit": "mariadb",
    },
    "mysql": {
        "name": "mysql",
        "engine": "mysql",
        "port": 3306,
        "systemd_unit": "mysql",
    },
    "mysqld": {
        "name": "mysqld",
        "engine": "mysql",
        "port": 3306,
        "systemd_unit": "mysqld",
    },
    "postgresql": {
        "name": "postgresql",
        "engine": "postgres",
        "port": 5432,
        "systemd_unit": "postgresql",
    },
    "redis-server": {
        "name": "redis",
        "engine": "redis",
        "port": 6379,
        "systemd_unit": "redis-server",
    },
    "redis": {
        "name": "redis",
        "engine": "redis",
        "port": 6379,
        "systemd_unit": "redis",
    },
    "mongod": {
        "name": "mongodb",
        "engine": "mongodb",
        "port": 27017,
        "systemd_unit": "mongod",
    },
}

ENGINE_PROBES: dict[str, ProbeFn] = {
    "mysql": check_mysql,
    "postgres": check_postgres,
    "redis": check_redis,
    "mongodb": check_mongodb,
}


def _candidate_targets(engine: str, port: int) -> list[str]:
    if engine == "mysql":
        return [
            "socket:/var/run/mysqld/mysqld.sock",
            "socket:/run/mysqld/mysqld.sock",
            f"127.0.0.1:{port}",
        ]
    if engine == "postgres":
        return [
            f"127.0.0.1:{port}",
            "socket:/var/run/postgresql/.s.PGSQL.5432",
            "socket:/run/postgresql/.s.PGSQL.5432",
        ]
    if engine == "redis":
        return [
            f"127.0.0.1:{port}",
            "socket:/var/run/redis/redis-server.sock",
            "socket:/run/redis/redis-server.sock",
        ]
    if engine == "mongodb":
        return [f"127.0.0.1:{port}"]
    return [f"127.0.0.1:{port}"]


def _probe_engine(engine: str, target: str) -> tuple[Status, str]:
    probe = ENGINE_PROBES.get(engine)
    if not probe:
        return "unknown", f"unsupported engine: {engine}"
    return probe(target)


def _mysql_defaults_args() -> list[str]:
    debian_cnf = Path("/etc/mysql/debian.cnf")
    if debian_cnf.exists():
        return [f"--defaults-extra-file={debian_cnf}"]
    return []


def _mysql_conn_args(connection: str) -> list[str]:
    if connection.startswith("socket:"):
        return [f"--socket={connection[7:]}"]
    host = "127.0.0.1"
    port = 3306
    if ":" in connection:
        host_part, port_part = connection.rsplit(":", 1)
        host = host_part or "127.0.0.1"
        try:
            port = int(port_part)
        except ValueError:
            port = 3306
    return [f"--host={host}", f"--port={port}"]


def _run_lines(cmd: list[str], timeout: float = 15.0) -> list[str]:
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


def _build_target(connection: str, database_name: str) -> str:
    return f"{connection}#{database_name}"


def _list_mysql_databases(connection: str) -> list[str]:
    lines = _run_lines(
        [
            "mysql",
            *_mysql_defaults_args(),
            *_mysql_conn_args(connection),
            "-N",
            "-B",
            "-e",
            "SHOW DATABASES",
        ]
    )
    return [line for line in lines if line.lower() not in MYSQL_SKIP_DATABASES]


def _list_postgres_databases(connection: str) -> list[str]:
    host = "127.0.0.1"
    port = 5432
    if connection.startswith("socket:"):
        host = connection[7:].rsplit("/", 1)[0]
    elif ":" in connection:
        host_part, port_part = connection.rsplit(":", 1)
        host = host_part or "127.0.0.1"
        try:
            port = int(port_part)
        except ValueError:
            port = 5432

    lines = _run_lines(
        [
            "psql",
            "-h",
            host,
            "-p",
            str(port),
            "-t",
            "-A",
            "-c",
            "SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname",
        ]
    )
    return [line for line in lines if line.lower() not in POSTGRES_SKIP_DATABASES]


def _list_mongodb_databases(connection: str) -> list[str]:
    host, port = "127.0.0.1", 27017
    if ":" in connection:
        host_part, port_part = connection.rsplit(":", 1)
        host = host_part or "127.0.0.1"
        try:
            port = int(port_part)
        except ValueError:
            port = 27017

    for binary in ("mongosh", "mongo"):
        try:
            lines = _run_lines(
                [
                    binary,
                    "--quiet",
                    "--host",
                    host,
                    "--port",
                    str(port),
                    "--eval",
                    "db.adminCommand('listDatabases').databases.map(d => d.name).join('\\n')",
                ],
                timeout=20.0,
            )
            names = []
            for line in lines:
                names.extend(part.strip() for part in line.split("\n") if part.strip())
            return [name for name in names if name not in {"admin", "local", "config"}]
        except RuntimeError:
            continue
    return []


def _list_redis_databases(connection: str) -> list[str]:
    if connection.startswith("socket:"):
        cmd = ["redis-cli", "-s", connection[7:], "INFO", "keyspace"]
    else:
        host = "127.0.0.1"
        port = 6379
        if ":" in connection:
            host_part, port_part = connection.rsplit(":", 1)
            host = host_part or "127.0.0.1"
            try:
                port = int(port_part)
            except ValueError:
                port = 6379
        cmd = ["redis-cli", "-h", host, "-p", str(port), "INFO", "keyspace"]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    if result.returncode != 0:
        return ["0"]

    databases: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("db") and ":" in line:
            db_index = line.split(":", 1)[0][2:]
            if db_index.isdigit():
                databases.append(db_index)
    return databases or ["0"]


def _list_databases_for_engine(engine: str, connection: str) -> list[str]:
    if engine == "mysql":
        return _list_mysql_databases(connection)
    if engine == "postgres":
        return _list_postgres_databases(connection)
    if engine == "mongodb":
        return _list_mongodb_databases(connection)
    if engine == "redis":
        return _list_redis_databases(connection)
    return []


def detect_databases() -> list[dict[str, str]]:
    enabled = _enabled_units()
    results: list[dict[str, str]] = []
    seen_keys: set[str] = set()
    seen_instances: set[str] = set()

    for unit in sorted(enabled):
        definition = DATABASE_DEFINITIONS.get(unit)
        if not definition:
            continue

        instance_name = str(definition["name"])
        if instance_name in seen_instances:
            continue

        systemd_unit = str(definition["systemd_unit"])
        service_status, _ = check_systemd(systemd_unit)
        if service_status != "up":
            continue

        engine = str(definition["engine"])
        port = int(definition["port"])
        working_connection = ""

        for target in _candidate_targets(engine, port):
            probe_status, _ = _probe_engine(engine, target)
            if probe_status == "up":
                working_connection = target
                break

        if not working_connection:
            continue

        seen_instances.add(instance_name)

        try:
            database_names = _list_databases_for_engine(engine, working_connection)
        except RuntimeError as exc:
            database_names = []
            if not results:
                results.append(
                    {
                        "name": f"_error_{instance_name}",
                        "engine": engine,
                        "source": instance_name,
                        "type": engine,
                        "connection": working_connection,
                        "target": working_connection,
                        "error": str(exc),
                    }
                )

        for database_name in database_names:
            unique_key = f"{instance_name}:{database_name}"
            if unique_key in seen_keys:
                continue

            service_name = database_name
            if engine == "redis":
                service_name = f"redis-db{database_name}"

            results.append(
                {
                    "name": service_name,
                    "database": database_name,
                    "engine": engine,
                    "source": instance_name,
                    "type": engine,
                    "connection": working_connection,
                    "target": _build_target(working_connection, database_name),
                }
            )
            seen_keys.add(unique_key)

    filtered = [item for item in results if not item.get("name", "").startswith("_error_")]
    return filtered
