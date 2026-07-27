# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
"""Bootstrap the base schema missing from a pristine Nebula Core checkout."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

from .config import MODE_PROD, DeploymentConfig
from .runner import Runner

BASE_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash BLOB NOT NULL,
    email TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    is_staff INTEGER NOT NULL DEFAULT 0,
    two_factor_secret TEXT,
    two_factor_enabled INTEGER NOT NULL DEFAULT 0,
    password_set_required INTEGER NOT NULL DEFAULT 0,
    display_name TEXT,
    created_at TEXT,
    deactivate_at TEXT
);
CREATE TABLE IF NOT EXISTS roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS user_roles (
    user_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    PRIMARY KEY (user_id, role_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS container_permissions (
    container_id TEXT NOT NULL,
    username TEXT NOT NULL,
    db_name TEXT DEFAULT 'system.db',
    role_tag TEXT DEFAULT 'user',
    PRIMARY KEY (container_id, username)
);
CREATE TABLE IF NOT EXISTS container_role_permissions (
    container_id TEXT NOT NULL,
    role_tag TEXT NOT NULL,
    allow_explorer BOOLEAN DEFAULT 1,
    allow_root_explorer BOOLEAN DEFAULT 0,
    allow_console BOOLEAN DEFAULT 1,
    allow_shell BOOLEAN DEFAULT 0,
    allow_settings BOOLEAN DEFAULT 0,
    allow_edit_files BOOLEAN DEFAULT 0,
    allow_edit_startup BOOLEAN DEFAULT 0,
    allow_edit_ports BOOLEAN DEFAULT 0,
    updated_by TEXT,
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (container_id, role_tag)
);
CREATE TABLE IF NOT EXISTS container_storage (
    container_id TEXT PRIMARY KEY,
    workspace_path TEXT,
    workspace_mount TEXT,
    disk_quota_mb INTEGER,
    managed_workspace BOOLEAN DEFAULT 0,
    updated_by TEXT,
    updated_at TEXT DEFAULT (datetime('now')),
    explorer_root TEXT,
    console_cwd TEXT,
    profile_name TEXT
);
"""

USER_COLUMNS = {
    "email": "TEXT",
    "is_active": "INTEGER NOT NULL DEFAULT 1",
    "is_staff": "INTEGER NOT NULL DEFAULT 0",
    "two_factor_secret": "TEXT",
    "two_factor_enabled": "INTEGER NOT NULL DEFAULT 0",
    "password_set_required": "INTEGER NOT NULL DEFAULT 0",
    "display_name": "TEXT",
    "created_at": "TEXT",
    "deactivate_at": "TEXT",
}

REQUIRED_TABLE_COLUMNS = {
    "users": {"username", "password_hash", "is_staff"},
    "container_permissions": {"container_id", "username", "db_name", "role_tag"},
    "container_role_permissions": {
        "container_id",
        "role_tag",
        "allow_explorer",
        "allow_console",
        "allow_settings",
    },
    "container_storage": {
        "container_id",
        "workspace_path",
        "managed_workspace",
        "explorer_root",
    },
}


def _initialize(database: Path) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    try:
        connection.executescript(BASE_SCHEMA)
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(users)").fetchall()
        }
        for name, definition in USER_COLUMNS.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")
        connection.commit()
    finally:
        connection.close()
    database.chmod(0o600)


def schema_status(database: Path) -> tuple[bool | None, str]:
    """Return schema health, or None when a protected database is unreadable."""
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        if database.exists():
            return None, f"protected or unreadable ({exc})"
        if database.parent.exists() and os.access(
            database.parent, os.R_OK | os.X_OK
        ):
            return False, "system database is missing"
        return None, f"protected or unreadable ({exc})"

    issues: list[str] = []
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        for table, required_columns in REQUIRED_TABLE_COLUMNS.items():
            if table not in tables:
                issues.append(f"missing table {table}")
                continue
            columns = {
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            missing = sorted(required_columns - columns)
            if missing:
                issues.append(f"{table} missing columns: {', '.join(missing)}")
    except sqlite3.DatabaseError as exc:
        return False, f"database error: {exc}"
    finally:
        connection.close()

    if issues:
        return False, "; ".join(issues)
    return True, "required Core tables are present"


def ensure_system_database(config: DeploymentConfig, runner: Runner) -> Path:
    database = config.core_path / "storage/databases/system.db"
    if config.mode != MODE_PROD:
        _initialize(database)
        return database

    # The service database is deliberately unreadable to the invoking operator.
    # Run the same small bootstrap as the Core service account.
    helper = f"""import sqlite3
from pathlib import Path
db = Path({str(database)!r})
db.parent.mkdir(parents=True, exist_ok=True)
conn = sqlite3.connect(db)
conn.executescript({BASE_SCHEMA!r})
columns = {{str(row[1]) for row in conn.execute("PRAGMA table_info(users)").fetchall()}}
definitions = {USER_COLUMNS!r}
for name, definition in definitions.items():
    if name not in columns:
        conn.execute(f"ALTER TABLE users ADD COLUMN {{name}} {{definition}}")
conn.commit()
conn.close()
db.chmod(0o600)
"""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(helper)
        helper_path = Path(handle.name)
    helper_path.chmod(0o644)
    try:
        runner.as_user(
            config.core_user,
            [str(config.core_path / ".venv/bin/python"), str(helper_path)],
            cwd=config.core_path,
            capture=False,
        )
    finally:
        helper_path.unlink(missing_ok=True)
    return database
