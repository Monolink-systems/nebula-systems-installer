# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
"""Source checkout, runtime environment and component build operations."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import MODE_PROD, DeploymentConfig
from .paths import CORE_MARKER, CORE_REPO_URL, PANEL_MARKER, PANEL_REPO_URL
from .prerequisites import CORE_PYTHON
from .runner import CommandError, Runner

MIN_CORE_VERSION = (0, 6, 0)
MIN_PANEL_VERSION = (0, 2, 0)


def _numeric_version(raw: str) -> tuple[int, int, int]:
    clean = raw.strip().lstrip("v").split("-", 1)[0]
    parts = clean.split(".")
    try:
        numbers = [int(part) for part in parts[:3]]
    except ValueError:
        return (0, 0, 0)
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers)  # type: ignore[return-value]


def component_versions(core_dir: Path, panel_dir: Path) -> tuple[str, str]:
    try:
        core = (core_dir / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        core = "unknown"
    try:
        panel = str(
            json.loads((panel_dir / "package.json").read_text(encoding="utf-8"))[
                "version"
            ]
        )
    except (OSError, ValueError, KeyError, TypeError):
        panel = "unknown"
    return core, panel


def validate_versions(core_dir: Path, panel_dir: Path) -> tuple[bool, str]:
    core, panel = component_versions(core_dir, panel_dir)
    if _numeric_version(core) < MIN_CORE_VERSION:
        return False, f"Nebula Core {core} is unsupported; expected 0.6.0 or newer"
    if _numeric_version(panel) < MIN_PANEL_VERSION:
        return False, f"Nebula Panel {panel} is unsupported; expected 0.2.0 or newer"
    return True, f"Compatible source versions: Core {core}, Panel {panel}"


def _as_owner(
    runner: Runner,
    owner: str,
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    current = os.getenv("SUDO_USER") or os.getenv("USER") or ""
    if not runner.is_root and owner == current:
        return runner.run(command, cwd=cwd, env=env, capture=capture, check=check)
    return runner.as_user(
        owner, command, cwd=cwd, env=env, capture=capture, check=check
    )


def clone_or_update(
    repo_url: str,
    target: Path,
    *,
    marker: Path,
    owner: str,
    group: str,
    runner: Runner,
    branch: str = "",
) -> str:
    if (target / marker).exists():
        if not (target / ".git").exists():
            return f"Using existing source tree at {target}"
        dirty = _as_owner(
            runner, owner, ["git", "status", "--porcelain"], cwd=target, capture=True
        ).stdout.strip()
        if dirty:
            return f"Using {target}; local source changes were preserved"
        _as_owner(runner, owner, ["git", "fetch", "--tags", "--prune"], cwd=target)
        if branch:
            _as_owner(runner, owner, ["git", "checkout", branch], cwd=target)
        _as_owner(runner, owner, ["git", "pull", "--ff-only"], cwd=target)
        return f"Updated {target}"

    privileged = target.as_posix().startswith(("/opt/", "/srv/"))
    runner.ensure_directory(
        target, owner=owner, group=group or owner, mode=0o750, privileged=privileged
    )
    command = ["git", "clone", "--depth", "1"]
    if branch:
        command.extend(["--branch", branch])
    command.extend([repo_url, str(target)])
    # git accepts an existing empty directory and leaves its ownership intact.
    _as_owner(runner, owner, command, cwd=target.parent)
    return f"Downloaded {repo_url} to {target}"


def sync_sources(
    config: DeploymentConfig, runner: Runner, *, branch: str = ""
) -> list[str]:
    # Production components have separate primary groups; the Panel must not be
    # able to read Core source, databases, or configuration.
    core_group = config.core_user
    panel_group = config.panel_user
    if config.mode == MODE_PROD:
        for path, owner in (
            (config.core_path, config.core_user),
            (config.panel_path, config.panel_user),
        ):
            if path.exists():
                runner.sudo(["chown", "-R", f"{owner}:{owner}", str(path)])
                runner.sudo(["chmod", "0750", str(path)])
    messages = [
        clone_or_update(
            CORE_REPO_URL,
            config.core_path,
            marker=CORE_MARKER,
            owner=config.core_user,
            group=core_group,
            runner=runner,
            branch=branch,
        ),
        clone_or_update(
            PANEL_REPO_URL,
            config.panel_path,
            marker=PANEL_MARKER,
            owner=config.panel_user,
            group=panel_group,
            runner=runner,
            branch=branch,
        ),
    ]
    if config.mode == MODE_PROD:
        for path, owner in (
            (config.core_path, config.core_user),
            (config.panel_path, config.panel_user),
        ):
            runner.sudo(["chown", "-R", f"{owner}:{owner}", str(path)])
            runner.sudo(["chmod", "0750", str(path)])
    return messages


def prepare_core(config: DeploymentConfig, runner: Runner, uv_binary: str) -> None:
    runtime_dir = config.root_path / "runtimes/python"
    cache_dir = config.root_path / ".cache/uv"
    privileged = config.mode == MODE_PROD
    for directory, mode in ((runtime_dir, 0o755), (cache_dir, 0o750)):
        runner.ensure_directory(
            directory,
            owner=config.core_user,
            group=config.core_user,
            mode=mode,
            privileged=privileged,
        )
    environment = {
        "UV_PYTHON_INSTALL_DIR": str(runtime_dir),
        "UV_CACHE_DIR": str(cache_dir),
        "UV_NO_MODIFY_PATH": "1",
        "HOME": str(config.core_path),
    }
    venv_python = config.core_path / ".venv/bin/python"
    recreate = False
    if venv_python.exists():
        result = _as_owner(
            runner,
            config.core_user,
            [
                str(venv_python),
                "-c",
                "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
            ],
            cwd=config.core_path,
            env=environment,
            capture=True,
        )
        recreate = result.stdout.strip() != CORE_PYTHON
    if not venv_python.exists() or recreate:
        command = [uv_binary, "venv", "--python", CORE_PYTHON]
        if recreate:
            command.append("--clear")
        command.append(".venv")
        _as_owner(
            runner,
            config.core_user,
            command,
            cwd=config.core_path,
            env=environment,
        )
    _as_owner(
        runner,
        config.core_user,
        [
            uv_binary,
            "pip",
            "install",
            "--python",
            str(venv_python),
            "--requirements",
            "requirements.txt",
        ],
        cwd=config.core_path,
        env=environment,
    )


def prepare_panel(config: DeploymentConfig, runner: Runner) -> None:
    cache_dir = config.root_path / ".cache/npm"
    runner.ensure_directory(
        cache_dir,
        owner=config.panel_user,
        group=config.panel_user,
        mode=0o750,
        privileged=config.mode == MODE_PROD,
    )
    environment = {
        "HOME": str(config.panel_path),
        "npm_config_cache": str(cache_dir),
    }
    _as_owner(
        runner,
        config.panel_user,
        ["npm", "ci"],
        cwd=config.panel_path,
        env=environment,
    )
    if config.mode == MODE_PROD:
        _as_owner(
            runner,
            config.panel_user,
            ["npm", "run", "check"],
            cwd=config.panel_path,
            env=environment,
        )
        _as_owner(
            runner,
            config.panel_user,
            ["npm", "run", "build"],
            cwd=config.panel_path,
            env=environment,
        )
        if not (config.panel_path / "build/index.js").exists():
            raise CommandError("Panel build completed without build/index.js")
        _as_owner(
            runner,
            config.panel_user,
            ["npm", "prune", "--omit=dev"],
            cwd=config.panel_path,
            env=environment,
        )


def panel_audit(config: DeploymentConfig, runner: Runner) -> tuple[int, int, int, int]:
    result = _as_owner(
        runner,
        config.panel_user,
        ["npm", "audit", "--omit=dev", "--json"],
        cwd=config.panel_path,
        env={
            "HOME": str(config.panel_path),
            "npm_config_cache": str(config.root_path / ".cache/npm"),
        },
        capture=True,
        check=False,
    )
    try:
        metadata = (
            json.loads(result.stdout).get("metadata", {}).get("vulnerabilities", {})
        )
        return (
            int(metadata.get("low", 0)),
            int(metadata.get("moderate", 0)),
            int(metadata.get("high", 0)),
            int(metadata.get("critical", 0)),
        )
    except (ValueError, TypeError):
        return (0, 0, 0, 0)


def copy_installer(source: Path, config: DeploymentConfig, runner: Runner) -> None:
    selected = [
        "main.py",
        "VERSION",
        "LICENSE",
        "README.md",
        "nebulactl.sh",
        "install.sh",
    ]
    with tempfile.TemporaryDirectory(prefix="nebula-installer-copy-") as directory:
        stage = Path(directory)
        (stage / "modules").mkdir()
        for name in selected:
            candidate = source / name
            if candidate.exists():
                shutil.copy2(candidate, stage / name)
        for candidate in (source / "modules").glob("*.py"):
            shutil.copy2(candidate, stage / "modules" / candidate.name)

        if config.mode == MODE_PROD:
            runner.sudo(["mkdir", "-p", str(config.installer_path)])
            runner.sudo(["cp", "-a", f"{stage}/.", str(config.installer_path)])
            runner.sudo(["chown", "-R", "root:root", str(config.installer_path)])
        else:
            config.installer_path.mkdir(parents=True, exist_ok=True)
            shutil.copytree(stage, config.installer_path, dirs_exist_ok=True)


# Lightweight compatibility helpers used by status and older integrations.
def node_version() -> str:
    binary = shutil.which("node")
    if not binary:
        return ""
    result = subprocess.run(
        [binary, "--version"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def build_panel(panel_dir: Path) -> tuple[bool, str]:
    try:
        subprocess.run(["npm", "ci"], cwd=panel_dir, check=True)
        subprocess.run(["npm", "run", "build"], cwd=panel_dir, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        return False, str(exc)
    return (panel_dir / "build/index.js").exists(), "Panel build completed"
