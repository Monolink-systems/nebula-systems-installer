# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
"""Production DNS checks and Caddy automatic HTTPS configuration."""

from __future__ import annotations

import ipaddress
import socket
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import DeploymentConfig
from .runner import CommandError, Runner

CADDY_MAIN = Path("/etc/caddy/Caddyfile")
CADDY_INCLUDE_DIR = Path("/etc/caddy/conf.d")
CADDY_SNIPPET = CADDY_INCLUDE_DIR / "nebula.caddy"
CADDY_IMPORT = "import /etc/caddy/conf.d/*.caddy"


def resolve_domain(domain: str) -> set[str]:
    addresses: set[str] = set()
    try:
        for result in socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM):
            addresses.add(str(result[4][0]))
    except OSError:
        pass
    return addresses


def public_ip() -> str:
    for url in ("https://api64.ipify.org", "https://api.ipify.org"):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "Nebula-Installer/2"}
            )
            with urllib.request.urlopen(request, timeout=6) as response:
                value = response.read(80).decode("ascii", errors="ignore").strip()
                ipaddress.ip_address(value)
                return value
        except (OSError, ValueError):
            continue
    return ""


def dns_status(config: DeploymentConfig) -> tuple[bool, list[str]]:
    current_ip = public_ip()
    messages: list[str] = []
    ready = True
    for label, domain in (("Panel", config.panel_domain), ("Core", config.core_domain)):
        addresses = resolve_domain(domain)
        if not addresses:
            ready = False
            messages.append(f"{label}: {domain} has no A/AAAA record")
        elif current_ip and current_ip not in addresses:
            ready = False
            messages.append(
                f"{label}: {domain} resolves to {', '.join(sorted(addresses))}; "
                f"this server reports {current_ip}"
            )
        else:
            messages.append(f"{label}: {domain} -> {', '.join(sorted(addresses))}")
    if current_ip:
        messages.append(f"Public server IP: {current_ip}")
    return ready, messages


def render_caddy(config: DeploymentConfig) -> str:
    headers = """\theader {
\t\tStrict-Transport-Security "max-age=31536000"
\t\tX-Content-Type-Options "nosniff"
\t\tX-Frame-Options "DENY"
\t\tReferrer-Policy "same-origin"
\t\t-Server
\t}"""
    return f"""# Managed by Nebula Installer.
{config.panel_domain} {{
\tencode zstd gzip
\trequest_body {{
\t\tmax_size 1100MB
\t}}
\treverse_proxy 127.0.0.1:{config.panel_port}
{headers}
}}

{config.core_domain} {{
\tencode zstd gzip
\trequest_body {{
\t\tmax_size 1100MB
\t}}
\treverse_proxy 127.0.0.1:{config.core_port}
{headers}
}}
"""


def install_caddy_config(config: DeploymentConfig, runner: Runner) -> None:
    if not config.panel_domain or not config.core_domain:
        raise CommandError("Both Panel and Core domains are required in production")
    if config.panel_domain == config.core_domain:
        raise CommandError("Panel and Core must use different domains")

    original = ""
    try:
        original = CADDY_MAIN.read_text(encoding="utf-8")
    except OSError:
        pass
    if CADDY_IMPORT not in original:
        updated = (
            original.rstrip() + "\n\n# Nebula managed sites\n" + CADDY_IMPORT + "\n"
        )
        runner.install_text(
            CADDY_MAIN, updated, mode=0o644, owner="root", group="caddy"
        )
    runner.install_text(
        CADDY_SNIPPET, render_caddy(config), mode=0o640, owner="root", group="caddy"
    )

    result = runner.sudo(
        ["caddy", "validate", "--config", str(CADDY_MAIN), "--adapter", "caddyfile"],
        check=False,
    )
    if result.returncode != 0:
        if original:
            runner.install_text(
                CADDY_MAIN, original, mode=0o644, owner="root", group="caddy"
            )
        runner.sudo(["unlink", str(CADDY_SNIPPET)], check=False)
        detail = ((result.stdout or "") + (result.stderr or "")).strip()
        raise CommandError(f"Caddy rejected the generated configuration:\n{detail}")
    runner.sudo(["systemctl", "enable", "--now", "caddy"])
    runner.sudo(["systemctl", "reload", "caddy"])


def wait_for_https(domain: str, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    context = ssl.create_default_context()
    while time.time() < deadline:
        try:
            request = urllib.request.Request(
                f"https://{domain}/login",
                headers={"User-Agent": "Nebula-Installer/2"},
            )
            with urllib.request.urlopen(
                request, timeout=5, context=context
            ) as response:
                if 200 <= response.status < 500:
                    return True
        except (OSError, urllib.error.URLError):
            time.sleep(2)
    return False
