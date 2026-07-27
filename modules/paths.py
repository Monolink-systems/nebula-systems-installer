# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
"""Repository URLs and paths owned by the installer."""

from pathlib import Path

CORE_REPO_URL = "https://github.com/Monolink-systems/nebula-core.git"
PANEL_REPO_URL = "https://github.com/Monolink-systems/nebula-panel.git"

CORE_MARKER = Path("nebula_core") / "main.py"
PANEL_MARKER = Path("package.json")


def installer_dir() -> Path:
    return Path(__file__).resolve().parents[1]
