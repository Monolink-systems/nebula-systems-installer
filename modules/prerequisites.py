# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
"""Ubuntu dependency and runtime bootstrap.

The installer itself only needs the Python shipped by Ubuntu.  Nebula Core gets
an isolated CPython 3.11 runtime through uv, while Panel gets a verified official
Node.js LTS binary.  This avoids depending on the age of Ubuntu's repositories.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import MODE_PROD, DeploymentConfig, invoking_user
from .runner import CommandError, Runner

NODE_INDEX_URL = "https://nodejs.org/dist/index.json"
NODE_DIST_URL = "https://nodejs.org/dist"
MIN_NODE_MAJOR = 22
CORE_PYTHON = "3.11"
APT_GET = [
    "apt-get",
    "-o",
    "DPkg::Lock::Timeout=120",
    "-o",
    "Acquire::Retries=3",
]


def os_release() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for raw in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            values[key] = value.strip().strip('"')
    except OSError:
        pass
    return values


def verify_host() -> list[str]:
    issues: list[str] = []
    release = os_release()
    if platform.system() != "Linux":
        issues.append("Nebula services are supported only on Linux")
    distro = release.get("ID", "")
    relatives = release.get("ID_LIKE", "")
    if distro != "ubuntu" and "ubuntu" not in relatives and "debian" not in relatives:
        issues.append(
            f"Automatic package installation supports Ubuntu/Debian; detected {distro or 'unknown'}"
        )
    if not Path("/run/systemd/system").exists() or not shutil.which("systemctl"):
        issues.append("systemd is required for managed services")
    if platform.machine().lower() not in {"x86_64", "amd64", "aarch64", "arm64"}:
        issues.append(f"Unsupported CPU architecture: {platform.machine()}")
    return issues


def _major(version_output: str) -> int:
    match = re.search(r"(\d+)", version_output)
    return int(match.group(1)) if match else 0


def command_version(command: str) -> str:
    binary = shutil.which(command)
    if not binary:
        return ""
    try:
        import subprocess

        result = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, check=False
        )
        return (
            (result.stdout or result.stderr).strip() if result.returncode == 0 else ""
        )
    except OSError:
        return ""


def node_lts_name(binary: str = "") -> str:
    executable = binary or shutil.which("node") or ""
    if not executable:
        return ""
    try:
        import subprocess

        result = subprocess.run(
            [executable, "-p", "String(process.release.lts || '')"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except OSError:
        return ""


def ensure_base_packages(runner: Runner, *, production: bool) -> None:
    packages = [
        "ca-certificates",
        "curl",
        "git",
        "gnupg",
        "python3",
        "python3-venv",
        "xz-utils",
    ]
    if production:
        packages.extend(
            [
                "apt-transport-https",
                "debian-archive-keyring",
                "debian-keyring",
                "logrotate",
                "ufw",
            ]
        )
    runner.sudo([*APT_GET, "update"], capture=False)
    runner.sudo(
        [
            "env",
            "DEBIAN_FRONTEND=noninteractive",
            *APT_GET,
            "install",
            "-y",
            *packages,
        ],
        capture=False,
    )


def ensure_uv(runner: Runner) -> str:
    current = shutil.which("uv")
    if current and Path(current).as_posix().startswith(
        ("/usr/bin/", "/usr/local/bin/")
    ):
        return current

    with tempfile.TemporaryDirectory(prefix="nebula-uv-") as directory:
        script = Path(directory) / "install-uv.sh"
        runner.run(
            [
                "curl",
                "--proto",
                "=https",
                "--tlsv1.2",
                "-LsSf",
                "https://astral.sh/uv/install.sh",
                "-o",
                str(script),
            ]
        )
        runner.sudo(
            [
                "env",
                "UV_UNMANAGED_INSTALL=/usr/local/bin",
                "UV_NO_MODIFY_PATH=1",
                "sh",
                str(script),
            ],
            capture=False,
        )
    uv = shutil.which("uv") or "/usr/local/bin/uv"
    if not Path(uv).exists():
        raise CommandError("uv installation completed but /usr/local/bin/uv is missing")
    return uv


def select_node_release(index: list[dict]) -> dict:
    candidates: list[dict] = []
    for release in index:
        version = str(release.get("version") or "")
        major = _major(version)
        files = set(release.get("files") or [])
        if (
            release.get("lts")
            and major >= MIN_NODE_MAJOR
            and ("linux-x64" in files or "linux-arm64" in files)
        ):
            candidates.append(release)
    if not candidates:
        raise CommandError("Node.js did not publish a supported Linux LTS release")
    return max(
        candidates,
        key=lambda item: tuple(int(x) for x in item["version"].lstrip("v").split(".")),
    )


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "Nebula-Installer/2"})
    last_error: OSError | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                destination.write_bytes(response.read())
            return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(attempt + 1)
    raise CommandError(f"Download failed after 3 attempts: {url}: {last_error}")


def ensure_node_lts(runner: Runner) -> str:
    existing = command_version("node")
    existing_binary = shutil.which("node")
    if (
        existing_binary
        and _major(existing) >= MIN_NODE_MAJOR
        and node_lts_name(existing_binary)
    ):
        return existing_binary

    arch = {
        "x86_64": "x64",
        "amd64": "x64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(platform.machine().lower())
    if not arch:
        raise CommandError(
            f"No official Node.js binary mapping for {platform.machine()}"
        )

    with tempfile.TemporaryDirectory(prefix="nebula-node-") as directory:
        temporary = Path(directory)
        index_path = temporary / "index.json"
        _download(NODE_INDEX_URL, index_path)
        release = select_node_release(
            json.loads(index_path.read_text(encoding="utf-8"))
        )
        version = str(release["version"])
        filename = f"node-{version}-linux-{arch}.tar.xz"
        archive = temporary / filename
        sums = temporary / "SHASUMS256.txt"
        _download(f"{NODE_DIST_URL}/{version}/{filename}", archive)
        _download(f"{NODE_DIST_URL}/{version}/SHASUMS256.txt", sums)

        expected = ""
        for line in sums.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[-1].lstrip("*") == filename:
                expected = parts[0]
                break
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        if not expected or actual != expected:
            raise CommandError("Node.js archive checksum verification failed")

        install_root = Path("/opt/nebula-runtimes")
        release_dir = install_root / filename.removesuffix(".tar.xz")
        runner.sudo(["mkdir", "-p", str(install_root)])
        runner.sudo(["tar", "-xJf", str(archive), "-C", str(install_root)])
        for binary in ("node", "npm", "npx", "corepack"):
            source = release_dir / "bin" / binary
            if source.exists() or runner.dry_run:
                runner.sudo(["ln", "-sfn", str(source), f"/usr/local/bin/{binary}"])

    node = "/usr/local/bin/node"
    result = runner.run([node, "--version"])
    if _major(result.stdout) < MIN_NODE_MAJOR:
        raise CommandError(
            f"Node.js {MIN_NODE_MAJOR}+ is required; got {result.stdout.strip()}"
        )
    return node


def ensure_caddy(runner: Runner) -> None:
    if shutil.which("caddy"):
        return
    with tempfile.TemporaryDirectory(prefix="nebula-caddy-") as directory:
        temporary = Path(directory)
        key = temporary / "caddy.gpg.key"
        keyring = temporary / "caddy-stable-archive-keyring.gpg"
        source_list = temporary / "caddy-stable.list"
        runner.run(
            [
                "curl",
                "-1sLf",
                "https://dl.cloudsmith.io/public/caddy/stable/gpg.key",
                "-o",
                str(key),
            ]
        )
        runner.run(["gpg", "--dearmor", "--yes", "--output", str(keyring), str(key)])
        runner.run(
            [
                "curl",
                "-1sLf",
                "https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt",
                "-o",
                str(source_list),
            ]
        )
        runner.sudo(
            [
                "install",
                "-o",
                "root",
                "-g",
                "root",
                "-m",
                "0644",
                str(keyring),
                "/usr/share/keyrings/caddy-stable-archive-keyring.gpg",
            ]
        )
        runner.sudo(
            [
                "install",
                "-o",
                "root",
                "-g",
                "root",
                "-m",
                "0644",
                str(source_list),
                "/etc/apt/sources.list.d/caddy-stable.list",
            ]
        )
    runner.sudo([*APT_GET, "update"], capture=False)
    runner.sudo(
        [
            "env",
            "DEBIAN_FRONTEND=noninteractive",
            *APT_GET,
            "install",
            "-y",
            "caddy",
        ],
        capture=False,
    )


def ensure_docker_engine(runner: Runner) -> str:
    version = command_version("docker")
    if _major(version) >= 24:
        return version

    release = os_release()
    distro = release.get("ID", "ubuntu")
    if distro not in {"ubuntu", "debian"}:
        distro = "ubuntu"
    codename = release.get("VERSION_CODENAME", "").strip()
    if not codename:
        raise CommandError(
            "Cannot determine Ubuntu/Debian codename for Docker repository"
        )
    architecture = runner.run(["dpkg", "--print-architecture"]).stdout.strip()

    with tempfile.TemporaryDirectory(prefix="nebula-docker-") as directory:
        temporary = Path(directory)
        key = temporary / "docker.asc"
        source = temporary / "docker.sources"
        runner.run(
            [
                "curl",
                "--proto",
                "=https",
                "--tlsv1.2",
                "-fsSL",
                f"https://download.docker.com/linux/{distro}/gpg",
                "-o",
                str(key),
            ]
        )
        source.write_text(
            "Types: deb\n"
            f"URIs: https://download.docker.com/linux/{distro}\n"
            f"Suites: {codename}\n"
            "Components: stable\n"
            f"Architectures: {architecture}\n"
            "Signed-By: /etc/apt/keyrings/docker.asc\n",
            encoding="utf-8",
        )
        runner.sudo(["install", "-d", "-m", "0755", "/etc/apt/keyrings"])
        runner.sudo(
            [
                "install",
                "-o",
                "root",
                "-g",
                "root",
                "-m",
                "0644",
                str(key),
                "/etc/apt/keyrings/docker.asc",
            ]
        )
        runner.sudo(
            [
                "install",
                "-o",
                "root",
                "-g",
                "root",
                "-m",
                "0644",
                str(source),
                "/etc/apt/sources.list.d/docker.sources",
            ]
        )
    runner.sudo([*APT_GET, "update"], capture=False)
    conflicting_packages: list[str] = []
    for package in (
        "docker.io",
        "docker-compose",
        "docker-compose-v2",
        "docker-doc",
        "podman-docker",
        "containerd",
        "runc",
    ):
        package_status = runner.run(
            ["dpkg-query", "-W", "-f=${db:Status-Abbrev}", package],
            check=False,
        )
        if package_status.returncode == 0 and package_status.stdout.startswith("ii "):
            conflicting_packages.append(package)
    if conflicting_packages:
        # Docker's official packages conflict with distro-provided Engine,
        # containerd, and runc packages. Removing packages does not delete
        # /var/lib/docker, so existing images and volumes remain available.
        runner.sudo(
            [
                "env",
                "DEBIAN_FRONTEND=noninteractive",
                *APT_GET,
                "remove",
                "-y",
                *conflicting_packages,
            ],
            capture=False,
        )
    runner.sudo(
        [
            "env",
            "DEBIAN_FRONTEND=noninteractive",
            *APT_GET,
            "install",
            "-y",
            "--allow-downgrades",
            "docker-ce",
            "docker-ce-cli",
            "containerd.io",
            "docker-buildx-plugin",
            "docker-compose-plugin",
        ],
        capture=False,
    )
    installed = command_version("docker")
    if _major(installed) < 24:
        raise CommandError(
            f"Docker Engine 24+ is required; installed: {installed or 'missing'}"
        )
    return installed


def ensure_service_accounts(config: DeploymentConfig, runner: Runner) -> None:
    if config.mode != MODE_PROD:
        runner.sudo(["usermod", "-aG", "docker", invoking_user()])
        return

    if (
        runner.run(["getent", "group", config.shared_group], check=False).returncode
        != 0
    ):
        runner.sudo(["groupadd", "--system", config.shared_group])
    for username in (config.core_user, config.panel_user):
        if runner.run(["getent", "group", username], check=False).returncode != 0:
            runner.sudo(["groupadd", "--system", username])
        if runner.run(["id", "-u", username], check=False).returncode != 0:
            runner.sudo(
                [
                    "useradd",
                    "--system",
                    "--no-create-home",
                    "--home-dir",
                    config.root,
                    "--gid",
                    username,
                    "--shell",
                    "/usr/sbin/nologin",
                    username,
                ]
            )
        else:
            runner.sudo(["usermod", "--gid", username, username])
        runner.sudo(["usermod", "-aG", config.shared_group, username])
    runner.sudo(["usermod", "-aG", "docker", config.core_user])


def start_docker(runner: Runner) -> None:
    runner.sudo(["systemctl", "enable", "--now", "docker"])
    result = runner.sudo(["docker", "info"], check=False)
    if result.returncode != 0:
        raise CommandError(
            "Docker Engine was installed but its daemon is not responding"
        )


def _ssh_ports(runner: Runner) -> set[int]:
    result = runner.sudo(["sshd", "-T"], check=False)
    ports: set[int] = set()
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            if line.startswith("port "):
                try:
                    ports.add(int(line.split()[1]))
                except (IndexError, ValueError):
                    pass
    return ports or {22}


def configure_firewall(runner: Runner, *, enable: bool) -> str:
    if not enable or not shutil.which("ufw"):
        return "Firewall configuration skipped"
    for port in sorted(_ssh_ports(runner)):
        runner.sudo(["ufw", "allow", f"{port}/tcp"])
    runner.sudo(["ufw", "allow", "80/tcp"])
    runner.sudo(["ufw", "allow", "443/tcp"])
    runner.sudo(["ufw", "--force", "enable"])
    return "UFW enabled; SSH, HTTP and HTTPS are allowed"
