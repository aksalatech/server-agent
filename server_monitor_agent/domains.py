from __future__ import annotations

import glob
import os
import re
from pathlib import Path

SKIP_HOSTS = {
    "_",
    "default",
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "[::]",
    "off",
    "none",
}

DOMAIN_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$",
    re.IGNORECASE,
)


def _normalize_domain(token: str) -> str | None:
    value = token.strip().lower().rstrip(".")
    if not value or value in SKIP_HOSTS:
        return None
    if value.startswith("http://"):
        value = value[7:]
    if value.startswith("https://"):
        value = value[8:]
    if value.startswith("*."):
        value = value[2:]
    if ":" in value:
        value = value.split(":", 1)[0]
    if not DOMAIN_RE.match(value):
        return None
    return value


def _parse_nginx_domains(path: Path) -> set[str]:
    domains: set[str] = set()
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return domains

    for match in re.finditer(r"server_name\s+([^;]+);", text, re.IGNORECASE):
        for part in match.group(1).split():
            domain = _normalize_domain(part)
            if domain:
                domains.add(domain)
    return domains


def _parse_apache_domains(path: Path) -> set[str]:
    domains: set[str] = set()
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return domains

    for match in re.finditer(r"Server(?:Name|Alias)\s+(\S+)", text, re.IGNORECASE):
        domain = _normalize_domain(match.group(1))
        if domain:
            domains.add(domain)
    return domains


def _parse_caddyfile(path: Path) -> set[str]:
    domains: set[str] = set()
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return domains

    for match in re.finditer(r"^([^\s#{]+)\s*\{", text, re.MULTILINE):
        block = match.group(1).strip()
        for part in block.split(","):
            domain = _normalize_domain(part.strip())
            if domain:
                domains.add(domain)
    return domains


def _collect_config_paths() -> list[tuple[str, Path]]:
    paths: list[tuple[str, Path]] = []

    for directory in ("/etc/nginx/sites-enabled", "/etc/nginx/conf.d"):
        if os.path.isdir(directory):
            for entry in sorted(glob.glob(os.path.join(directory, "*"))):
                path = Path(entry)
                if path.is_file() or path.is_symlink():
                    paths.append(("nginx", path))

    for directory in ("/etc/apache2/sites-enabled", "/etc/httpd/conf.d"):
        if os.path.isdir(directory):
            for entry in sorted(glob.glob(os.path.join(directory, "*"))):
                path = Path(entry)
                if path.is_file() or path.is_symlink():
                    paths.append(("apache", path))

    for file_path in ("/etc/caddy/Caddyfile",):
        path = Path(file_path)
        if path.is_file():
            paths.append(("caddy", path))

    return paths


def detect_domains() -> list[dict[str, str]]:
    found: dict[str, dict[str, str]] = {}

    for source, path in _collect_config_paths():
        if source == "nginx":
            raw_domains = _parse_nginx_domains(path)
        elif source == "apache":
            raw_domains = _parse_apache_domains(path)
        else:
            raw_domains = _parse_caddyfile(path)

        for domain in raw_domains:
            if domain in found:
                if source not in found[domain]["source"]:
                    found[domain]["source"] = f"{found[domain]['source']},{source}"
                continue
            found[domain] = {
                "domain": domain,
                "source": source,
                "url": f"https://{domain}",
                "name": domain[:64],
                "type": "http",
                "target": f"https://{domain}",
            }

    return [found[key] for key in sorted(found.keys())]
