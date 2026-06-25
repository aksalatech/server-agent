from __future__ import annotations

import gzip
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from .checker import _postgres_psql_host_port, _postgres_run_as_db_user

BACKUP_WORK_DIR = Path("/var/lib/server-monitor-agent/backups")


def _parse_host_port(target: str, default_port: int) -> tuple[str, int]:
    if target.startswith("socket:"):
        return target, default_port

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


def _mysql_args(target: str) -> list[str]:
    if target.startswith("socket:"):
        return [f"--socket={target[7:]}"]
    host, port = _parse_host_port(target, 3306)
    return [f"--host={host}", f"--port={port}"]


def _mysql_defaults_file() -> list[str]:
    debian_cnf = Path("/etc/mysql/debian.cnf")
    if debian_cnf.exists():
        return [f"--defaults-extra-file={debian_cnf}"]
    return []


def _redis_args(target: str) -> list[str]:
    if target.startswith("socket:"):
        return ["-s", target[7:]]
    host, port = _parse_host_port(target, 6379)
    return ["-h", host, "-p", str(port)]


def _run_command(cmd: list[str], timeout: float = 600.0) -> None:
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        output = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(output or f"command failed: {' '.join(cmd)}")


def _gzip_file(source: Path, destination: Path) -> None:
    with source.open("rb") as src, gzip.open(destination, "wb") as dst:
        shutil.copyfileobj(src, dst)


def _resolve_db_target(target: str, database_name: str | None) -> tuple[str, str | None]:
    if "#" in target:
        connection, db_name = target.rsplit("#", 1)
        return connection, database_name or db_name or None
    return target, database_name


def backup_database(
    engine: str,
    target: str,
    database_name: str | None,
    output_path: Path,
) -> None:
    connection, resolved_db = _resolve_db_target(target, database_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    engine_key = engine.lower()

    if engine_key == "mysql":
        _backup_mysql(connection, resolved_db, output_path)
        return
    if engine_key == "postgres":
        _backup_postgres(connection, resolved_db, output_path)
        return
    if engine_key == "redis":
        _backup_redis(connection, output_path)
        return
    if engine_key == "mongodb":
        _backup_mongodb(connection, resolved_db, output_path)
        return

    raise RuntimeError(f"engine tidak didukung untuk backup: {engine}")


def restore_database(
    engine: str,
    target: str,
    database_name: str | None,
    input_path: Path,
) -> None:
    connection, resolved_db = _resolve_db_target(target, database_name)
    engine_key = engine.lower()

    if engine_key == "mysql":
        _restore_mysql(connection, resolved_db, input_path)
        return
    if engine_key == "postgres":
        _restore_postgres(connection, resolved_db, input_path)
        return
    if engine_key == "redis":
        _restore_redis(connection, input_path)
        return
    if engine_key == "mongodb":
        _restore_mongodb(connection, resolved_db, input_path)
        return

    raise RuntimeError(f"engine tidak didukung untuk restore: {engine}")


def _backup_mysql(target: str, database_name: str | None, output_path: Path) -> None:
    with tempfile.TemporaryDirectory(dir=BACKUP_WORK_DIR) as tmp:
        raw_path = Path(tmp) / "dump.sql"
        cmd = [
            "mysqldump",
            *_mysql_defaults_file(),
            *_mysql_args(target),
            "--single-transaction",
            "--routines",
            "--triggers",
        ]
        if database_name:
            cmd.append(database_name)
        else:
            cmd.append("--all-databases")

        with raw_path.open("wb") as handle:
            result = subprocess.run(cmd, stdout=handle, stderr=subprocess.PIPE, check=False)
            if result.returncode != 0:
                raise RuntimeError((result.stderr or b"").decode().strip() or "mysqldump gagal")

        _gzip_file(raw_path, output_path)


def _restore_mysql(target: str, database_name: str | None, input_path: Path) -> None:
    if not database_name:
        raise RuntimeError("restore MySQL memerlukan nama database")

    with tempfile.TemporaryDirectory(dir=BACKUP_WORK_DIR) as tmp:
        raw_path = Path(tmp) / "dump.sql"
        with gzip.open(input_path, "rb") as src, raw_path.open("wb") as dst:
            shutil.copyfileobj(src, dst)

        cmd = ["mysql", *_mysql_defaults_file(), *_mysql_args(target), database_name]
        with raw_path.open("rb") as handle:
            result = subprocess.run(cmd, stdin=handle, stderr=subprocess.PIPE, check=False)
            if result.returncode != 0:
                raise RuntimeError((result.stderr or b"").decode().strip() or "mysql restore gagal")


def _backup_postgres(target: str, database_name: str | None, output_path: Path) -> None:
    with tempfile.TemporaryDirectory(dir=BACKUP_WORK_DIR) as tmp:
        raw_path = Path(tmp) / "dump.sql"
        host, port = _postgres_psql_host_port(target)
        cmd = _postgres_run_as_db_user(["pg_dump", "-h", host, "-p", str(port), "-Fp"])
        if database_name:
            cmd.append(database_name)
        else:
            cmd.append("--all")

        with raw_path.open("wb") as handle:
            result = subprocess.run(cmd, stdout=handle, stderr=subprocess.PIPE, check=False)
            if result.returncode != 0:
                raise RuntimeError((result.stderr or b"").decode().strip() or "pg_dump gagal")

        _gzip_file(raw_path, output_path)


def _restore_postgres(target: str, database_name: str | None, input_path: Path) -> None:
    with tempfile.TemporaryDirectory(dir=BACKUP_WORK_DIR) as tmp:
        raw_path = Path(tmp) / "dump.sql"
        with gzip.open(input_path, "rb") as src, raw_path.open("wb") as dst:
            shutil.copyfileobj(src, dst)

        host, port = _postgres_psql_host_port(target)
        cmd = _postgres_run_as_db_user(
            ["psql", "-h", host, "-p", str(port), "-v", "ON_ERROR_STOP=1"]
        )
        if database_name:
            cmd.extend(["-d", database_name])

        with raw_path.open("rb") as handle:
            result = subprocess.run(cmd, stdin=handle, stderr=subprocess.PIPE, check=False)
            if result.returncode != 0:
                raise RuntimeError((result.stderr or b"").decode().strip() or "psql restore gagal")


def _backup_redis(target: str, output_path: Path) -> None:
    with tempfile.TemporaryDirectory(dir=BACKUP_WORK_DIR) as tmp:
        raw_path = Path(tmp) / "dump.rdb"
        cmd = ["redis-cli", *_redis_args(target), "--rdb", str(raw_path)]
        _run_command(cmd)
        _gzip_file(raw_path, output_path)


def _restore_redis(target: str, input_path: Path) -> None:
    with tempfile.TemporaryDirectory(dir=BACKUP_WORK_DIR) as tmp:
        raw_path = Path(tmp) / "dump.rdb"
        with gzip.open(input_path, "rb") as src, raw_path.open("wb") as dst:
            shutil.copyfileobj(src, dst)

        ping_cmd = ["redis-cli", *_redis_args(target), "ping"]
        _run_command(ping_cmd)

        raise RuntimeError(
            "restore Redis memerlukan restart service dan penggantian dump.rdb manual di host"
        )


def _backup_mongodb(target: str, database_name: str | None, output_path: Path) -> None:
    host, port = _parse_host_port(target, 27017)
    with tempfile.TemporaryDirectory(dir=BACKUP_WORK_DIR) as tmp:
        dump_dir = Path(tmp) / "dump"
        cmd = ["mongodump", "--host", f"{host}:{port}", "--out", str(dump_dir)]
        if database_name:
            cmd.extend(["--db", database_name])
        _run_command(cmd, timeout=900.0)

        with tarfile.open(output_path, "w:gz") as archive:
            archive.add(dump_dir, arcname="dump")


def _restore_mongodb(target: str, database_name: str | None, input_path: Path) -> None:
    host, port = _parse_host_port(target, 27017)
    with tempfile.TemporaryDirectory(dir=BACKUP_WORK_DIR) as tmp:
        extract_dir = Path(tmp) / "restore"
        extract_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(input_path, "r:gz") as archive:
            archive.extractall(extract_dir)

        dump_root = extract_dir / "dump"
        if not dump_root.exists():
            for child in extract_dir.iterdir():
                if child.is_dir():
                    dump_root = child
                    break

        cmd = ["mongorestore", "--host", f"{host}:{port}", "--drop", str(dump_root)]
        if database_name:
            cmd.extend(["--db", database_name, str(dump_root / database_name)])
        _run_command(cmd, timeout=900.0)
