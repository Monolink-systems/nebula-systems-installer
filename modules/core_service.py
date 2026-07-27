# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
"""Hardened systemd units and day-to-day service control."""

from __future__ import annotations

import shutil
from pathlib import Path

from .config import MODE_PROD, DeploymentConfig, invoking_home
from .runner import Runner

SYSTEMD_DIR = Path("/etc/systemd/system")


def node_binary() -> str | None:
    return shutil.which("node")


def _quote(value: object) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _primary_group(username: str) -> str:
    try:
        import grp
        import pwd

        return grp.getgrgid(pwd.getpwnam(username).pw_gid).gr_name
    except (ImportError, KeyError):
        return username


def build_core_unit(config: DeploymentConfig) -> str:
    production = config.mode == MODE_PROD
    group = config.core_user if production else _primary_group(config.core_user)
    home = "/var/lib/nebula-core" if production else str(config.core_path)
    supplementary = "SupplementaryGroups=docker\n"
    security = ""
    runtime = ""
    if production:
        runtime = """RuntimeDirectory=nebula-core
RuntimeDirectoryMode=0750
StateDirectory=nebula-core
StateDirectoryMode=0750
"""
        security = f"""ProtectSystem=full
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
NoNewPrivileges=true
RestrictSUIDSGID=true
LockPersonality=true
CapabilityBoundingSet=
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK
ReadWritePaths={config.core_path / "storage"} {config.core_path / "logs"}
"""
    return f"""[Unit]
Description=Nebula Core
Documentation=https://github.com/Monolink-systems/nebula-core
After=network-online.target docker.service
Wants=network-online.target docker.service

[Service]
Type=simple
User={config.core_user}
Group={group}
{supplementary}UMask={"0007" if production else "0002"}
WorkingDirectory={config.core_path}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=ENV={"production" if production else "development"}
Environment="HOME={home}"
Environment="NEBULA_CONFIG_PATH={config.core_path / "serviceconfig.yaml"}"
EnvironmentFile=-{config.core_env_path}
ExecStart={config.core_path / ".venv/bin/python"} -m nebula_core
Restart=on-failure
RestartSec=2
TimeoutStopSec=30
LimitNOFILE=65535
Delegate=yes
{runtime}{security}
[Install]
WantedBy=multi-user.target
"""


def build_panel_unit(config: DeploymentConfig, node: str = "", npm: str = "") -> str:
    production = config.mode == MODE_PROD
    group = config.panel_user if production else _primary_group(config.panel_user)
    home = "/var/lib/nebula-panel" if production else str(config.panel_path)
    if production:
        executable = node or node_binary() or "/usr/local/bin/node"
        exec_start = f"{executable} build/index.js"
        security = f"""StateDirectory=nebula-panel
StateDirectoryMode=0750
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
NoNewPrivileges=true
RestrictSUIDSGID=true
LockPersonality=true
CapabilityBoundingSet=
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
ReadWritePaths={config.panel_path / "logs"}
"""
    else:
        executable = npm or shutil.which("npm") or "/usr/local/bin/npm"
        exec_start = f"{executable} run dev -- --host {config.panel_host} --port {config.panel_port}"
        security = "NoNewPrivileges=true\n"
    return f"""[Unit]
Description=Nebula Panel
Documentation=https://github.com/Monolink-systems/nebula-panel
After=network-online.target {config.core_service}.service
Wants=network-online.target

[Service]
Type=simple
User={config.panel_user}
Group={group}
UMask={"0027" if production else "0002"}
WorkingDirectory={config.panel_path}
Environment=PATH=/usr/local/bin:/usr/bin:/bin
Environment=NODE_ENV={"production" if production else "development"}
Environment="HOME={home}"
EnvironmentFile=-{config.panel_path / ".env"}
ExecStart={exec_start}
Restart=on-failure
RestartSec=2
TimeoutStopSec=30
LimitNOFILE=65535
{security}
[Install]
WantedBy=multi-user.target
"""


def install_services(config: DeploymentConfig, runner: Runner) -> None:
    runner.install_text(
        SYSTEMD_DIR / f"{config.core_service}.service",
        build_core_unit(config),
        mode=0o644,
    )
    runner.install_text(
        SYSTEMD_DIR / f"{config.panel_service}.service",
        build_panel_unit(config),
        mode=0o644,
    )
    runner.sudo(["systemctl", "daemon-reload"])
    runner.sudo(
        [
            "systemctl",
            "enable",
            "--now",
            f"{config.core_service}.service",
            f"{config.panel_service}.service",
        ]
    )


def install_cli(config: DeploymentConfig, runner: Runner) -> Path:
    wrapper = f"""#!/usr/bin/env sh
set -eu
export NEBULA_STATE_FILE={_quote(config.state_path)}
exec {_quote(config.core_path / ".venv/bin/python")} {_quote(config.installer_path / "main.py")} "$@"
"""
    if config.mode == MODE_PROD:
        destination = Path("/usr/local/bin/nebula")
        runner.install_text(destination, wrapper, mode=0o755)
    else:
        destination = invoking_home() / ".local/bin/nebula"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(wrapper, encoding="utf-8")
        destination.chmod(0o755)
    return destination


def service_action(
    service_name: str,
    action: str,
    *,
    runner: Runner | None = None,
    lines: int = 120,
    follow: bool = False,
) -> tuple[bool, str]:
    active_runner = runner or Runner()
    unit = (
        service_name if service_name.endswith(".service") else f"{service_name}.service"
    )
    if action in {"start", "stop", "restart", "enable", "disable"}:
        result = active_runner.sudo(["systemctl", action, unit], check=False)
    elif action == "status":
        result = active_runner.run(
            ["systemctl", "status", unit, "--no-pager", "-n", "30"], check=False
        )
    elif action == "is-active":
        result = active_runner.run(["systemctl", "is-active", unit], check=False)
    elif action == "logs":
        command = ["journalctl", "-u", unit, "-n", str(max(10, lines)), "--no-pager"]
        if follow:
            command.append("-f")
        result = active_runner.sudo(command, check=False, capture=not follow)
    else:
        return False, f"Unsupported service action: {action}"
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    return result.returncode == 0, output
