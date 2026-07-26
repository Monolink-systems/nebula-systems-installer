# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
"""systemd unit generation and service control for Nebula components."""
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .paths import core_venv_python

try:
    import grp
    import pwd
except ImportError:
    # POSIX-only. Absent on Windows, where nothing below can run anyway; the
    # module still imports so `--help` and `--version` work everywhere.
    grp = None
    pwd = None

DEFAULT_CORE_SERVICE = "nebula-core"
DEFAULT_PANEL_SERVICE = "nebula-panel"

SYSTEMD_DIR = Path("/etc/systemd/system")


def _run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def systemd_available() -> bool:
    return shutil.which("systemctl") is not None


def node_binary() -> Optional[str]:
    return shutil.which("node")


def detect_run_user() -> str:
    return os.getenv("SUDO_USER") or os.getenv("USER") or "root"


def user_exists(username: str) -> bool:
    if pwd is None:
        return False
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False


def default_core_run_user() -> str:
    configured = (
        os.getenv("NEBULA_CORE_SERVICE_USER") or os.getenv("NEBULA_CORE_RUN_USER") or ""
    ).strip()
    if configured:
        return configured
    if user_exists("nebulapanel"):
        return "nebulapanel"
    return detect_run_user()


def default_panel_run_user() -> str:
    configured = (os.getenv("NEBULA_PANEL_SERVICE_USER") or "").strip()
    if configured:
        return configured
    return detect_run_user()


def primary_group_for_user(username: str) -> str:
    if pwd is None or grp is None:
        return username
    try:
        user_info = pwd.getpwnam(username)
        return grp.getgrgid(user_info.pw_gid).gr_name
    except KeyError:
        return username


def _user_group_names(username: str) -> set[str]:
    if pwd is None or grp is None:
        return set()
    try:
        user_info = pwd.getpwnam(username)
    except KeyError:
        return set()

    names: set[str] = set()
    try:
        names.add(grp.getgrgid(user_info.pw_gid).gr_name)
    except KeyError:
        pass
    for group_info in grp.getgrall():
        if username in group_info.gr_mem:
            names.add(group_info.gr_name)
    return names


def _supplementary_groups_for(username: str) -> list[str]:
    primary_group = primary_group_for_user(username)
    groups = _user_group_names(username)
    preferred = ["docker", "nebulapanel"]
    ordered = [name for name in preferred if name in groups and name != primary_group]
    ordered.extend(
        name for name in sorted(groups) if name != primary_group and name not in ordered
    )
    return ordered


def _build_unit(
    *,
    description: str,
    working_directory: Path,
    exec_start: str,
    env_path: Path,
    log_dir: Path,
    run_user: str,
    run_group: str,
    supplementary_groups: Optional[list[str]],
    extra_environment: Optional[list[str]] = None,
) -> str:
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / "stdout.log"
    stderr_path = log_dir / "stderr.log"

    lines = [f"Environment={item}" for item in (extra_environment or [])]
    if supplementary_groups:
        lines.insert(0, f"SupplementaryGroups={' '.join(supplementary_groups)}")
    extra_block = ("\n".join(lines) + "\n") if lines else ""

    return f"""[Unit]
Description={description}
After=network.target
Wants=network-online.target

[Service]
Type=simple
User={run_user}
Group={run_group}
{extra_block}UMask=0002
WorkingDirectory={working_directory}
EnvironmentFile=-{env_path}
ExecStart={exec_start}
Restart=on-failure
RestartSec=2
Delegate=yes
NoNewPrivileges=yes
TimeoutStopSec=15
LimitNOFILE=65535
StandardOutput=append:{stdout_path}
StandardError=append:{stderr_path}

[Install]
WantedBy=multi-user.target
"""


def build_core_unit(
    core_dir: Path,
    run_user: str,
    service_name: str = DEFAULT_CORE_SERVICE,
    env_mode: str = "production",
) -> str:
    return _build_unit(
        description=f"Nebula Core ({service_name})",
        working_directory=core_dir,
        exec_start=f"{core_venv_python(core_dir)} -m nebula_core",
        env_path=core_dir / ".env",
        log_dir=core_dir / "storage" / "logs",
        run_user=run_user,
        run_group=primary_group_for_user(run_user),
        supplementary_groups=_supplementary_groups_for(run_user),
        extra_environment=[
            "PYTHONUNBUFFERED=1",
            "PYTHONDONTWRITEBYTECODE=1",
            f"ENV={env_mode}",
            f"NEBULA_CONFIG_PATH={core_dir / 'nebula_core' / 'serviceconfig.yaml'}",
        ],
    )


def build_panel_unit(
    panel_dir: Path,
    run_user: str,
    service_name: str = DEFAULT_PANEL_SERVICE,
    env_mode: str = "production",
    node_bin: Optional[str] = None,
) -> str:
    node_path = node_bin or node_binary() or "/usr/bin/node"
    return _build_unit(
        description=f"Nebula Panel ({service_name})",
        working_directory=panel_dir,
        exec_start=f"{node_path} build/index.js",
        env_path=panel_dir / ".env",
        log_dir=panel_dir / "logs",
        run_user=run_user,
        run_group=primary_group_for_user(run_user),
        supplementary_groups=None,
        extra_environment=[f"NODE_ENV={env_mode}"],
    )


def _write_unit(service_name: str, content: str) -> tuple[bool, str]:
    tmp_unit = Path("/tmp") / f"{service_name}.service"
    target_unit = SYSTEMD_DIR / f"{service_name}.service"
    tmp_unit.write_text(content, encoding="utf-8")

    for cmd in (
        ["sudo", "cp", str(tmp_unit), str(target_unit)],
        ["sudo", "systemctl", "daemon-reload"],
        ["sudo", "systemctl", "enable", service_name],
    ):
        result = _run(cmd)
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or "command failed").strip()

    return True, f"Installed/updated {service_name} at {target_unit}"


def install_core_service(
    core_dir: Path,
    run_user: Optional[str] = None,
    service_name: str = DEFAULT_CORE_SERVICE,
    env_mode: str = "production",
) -> tuple[bool, str]:
    if not systemd_available():
        return False, "systemctl is not available on this host"

    python_bin = core_venv_python(core_dir)
    if not python_bin.exists():
        return False, f"Python virtualenv not found: {python_bin}"

    user = (run_user or default_core_run_user()).strip() or "root"
    return _write_unit(service_name, build_core_unit(core_dir, user, service_name, env_mode))


def install_panel_service(
    panel_dir: Path,
    run_user: Optional[str] = None,
    service_name: str = DEFAULT_PANEL_SERVICE,
    env_mode: str = "production",
) -> tuple[bool, str]:
    if not systemd_available():
        return False, "systemctl is not available on this host"

    if not (panel_dir / "build" / "index.js").exists():
        return False, f"Panel build not found in {panel_dir / 'build'} — build the panel first"
    if not node_binary():
        return False, "node was not found on PATH"

    user = (run_user or default_panel_run_user()).strip() or "root"
    return _write_unit(service_name, build_panel_unit(panel_dir, user, service_name, env_mode))


def service_action(service_name: str, action: str, lines: int = 100) -> tuple[bool, str]:
    if not systemd_available():
        return False, "systemctl is not available on this host"

    normalized = str(action or "").strip().lower()
    if normalized in {"start", "stop", "restart", "enable", "disable"}:
        result = _run(["sudo", "systemctl", normalized, service_name])
        return result.returncode == 0, (result.stdout + result.stderr).strip()

    if normalized == "status":
        result = _run(["sudo", "systemctl", "status", service_name, "--no-pager", "-n", "40"])
        return result.returncode == 0, (result.stdout + result.stderr).strip()

    if normalized == "logs":
        tail = max(10, int(lines or 100))
        result = _run(["sudo", "journalctl", "-u", service_name, "-n", str(tail), "--no-pager"])
        return result.returncode == 0, (result.stdout + result.stderr).strip()

    return False, f"Unsupported action: {action}"
