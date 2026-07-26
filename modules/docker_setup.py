# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
"""Docker Engine detection, installation, and daemon access checks."""
import getpass
import os
import platform
import shutil
import subprocess
from pathlib import Path

SUPPORTED_AUTO_INSTALL = {"debian", "ubuntu", "linux", "rhel"}


def is_installed() -> bool:
    return shutil.which("docker") is not None


def daemon_status() -> tuple[bool, str]:
    if not is_installed():
        return False, "docker binary not found"
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, text=True)
        return result.returncode == 0, (result.stdout + result.stderr).strip()
    except OSError as exc:
        return False, str(exc)


def detect_distro() -> str:
    try:
        os_release = Path("/etc/os-release")
        if os_release.exists():
            data = os_release.read_text(encoding="utf-8").lower()
            if "debian" in data or "ubuntu" in data:
                return "debian"
            if "rhel" in data or "fedora" in data or "centos" in data:
                return "rhel"
    except OSError:
        pass
    return platform.system().lower()


def install(workdir: Path) -> tuple[bool, str]:
    distro = detect_distro()
    if distro not in SUPPORTED_AUTO_INSTALL:
        return False, (
            f"Automatic installation is not available on '{distro}'. "
            "See https://docs.docker.com/engine/install/"
        )

    script = workdir / "get-docker.sh"
    try:
        subprocess.run(
            ["/bin/sh", "-c", f"curl -fsSL https://get.docker.com -o {script}"],
            cwd=workdir,
            check=True,
        )
        subprocess.run(["sudo", "sh", str(script)], cwd=workdir, check=True)
        return True, "Docker installation script completed"
    except subprocess.CalledProcessError as exc:
        return False, f"Docker installation failed: {exc}"
    finally:
        if script.exists():
            script.unlink()


def start_daemon() -> tuple[bool, str]:
    try:
        subprocess.run(["sudo", "systemctl", "start", "docker"], check=True)
        subprocess.run(["sudo", "systemctl", "enable", "docker"], check=True)
        return True, "Docker service started and enabled"
    except subprocess.CalledProcessError as exc:
        return False, f"Failed to start Docker: {exc}"


def current_username() -> str:
    username = os.getenv("SUDO_USER") or os.getenv("USER") or ""
    if username:
        return username
    try:
        return getpass.getuser()
    except OSError:
        return ""


def add_user_to_group(username: str = "") -> tuple[bool, str]:
    target = username or current_username()
    if not target:
        return False, "Could not determine the current user. Run: sudo usermod -aG docker <user>"
    try:
        subprocess.run(["sudo", "usermod", "-aG", "docker", target], check=True)
        return True, (
            f"User '{target}' added to the docker group. "
            "Log out and back in for the change to take effect."
        )
    except subprocess.CalledProcessError as exc:
        return False, f"Failed to add user to the docker group: {exc}"
