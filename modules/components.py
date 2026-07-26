# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
"""Provisioning of the Core and Panel checkouts: sources, dependencies, builds."""
import shutil
import subprocess
import venv
from pathlib import Path

from .paths import CORE_REPO_URL, PANEL_REPO_URL, core_venv_python


def _run(cmd: list[str], cwd: Path) -> tuple[bool, str]:
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    except OSError as exc:
        return False, str(exc)
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def git_available() -> bool:
    return shutil.which("git") is not None


def npm_binary() -> str | None:
    return shutil.which("npm")


def clone_component(repo_url: str, target: Path, branch: str = "") -> tuple[bool, str]:
    if target.exists() and any(target.iterdir()):
        return False, f"{target} already exists and is not empty"
    if not git_available():
        return False, "git was not found on PATH"

    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [repo_url, str(target)]
    ok, output = _run(cmd, target.parent)
    return ok, output or f"Cloned {repo_url} into {target}"


def clone_core(target: Path, branch: str = "") -> tuple[bool, str]:
    return clone_component(CORE_REPO_URL, target, branch)


def clone_panel(target: Path, branch: str = "") -> tuple[bool, str]:
    return clone_component(PANEL_REPO_URL, target, branch)


def ensure_core_virtualenv(core_dir: Path) -> tuple[bool, str]:
    python_bin = core_venv_python(core_dir)
    if python_bin.exists():
        return True, f"Virtualenv already present at {core_dir / '.venv'}"
    try:
        venv.create(core_dir / ".venv", with_pip=True)
    except Exception as exc:
        return False, f"Failed to create virtualenv: {exc}"
    return True, f"Created virtualenv at {core_dir / '.venv'}"


def install_core_dependencies(core_dir: Path) -> tuple[bool, str]:
    python_bin = core_venv_python(core_dir)
    if not python_bin.exists():
        return False, f"Virtualenv not found: {python_bin}"

    requirements = core_dir / "requirements.txt"
    if not requirements.exists():
        return False, f"requirements.txt not found in {core_dir}"

    ok, output = _run([str(python_bin), "-m", "pip", "install", "--upgrade", "pip"], core_dir)
    if not ok:
        return False, output

    ok, output = _run(
        [str(python_bin), "-m", "pip", "install", "-r", str(requirements)], core_dir
    )
    return ok, output or "Core dependencies installed"


def build_panel(panel_dir: Path) -> tuple[bool, str]:
    npm = npm_binary()
    if not npm:
        return False, "npm was not found on PATH — install Node.js 20 or newer"

    install_cmd = ["npm", "ci"] if (panel_dir / "package-lock.json").exists() else ["npm", "install"]
    ok, output = _run(install_cmd, panel_dir)
    if not ok:
        return False, output

    ok, output = _run(["npm", "run", "build"], panel_dir)
    if not ok:
        return False, output
    if not (panel_dir / "build" / "index.js").exists():
        return False, "Build finished but build/index.js is missing — check the SvelteKit adapter"
    return True, "Panel build completed"


def node_version() -> str:
    node = shutil.which("node")
    if not node:
        return ""
    try:
        result = subprocess.run([node, "--version"], capture_output=True, text=True)
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""
