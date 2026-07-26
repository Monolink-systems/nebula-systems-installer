# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
"""Generation and synchronisation of the per-component `.env` files.

Core and Panel keep separate environment files. A small set of values must agree
across both, most importantly the internal token, so they are written from one
resolved set here rather than maintained by hand.
"""
import secrets
from pathlib import Path

SHARED_KEYS = ("NEBULA_INSTALLER_TOKEN",)


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return values
    return values


def write_env_file(path: Path, values: dict[str, str], title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {title}",
        "# Managed by the Nebula Systems Installer. Hand edits are preserved.",
        "",
    ]
    lines.extend(f'{key}="{values[key]}"' for key in sorted(values))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def core_defaults(core_host: str, core_port: int, panel_origins: str) -> dict[str, str]:
    return {
        "NEBULA_CORE_HOST": core_host,
        "NEBULA_CORE_PORT": str(core_port),
        "NEBULA_CORE_GRPC_HOST": "127.0.0.1",
        "NEBULA_CORE_GRPC_PORT": "50051",
        "NEBULA_SESSION_SECRET": secrets.token_urlsafe(32),
        "NEBULA_INSTALLER_TOKEN": secrets.token_urlsafe(32),
        "NEBULA_PASSWORD_RESET_SECRET": secrets.token_urlsafe(32),
        "NEBULA_COOKIE_SECURE": "false",
        "NEBULA_CORS_ORIGINS": panel_origins,
    }


def panel_defaults(core_url: str, panel_host: str, panel_port: int) -> dict[str, str]:
    return {
        "NEBULA_CORE_URL": core_url,
        "NEBULA_INSTALLER_TOKEN": "",
        "HOST": panel_host,
        "PORT": str(panel_port),
        "NODE_ENV": "production",
    }


def ensure_core_env(
    core_dir: Path,
    *,
    core_host: str,
    core_port: int,
    panel_origins: str,
) -> dict[str, str]:
    """Create or top up Core's `.env`, keeping any value already present."""
    path = core_dir / ".env"
    values = read_env_file(path)
    for key, value in core_defaults(core_host, core_port, panel_origins).items():
        values.setdefault(key, value)
    write_env_file(path, values, "Nebula Core environment")
    return values


def ensure_panel_env(
    panel_dir: Path,
    *,
    core_values: dict[str, str],
    core_url: str,
    panel_host: str,
    panel_port: int,
) -> dict[str, str]:
    """Create or top up the Panel `.env` and re-sync the values shared with Core."""
    path = panel_dir / ".env"
    values = read_env_file(path)
    for key, value in panel_defaults(core_url, panel_host, panel_port).items():
        values.setdefault(key, value)
    for key in SHARED_KEYS:
        if core_values.get(key):
            values[key] = core_values[key]
    values["NEBULA_CORE_URL"] = core_url
    write_env_file(path, values, "Nebula Panel environment")
    return values
