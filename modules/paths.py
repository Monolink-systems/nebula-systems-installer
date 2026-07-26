# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
"""Discovery of the Nebula components this installer manages.

Core and Panel are independent repositories, so their locations are resolved at
runtime instead of being derived from the installer's own path.
"""
import os
from pathlib import Path
from typing import Optional

CORE_DIR_ENV = "NEBULA_CORE_DIR"
PANEL_DIR_ENV = "NEBULA_PANEL_DIR"
ROOT_DIR_ENV = "NEBULA_ROOT_DIR"

DEFAULT_ROOT = Path("/opt/nebula")

CORE_REPO_URL = "https://github.com/elmWilh/Nebula-Core.git"
PANEL_REPO_URL = "https://github.com/elmWilh/Nebula-Panel.git"

CORE_MARKER = Path("nebula_core") / "main.py"
PANEL_MARKER = Path("package.json")


def installer_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def deployment_root() -> Path:
    configured = (os.getenv(ROOT_DIR_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_ROOT


def _candidates(names: list[str]) -> list[Path]:
    bases = [installer_dir().parent, deployment_root(), Path.home()]
    return [base / name for base in bases for name in names]


def _first_valid(candidates: list[Path], marker: Path) -> Optional[Path]:
    for candidate in candidates:
        if (candidate / marker).exists():
            return candidate.resolve()
    return None


def core_dir(explicit: Optional[str] = None) -> Optional[Path]:
    if explicit:
        return Path(explicit).expanduser().resolve()
    configured = (os.getenv(CORE_DIR_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return _first_valid(_candidates(["Nebula-Core", "core", "nebula-core"]), CORE_MARKER)


def panel_dir(explicit: Optional[str] = None) -> Optional[Path]:
    if explicit:
        return Path(explicit).expanduser().resolve()
    configured = (os.getenv(PANEL_DIR_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return _first_valid(_candidates(["Nebula-Panel", "panel", "nebula-panel"]), PANEL_MARKER)


def is_core_dir(path: Optional[Path]) -> bool:
    return bool(path and (path / CORE_MARKER).exists())


def is_panel_dir(path: Optional[Path]) -> bool:
    return bool(path and (path / PANEL_MARKER).exists())


def core_venv_python(base: Path) -> Path:
    return base / ".venv" / "bin" / "python"


def core_env_file(base: Path) -> Path:
    return base / ".env"


def panel_env_file(base: Path) -> Path:
    return base / ".env"


def core_database(base: Path) -> Path:
    return base / "storage" / "databases" / "system.db"
