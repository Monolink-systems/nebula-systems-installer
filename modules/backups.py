# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
"""Consistent SQLite and workspace backups for production deployments."""

from __future__ import annotations

import shutil
import sqlite3
import tarfile
import time
from pathlib import Path

from .config import MODE_PROD, DeploymentConfig
from .runner import Runner

BACKUP_ROOT = Path("/var/backups/nebula")


def _sqlite_backup(source: Path, destination: Path) -> None:
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


def create_backup(
    config: DeploymentConfig,
    *,
    destination_root: Path | None = None,
    include_workspaces: bool = True,
) -> Path:
    root = destination_root or (
        BACKUP_ROOT if config.mode == MODE_PROD else config.root_path / "backups"
    )
    stamp = time.strftime("%Y%m%d-%H%M%S")
    destination = root / stamp
    destination.mkdir(parents=True, exist_ok=False)
    destination.chmod(0o700)

    database_root = config.core_path / "storage/databases"
    database_destination = destination / "databases"
    database_destination.mkdir(mode=0o700)
    databases = [database_root / "system.db", *(database_root / "clients").glob("*.db")]
    for database in databases:
        if database.exists():
            _sqlite_backup(database, database_destination / database.name)

    for source, name in (
        (config.core_env_path, "core.env"),
        (config.panel_path / ".env", "panel.env"),
        (config.core_path / "serviceconfig.yaml", "serviceconfig.yaml"),
        (config.state_path, "installer.json"),
    ):
        if source.exists():
            target = destination / name
            shutil.copy2(source, target)
            target.chmod(0o600)

    workspaces = config.core_path / "storage/container_workspaces"
    if include_workspaces and workspaces.exists():
        with tarfile.open(destination / "workspaces.tar.gz", "w:gz") as archive:
            archive.add(workspaces, arcname="container_workspaces", recursive=True)
    return destination


def install_backup_timer(config: DeploymentConfig, runner: Runner) -> None:
    if config.mode != MODE_PROD:
        return
    runner.ensure_directory(
        BACKUP_ROOT, owner="root", group="root", mode=0o700, privileged=True
    )
    service = """[Unit]
Description=Nebula consistent daily backup
After=nebula-core.service

[Service]
Type=oneshot
User=root
ExecStart=/usr/local/bin/nebula backup --scheduled
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
NoNewPrivileges=true
PrivateTmp=true
"""
    timer = """[Unit]
Description=Run Nebula backup every day

[Timer]
OnCalendar=daily
Persistent=true
RandomizedDelaySec=30m

[Install]
WantedBy=timers.target
"""
    runner.install_text(Path("/etc/systemd/system/nebula-backup.service"), service)
    runner.install_text(Path("/etc/systemd/system/nebula-backup.timer"), timer)
    runner.sudo(["systemctl", "daemon-reload"])
    runner.sudo(["systemctl", "enable", "--now", "nebula-backup.timer"])
