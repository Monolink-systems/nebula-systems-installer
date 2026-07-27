#!/usr/bin/env python3
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
"""Nebula's zero-preparation Ubuntu installer and deployment CLI."""

from __future__ import annotations

import argparse
import getpass
import os
import re
import socket
import stat
import subprocess
import sys
from pathlib import Path

from modules import (
    backups,
    components,
    core_api,
    database,
    env,
    prerequisites,
    proxy,
    ui,
)
from modules.config import (
    MODE_DEV,
    MODE_PROD,
    DeploymentConfig,
    normalize_domain,
)
from modules.core_service import install_cli, install_services, service_action
from modules.paths import installer_dir
from modules.runner import CommandError, Runner

VERSION = (installer_dir() / "VERSION").read_text(encoding="utf-8").strip()
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")


def _load_config(args: argparse.Namespace) -> DeploymentConfig:
    config = DeploymentConfig.load(getattr(args, "state", ""))
    if not config:
        raise CommandError("Nebula is not installed yet. Run: ./install.sh")
    return config


def _prompt_domain(label: str, default: str) -> str:
    while True:
        raw = ui.ask(label, default)
        try:
            return normalize_domain(raw)
        except ValueError as exc:
            ui.warn(str(exc))


def _read_admin_password(
    args: argparse.Namespace, mode: str, username: str
) -> tuple[str, bool]:
    minimum = 16 if mode == MODE_PROD else 12

    def validation_error(password: str) -> str:
        if len(password) < minimum:
            return f"Password must contain at least {minimum} characters."
        if username.lower() in password.lower():
            return "Password must not contain the administrator username."
        return ""

    password_file = getattr(args, "admin_password_file", "")
    if password_file:
        try:
            password_path = Path(password_file).expanduser()
            metadata = password_path.stat()
            if mode == MODE_PROD and (
                not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077
            ):
                raise CommandError(
                    "Production password file must be a regular file readable "
                    "only by its owner (chmod 600)."
                )
            password = password_path.read_text(encoding="utf-8").rstrip("\r\n")
            error = validation_error(password)
            if error:
                raise CommandError(error)
            return password, False
        except (CommandError, OSError) as exc:
            if not sys.stdin.isatty():
                raise
            ui.warn(f"{exc} Enter the password manually.")
            args.admin_password_file = ""

    environment_password = os.getenv("NEBULA_ADMIN_PASSWORD")
    if environment_password:
        error = validation_error(environment_password)
        if error:
            if not sys.stdin.isatty():
                raise CommandError(error)
            ui.warn(
                f"NEBULA_ADMIN_PASSWORD is invalid: {error} "
                "Enter the password manually."
            )
        else:
            return environment_password, False

    if not sys.stdin.isatty():
        if mode == MODE_DEV:
            password = f"nebula-dev-{os.urandom(6).hex()}"
            return password, True
        else:
            raise CommandError(
                "Production admin password is required in non-interactive mode; "
                "use --admin-password-file"
            )

    while True:
        if mode == MODE_DEV:
            password = getpass.getpass(
                "Administrator password [Enter to generate a local password]: "
            ).strip()
            if not password:
                return f"nebula-dev-{os.urandom(6).hex()}", True
        else:
            password = getpass.getpass(
                "Administrator password (minimum 16 characters): "
            ).strip()

        confirm = getpass.getpass("Confirm administrator password: ").strip()
        if password != confirm:
            ui.warn("Passwords do not match. Try again.")
            continue

        error = validation_error(password)
        if error:
            ui.warn(error)
            continue
        return password, False


def _admin_credentials(args: argparse.Namespace, mode: str) -> tuple[str, str, bool]:
    provided = getattr(args, "admin_user", "")
    if not sys.stdin.isatty():
        username = provided or "nebula_admin"
        if not USERNAME_RE.fullmatch(username):
            raise CommandError(
                "Administrator username must contain 5-32 letters, digits, or underscores."
            )
        password, generated = _read_admin_password(args, mode, username)
        return username, password, generated

    while True:
        username = provided or ui.ask("Administrator username", "nebula_admin")
        provided = ""
        if USERNAME_RE.fullmatch(username):
            password, generated = _read_admin_password(args, mode, username)
            return username, password, generated
        ui.warn(
            "Username must contain 5-32 letters, digits, or underscores. Try again."
        )


def _prepare_root(config: DeploymentConfig, runner: Runner) -> None:
    if config.mode == MODE_PROD:
        runner.ensure_directory(
            config.root_path,
            owner="root",
            group=config.shared_group,
            mode=0o755,
            privileged=True,
        )
    else:
        config.root_path.mkdir(parents=True, exist_ok=True)
        config.root_path.chmod(0o750)


def _provision_components(
    config: DeploymentConfig,
    runner: Runner,
    *,
    uv_binary: str,
    branch: str = "",
    update_sources: bool = True,
) -> dict[str, str]:
    if update_sources:
        ui.step("Downloading Nebula Core and Nebula Panel")
        for message in components.sync_sources(config, runner, branch=branch):
            ui.ok(message)

    ok, version_message = components.validate_versions(
        config.core_path, config.panel_path
    )
    if not ok:
        raise CommandError(version_message)
    ui.ok(version_message)

    ui.step("Configuring Nebula Core")
    components.prepare_core(config, runner, uv_binary)
    core_values = env.configure_environment(config, runner)
    env.configure_core_profile(config, runner)
    env.prepare_storage(config, runner)
    database.ensure_system_database(config, runner)
    ui.ok("Core runtime, dependencies, profile, and secrets are ready")

    ui.step("Configuring Nebula Panel")
    components.prepare_panel(config, runner)
    ui.ok(
        "Panel dependencies and production bundle are ready"
        if config.mode == MODE_PROD
        else "Panel dependencies and Vite development server are ready"
    )
    low, moderate, high, critical = components.panel_audit(config, runner)
    if any((low, moderate, high, critical)):
        ui.warn(
            "Upstream Panel dependency audit: "
            f"{critical} critical, {high} high, {moderate} moderate, {low} low"
        )
    if config.mode == MODE_PROD and critical:
        raise CommandError(
            "Production installation stopped: Panel has critical runtime dependency advisories"
        )
    if not core_values.get("NEBULA_INSTALLER_TOKEN"):
        raise CommandError("Core installer token was not generated")
    return core_values


def _install_management(config: DeploymentConfig, runner: Runner) -> Path:
    components.copy_installer(installer_dir(), config, runner)
    config.installed_version = VERSION
    config.save(runner)
    cli = install_cli(config, runner)
    install_services(config, runner)
    if config.mode == MODE_PROD:
        backups.install_backup_timer(config, runner)
    return cli


def _bootstrap_admin(
    config: DeploymentConfig,
    args: argparse.Namespace,
    core_values: dict[str, str],
) -> tuple[str, str]:
    token = core_values["NEBULA_INSTALLER_TOKEN"]
    count = core_api.admin_count(config.core_host, config.core_port, token)
    if count is not None and count > 0:
        ui.ok("A super-administrator already exists")
        return "", ""
    username, password, generated = _admin_credentials(args, config.mode)
    ok, message = core_api.create_admin(
        config.core_host,
        config.core_port,
        username,
        password,
        token,
    )
    if not ok:
        raise CommandError(f"Core did not create the super-administrator: {message}")
    ui.ok(message)
    return username, password if generated else ""


def install_command(args: argparse.Namespace) -> int:
    ui.banner(VERSION)
    mode = args.mode or (ui.choose_mode() if sys.stdin.isatty() else "")
    if mode not in {MODE_DEV, MODE_PROD}:
        raise CommandError("Choose --mode dev or --mode prod")
    args.mode = mode

    panel_domain = args.panel_domain
    core_domain = args.core_domain
    if mode == MODE_PROD:
        if sys.stdin.isatty():
            while True:
                try:
                    panel_domain = (
                        normalize_domain(panel_domain)
                        if panel_domain
                        else _prompt_domain("Panel domain", "panel.example.com")
                    )
                except ValueError as exc:
                    ui.warn(str(exc))
                    panel_domain = ""
                    continue
                try:
                    core_domain = (
                        normalize_domain(core_domain)
                        if core_domain
                        else _prompt_domain("Core API domain", "core.example.com")
                    )
                except ValueError as exc:
                    ui.warn(str(exc))
                    core_domain = ""
                    continue
                if panel_domain == core_domain:
                    ui.warn("Panel and Core must use different domains.")
                    core_domain = ""
                    continue
                break
        if not panel_domain or not core_domain:
            raise CommandError("Production requires --panel-domain and --core-domain")
        args.panel_domain = panel_domain
        args.core_domain = core_domain

    while True:
        try:
            config = DeploymentConfig.create(
                mode,
                root=args.root,
                panel_domain=panel_domain,
                core_domain=core_domain,
            )
            break
        except ValueError as exc:
            if not sys.stdin.isatty():
                raise
            ui.warn(str(exc))
            default_root = (
                "/opt/nebula"
                if mode == MODE_PROD
                else str(DeploymentConfig.create(MODE_DEV).root_path)
            )
            args.root = ui.ask("Installation directory", default_root)
    if config.panel_domain and config.panel_domain == config.core_domain:
        raise CommandError("Panel and Core must use different domains")

    ui.section("Installation plan")
    print(f"  Mode       : {config.mode}")
    print(f"  Directory  : {config.root}")
    print(f"  Panel      : {config.panel_url}")
    print(f"  Core API   : {config.public_core_url}")
    print(f"  Services   : {config.core_service}, {config.panel_service}")
    if not args.yes and sys.stdin.isatty():
        if not ui.ask_yes_no("Start the installation?", True):
            ui.info("Installation cancelled by the user.")
            return 0
        args.yes = True

    host_issues = prerequisites.verify_host()
    if host_issues:
        raise CommandError("\n".join(host_issues))

    runner = Runner(verbose=args.verbose)
    runner.require_privileges()
    try:
        if mode == MODE_PROD:
            ready, dns_messages = proxy.dns_status(config)
            ui.step("Checking public DNS")
            for message in dns_messages:
                (ui.ok if ready or "->" in message else ui.warn)(message)
            if not ready:
                ui.warn(
                    "TLS will become available after the A/AAAA records resolve "
                    "to this server and ports 80 and 443 are reachable."
                )

        ui.step("Preparing Ubuntu and host dependencies")
        prerequisites.ensure_base_packages(runner, production=mode == MODE_PROD)
        docker_version = prerequisites.ensure_docker_engine(runner)
        uv_binary = prerequisites.ensure_uv(runner)
        node_binary = prerequisites.ensure_node_lts(runner)
        if mode == MODE_PROD:
            prerequisites.ensure_caddy(runner)
        ui.ok(f"uv: {uv_binary}")
        node_version = subprocess.run(
            [node_binary, "--version"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        ui.ok(f"Node.js: {node_version}")
        ui.ok(f"Docker: {docker_version}")

        ui.step("Configuring service accounts, Docker, and directories")
        prerequisites.ensure_service_accounts(config, runner)
        prerequisites.start_docker(runner)
        _prepare_root(config, runner)
        ui.ok("Docker is running and service permissions are configured")

        core_values = _provision_components(
            config,
            runner,
            uv_binary=uv_binary,
            branch=args.branch,
        )

        ui.step("Installing system services and the nebula command")
        cli_path = _install_management(config, runner)
        ui.ok(f"CLI: {cli_path}")
        ui.ok("Core and Panel are enabled at boot")

        ui.step("Checking Core and creating the super-administrator")
        if not core_api.wait_until_ready(
            config.core_host, config.core_port, timeout=60
        ):
            _, logs = service_action(
                config.core_service, "logs", runner=runner, lines=60
            )
            raise CommandError(f"Nebula Core did not become ready in time.\n{logs}")
        admin_user, generated_password = _bootstrap_admin(config, args, core_values)

        https_ready = False
        if mode == MODE_PROD:
            ui.step("Configuring reverse proxy, TLS, and firewall")
            proxy.install_caddy_config(config, runner)
            ui.ok("Caddy is managing HTTPS and certificate renewal")
            ui.ok(prerequisites.configure_firewall(runner, enable=not args.no_firewall))
            dns_ready, _ = proxy.dns_status(config)
            if dns_ready:
                https_ready = proxy.wait_for_https(config.panel_domain, timeout=60)
                (ui.ok if https_ready else ui.warn)(
                    "Panel is responding over HTTPS"
                    if https_ready
                    else "Caddy is still obtaining the certificate; check: nebula logs proxy"
                )

        ui.section("Installation complete")
        ui.ok(f"Nebula is available at {config.panel_url}")
        print(f"  Core API : {config.public_core_url}")
        print(f"  CLI      : {cli_path}")
        print("  Verify   : nebula doctor")
        print("  Logs     : nebula logs all")
        if admin_user:
            print(f"  Admin    : {admin_user}")
        if generated_password:
            ui.warn(f"Store this generated password now: {generated_password}")
        if mode == MODE_DEV and str(cli_path.parent) not in os.getenv("PATH", "").split(
            ":"
        ):
            ui.info(f'Add the CLI to PATH: export PATH="{cli_path.parent}:$PATH"')
        if mode == MODE_PROD and not https_ready:
            ui.info(
                "Caddy will obtain and renew certificates after DNS becomes available."
            )
        return 0
    finally:
        runner.close()


def _service_names(config: DeploymentConfig, target: str) -> list[str]:
    if target == "core":
        return [config.core_service]
    if target == "panel":
        return [config.panel_service]
    if target == "proxy":
        return ["caddy"]
    services = [config.core_service, config.panel_service]
    if config.mode == MODE_PROD:
        services.append("caddy")
    return services


def services_command(args: argparse.Namespace) -> int:
    config = _load_config(args)
    runner = Runner(verbose=args.verbose)
    failed = False
    for service in _service_names(config, args.target):
        ok, output = service_action(
            service,
            args.command,
            runner=runner,
            lines=getattr(args, "lines", 120),
            follow=getattr(args, "follow", False),
        )
        if not getattr(args, "follow", False):
            print(f"\n--- {service} ---")
            print(output or ("ok" if ok else "failed"))
        failed = failed or not ok
    return 1 if failed else 0


def status_command(args: argparse.Namespace) -> int:
    config = _load_config(args)
    ui.banner(VERSION)
    ui.section("Deployment")
    print(f"  Mode       : {config.mode}")
    print(f"  Root       : {config.root}")
    core_version, panel_version = components.component_versions(
        config.core_path, config.panel_path
    )
    print(f"  Core       : {core_version}")
    print(f"  Panel      : {panel_version}")
    print(f"  Panel URL  : {config.panel_url}")
    print(f"  Core URL   : {config.public_core_url}")

    ui.section("Services")
    healthy = True
    for service in [config.core_service, config.panel_service]:
        ok, output = service_action(service, "is-active")
        state = output.strip() or "unknown"
        print(f"  {service:<14}: {state}")
        healthy = healthy and ok
    if config.mode == MODE_PROD:
        ok, output = service_action("caddy", "is-active")
        print(f"  {'caddy':<14}: {output.strip() or 'unknown'}")
        healthy = healthy and ok

    core_ready = core_api.wait_until_ready(
        config.core_host, config.core_port, timeout=2
    )
    print(f"\n  Core health : {'ok' if core_ready else 'offline'}")
    return 0 if healthy and core_ready else 1


def _check_http(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def doctor_command(args: argparse.Namespace) -> int:
    config = _load_config(args)
    ui.banner(VERSION)
    checks: list[tuple[str, bool, str]] = []

    checks.append(
        (
            "Core sources",
            (config.core_path / "nebula_core/main.py").exists(),
            str(config.core_path),
        )
    )
    checks.append(
        (
            "Panel sources",
            (config.panel_path / "package.json").exists(),
            str(config.panel_path),
        )
    )
    compatible, versions = components.validate_versions(
        config.core_path, config.panel_path
    )
    checks.append(("Version compatibility", compatible, versions))
    checks.append(
        (
            "Python runtime",
            (config.core_path / ".venv/bin/python").exists(),
            "Core .venv",
        )
    )
    node_version = components.node_version()
    node_lts = prerequisites.node_lts_name()
    checks.append(
        (
            "Node.js LTS",
            prerequisites._major(node_version) >= prerequisites.MIN_NODE_MAJOR
            and bool(node_lts),
            f"{node_version} ({node_lts})" if node_version else "missing",
        )
    )
    checks.append(("Docker", service_action("docker", "is-active")[0], "systemd"))
    checks.append(
        (
            "Core service",
            service_action(config.core_service, "is-active")[0],
            config.core_service,
        )
    )
    checks.append(
        (
            "Panel service",
            service_action(config.panel_service, "is-active")[0],
            config.panel_service,
        )
    )
    checks.append(
        (
            "Core API",
            core_api.wait_until_ready(config.core_host, config.core_port, timeout=3),
            config.panel_core_url,
        )
    )
    checks.append(
        (
            "Panel port",
            _check_http(config.panel_host, config.panel_port),
            f"{config.panel_host}:{config.panel_port}",
        )
    )
    schema_ok, schema_detail = database.schema_status(
        config.core_path / "storage/databases/system.db"
    )
    checks.append(
        (
            "Core database schema",
            schema_ok is not False,
            schema_detail,
        )
    )

    if config.mode == MODE_PROD:
        core_values = env.read_env_file(config.core_env_path)
        panel_values = env.read_env_file(config.panel_path / ".env")
        # A non-root operator cannot read 0600 service files; ownership and mode
        # still remain useful checks without asking for sudo.
        readable = bool(core_values and panel_values)
        token_ok = readable and core_values.get(
            "NEBULA_INSTALLER_TOKEN"
        ) == panel_values.get("NEBULA_INSTALLER_TOKEN")
        checks.append(
            (
                "Shared internal token",
                token_ok or not readable,
                "matched"
                if token_ok
                else "protected; run sudo nebula doctor for deep check",
            )
        )
        checks.append(
            (
                "Secure cookies",
                core_values.get("NEBULA_COOKIE_SECURE") == "true" or not readable,
                "production policy",
            )
        )
        dns_ready, messages = proxy.dns_status(config)
        checks.append(("Public DNS", dns_ready, "; ".join(messages[:2])))
        checks.append(
            ("Caddy", service_action("caddy", "is-active")[0], "automatic HTTPS")
        )

    ui.section("Diagnostics")
    failed = 0
    for name, ok, detail in checks:
        if ok:
            ui.ok(f"{name}: {detail}")
        else:
            ui.error(f"{name}: {detail}")
            failed += 1
    if failed:
        ui.warn(f"Detected issues: {failed}. Run: nebula repair")
    else:
        ui.ok("The deployment is ready")
    return 1 if failed else 0


def _privileged_reexec(
    config: DeploymentConfig, args: argparse.Namespace
) -> int | None:
    if config.mode != MODE_PROD or os.geteuid() == 0:
        return None
    runner = Runner(verbose=args.verbose)
    runner.require_privileges()
    command = [
        "env",
        f"NEBULA_STATE_FILE={config.state_path}",
        str(config.core_path / ".venv/bin/python"),
        str(config.installer_path / "main.py"),
        args.command,
    ]
    if args.command == "backup" and getattr(args, "scheduled", False):
        command.append("--scheduled")
    result = runner.sudo(command, capture=False, check=False)
    return result.returncode


def backup_command(args: argparse.Namespace) -> int:
    config = _load_config(args)
    reexec = _privileged_reexec(config, args)
    if reexec is not None:
        return reexec
    path = backups.create_backup(
        config,
        include_workspaces=not getattr(args, "databases_only", False),
    )
    ui.ok(f"Backup created: {path}")
    return 0


def repair_command(args: argparse.Namespace) -> int:
    config = _load_config(args)
    runner = Runner(verbose=args.verbose)
    runner.require_privileges()
    try:
        ui.banner(VERSION)
        ui.step("Checking runtimes and restoring configuration")
        uv_binary = prerequisites.ensure_uv(runner)
        prerequisites.ensure_node_lts(runner)
        prerequisites.ensure_service_accounts(config, runner)
        prerequisites.start_docker(runner)
        _prepare_root(config, runner)
        _provision_components(
            config,
            runner,
            uv_binary=uv_binary,
            update_sources=False,
        )
        _install_management(config, runner)
        if config.mode == MODE_PROD:
            prerequisites.ensure_caddy(runner)
            proxy.install_caddy_config(config, runner)
        runner.sudo(["systemctl", "restart", config.core_service, config.panel_service])
        ui.ok("Configuration, build, and services were restored")
        return 0
    finally:
        runner.close()


def update_command(args: argparse.Namespace) -> int:
    config = _load_config(args)
    runner = Runner(verbose=args.verbose)
    runner.require_privileges()
    try:
        ui.banner(VERSION)
        if config.mode == MODE_PROD:
            result = runner.sudo(
                [
                    "env",
                    f"NEBULA_STATE_FILE={config.state_path}",
                    str(config.core_path / ".venv/bin/python"),
                    str(config.installer_path / "main.py"),
                    "backup",
                    "--databases-only",
                ],
                capture=False,
                check=False,
            )
            if result.returncode != 0:
                raise CommandError("Pre-update backup failed; update was cancelled")
        uv_binary = prerequisites.ensure_uv(runner)
        prerequisites.ensure_node_lts(runner)
        _provision_components(
            config,
            runner,
            uv_binary=uv_binary,
            branch=args.branch,
            update_sources=True,
        )
        _install_management(config, runner)
        runner.sudo(["systemctl", "restart", config.core_service, config.panel_service])
        if not core_api.wait_until_ready(
            config.core_host, config.core_port, timeout=60
        ):
            raise CommandError("Core did not become healthy after the update")
        ui.ok("Core and Panel updated together and restarted")
        return 0
    finally:
        runner.close()


def rotate_secrets_command(args: argparse.Namespace) -> int:
    config = _load_config(args)
    runner = Runner(verbose=args.verbose)
    runner.require_privileges()
    try:
        env.configure_environment(config, runner, rotate_secrets=True)
        runner.sudo(["systemctl", "restart", config.core_service, config.panel_service])
        ui.ok(
            "Internal, session and password-reset secrets rotated; all sessions were revoked"
        )
        return 0
    finally:
        runner.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nebula",
        description="Install and operate Nebula Core + Panel on Ubuntu.",
    )
    parser.add_argument(
        "--version", action="version", version=f"Nebula Installer {VERSION}"
    )
    parser.add_argument("--state", default="", help=argparse.SUPPRESS)
    parser.add_argument("--verbose", action="store_true", help="show host commands")
    subparsers = parser.add_subparsers(dest="command")

    install = subparsers.add_parser("install", help="guided installation")
    install.add_argument("--mode", choices=[MODE_DEV, MODE_PROD], default="")
    install.add_argument("--root", default="")
    install.add_argument("--panel-domain", default="")
    install.add_argument("--core-domain", default="")
    install.add_argument("--admin-user", default="")
    install.add_argument("--admin-password-file", default="")
    install.add_argument("--branch", default="")
    install.add_argument("--yes", "-y", action="store_true")
    install.add_argument("--no-firewall", action="store_true")

    subparsers.add_parser("status", help="deployment summary")
    subparsers.add_parser("doctor", help="diagnose installation and compatibility")
    subparsers.add_parser(
        "repair", help="idempotently rebuild configuration and services"
    )
    update = subparsers.add_parser(
        "update", help="backup, update Core + Panel, rebuild and restart"
    )
    update.add_argument("--branch", default="")

    for command in ("start", "stop", "restart"):
        service = subparsers.add_parser(command, help=f"{command} services")
        service.add_argument(
            "target",
            nargs="?",
            choices=["all", "core", "panel", "proxy"],
            default="all",
        )

    logs = subparsers.add_parser("logs", help="show service logs")
    logs.add_argument(
        "target", nargs="?", choices=["all", "core", "panel", "proxy"], default="all"
    )
    logs.add_argument("--lines", type=int, default=120)
    logs.add_argument("--follow", "-f", action="store_true")

    backup = subparsers.add_parser("backup", help="create a consistent backup")
    backup.add_argument("--scheduled", action="store_true", help=argparse.SUPPRESS)
    backup.add_argument("--databases-only", action="store_true")
    subparsers.add_parser(
        "rotate-secrets",
        help="rotate production secrets and revoke sessions",
    )
    return parser


def main() -> int:
    parser = build_parser()
    raw = sys.argv[1:]
    if not raw:
        raw = ["install"]
    args = parser.parse_args(raw)
    while True:
        try:
            if args.command == "install":
                return install_command(args)
            if args.command == "status":
                return status_command(args)
            if args.command == "doctor":
                return doctor_command(args)
            if args.command in {"start", "stop", "restart", "logs"}:
                return services_command(args)
            if args.command == "backup":
                return backup_command(args)
            if args.command == "repair":
                return repair_command(args)
            if args.command == "update":
                return update_command(args)
            if args.command == "rotate-secrets":
                return rotate_secrets_command(args)
            parser.print_help()
            return 2
        except (CommandError, OSError, ValueError) as exc:
            ui.error(str(exc))
            if args.command == "install" and sys.stdin.isatty():
                ui.info(
                    "Completed steps have been preserved and may be run again safely."
                )
                if ui.ask_yes_no(
                    "Retry the installation from the last completed state?", True
                ):
                    ui.info("Retrying the installation.")
                    continue
                ui.info(
                    "Installation stopped by the user. Run the same command to resume."
                )
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
