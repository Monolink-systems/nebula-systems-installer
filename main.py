#!/usr/bin/env python3
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
"""Nebula Systems Installer.

Provisions and manages a Nebula deployment composed of two independent
repositories: Nebula Core and Nebula Panel. Runs on a stock Python 3.11
interpreter with no third-party dependencies.
"""
import argparse
import getpass
import sys
from pathlib import Path

from modules import components, core_api, docker_setup, paths, ui
from modules import env as env_module
from modules.core_service import (
    DEFAULT_CORE_SERVICE,
    DEFAULT_PANEL_SERVICE,
    default_core_run_user,
    default_panel_run_user,
    install_core_service,
    install_panel_service,
    node_binary,
    service_action,
    systemd_available,
)

VERSION = (paths.installer_dir() / "VERSION").read_text(encoding="utf-8").strip()

DEFAULT_CORE_HOST = "127.0.0.1"
DEFAULT_CORE_PORT = 8000
DEFAULT_PANEL_HOST = "0.0.0.0"
DEFAULT_PANEL_PORT = 3000


class Deployment:
    """Resolved locations of the components this run operates on."""

    def __init__(self, core: str = "", panel: str = ""):
        self.core = paths.core_dir(core or None)
        self.panel = paths.panel_dir(panel or None)

    @property
    def core_ready(self) -> bool:
        return paths.is_core_dir(self.core)

    @property
    def panel_ready(self) -> bool:
        return paths.is_panel_dir(self.panel)

    def describe(self) -> None:
        print(f" Core  : {self.core if self.core_ready else 'not found'}")
        print(f" Panel : {self.panel if self.panel_ready else 'not found'}")

    def require_core(self) -> Path:
        if not self.core_ready:
            ui.error(
                "Nebula Core was not found. Pass --core-dir, set NEBULA_CORE_DIR, "
                "or place the checkout next to this installer."
            )
            sys.exit(2)
        return self.core

    def require_panel(self) -> Path:
        if not self.panel_ready:
            ui.error(
                "Nebula Panel was not found. Pass --panel-dir, set NEBULA_PANEL_DIR, "
                "or place the checkout next to this installer."
            )
            sys.exit(2)
        return self.panel


def panel_origins(host: str, port: int) -> str:
    origins = [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]
    if host not in {"0.0.0.0", "127.0.0.1", "localhost", ""}:
        origins.append(f"http://{host}:{port}")
    return ",".join(origins)


def prepare_core(core: Path, core_host: str, core_port: int, origins: str) -> dict[str, str]:
    ui.step("Preparing the Core virtual environment")
    ui.result(*components.ensure_core_virtualenv(core))

    ui.step("Installing Core dependencies")
    ok, message = components.install_core_dependencies(core)
    if not ok:
        ui.error(message)
        sys.exit(1)
    ui.ok("Core dependencies installed")

    ui.step("Writing the Core environment file")
    values = env_module.ensure_core_env(
        core, core_host=core_host, core_port=core_port, panel_origins=origins
    )
    ui.ok(f"Environment file ready: {core / '.env'}")
    return values


def prepare_panel(
    panel: Path,
    core_values: dict[str, str],
    core_url: str,
    panel_host: str,
    panel_port: int,
) -> None:
    ui.step("Writing the Panel environment file")
    env_module.ensure_panel_env(
        panel,
        core_values=core_values,
        core_url=core_url,
        panel_host=panel_host,
        panel_port=panel_port,
    )
    ui.ok(f"Environment file ready: {panel / '.env'}")

    node = components.node_version()
    if not node:
        ui.warn("Node.js was not found. Skipping the Panel build.")
        return
    ui.ok(f"Node.js detected: {node}")

    ui.step("Building the Panel")
    ok, message = components.build_panel(panel)
    ui.result(ok, "Panel build completed" if ok else f"Panel build failed: {message}")


def ensure_docker() -> None:
    ui.step("Checking Docker")
    installed = docker_setup.is_installed()
    ready, info = docker_setup.daemon_status() if installed else (False, "not installed")
    if ready:
        ui.ok("Docker is installed and responding")
        return

    if not installed:
        ui.warn("Docker is not installed.")
        if ui.ask_yes_no("Install Docker automatically now?", default=True):
            ui.result(*docker_setup.install(paths.installer_dir()))

    ready, info = docker_setup.daemon_status()
    if ready:
        ui.ok("Docker is ready")
        return

    if "permission denied" in info.lower():
        if ui.ask_yes_no("Add the current user to the docker group?", default=True):
            ui.result(*docker_setup.add_user_to_group())
        return

    if ui.ask_yes_no("Start and enable the Docker service now?", default=True):
        ui.result(*docker_setup.start_daemon())
        ready, _ = docker_setup.daemon_status()

    if not ready:
        ui.warn("Docker is still unavailable. Nebula will install, but containers will not run.")


def prompt_admin_credentials() -> tuple[str, str]:
    while True:
        username = ui.ask("Admin username", "nebula_admin")
        if len(username) < 5 or not username.replace("_", "").isalnum():
            ui.warn("Username must be at least 5 characters: letters, digits, and underscores.")
            continue
        break

    while True:
        password = getpass.getpass("Admin password: ").strip()
        confirm = getpass.getpass("Repeat password: ").strip()
        if len(password) < 12:
            ui.warn("Password must be at least 12 characters.")
            continue
        if password != confirm:
            ui.warn("Passwords do not match.")
            continue
        break

    return username, password


def bootstrap_admin(core: Path, core_host: str, core_port: int, token: str) -> None:
    if core_api.admin_exists(paths.core_database(core)):
        ui.ok("An administrator already exists. Skipping.")
        return

    username, password = prompt_admin_credentials()
    ok, message = core_api.create_admin(core_host, core_port, username, password, token)
    if not ok:
        ui.error(message)
        sys.exit(1)
    ui.ok(message)


def install_services(
    core: Path,
    panel: Path,
    core_service: str,
    panel_service: str,
    env_mode: str,
) -> tuple[bool, bool]:
    if not systemd_available():
        ui.warn("systemd is not available. Skipping service installation.")
        return False, False

    ui.step(f"Installing the Core service as user '{default_core_run_user()}'")
    core_ok, message = install_core_service(core, service_name=core_service, env_mode=env_mode)
    ui.result(core_ok, message)

    ui.step(f"Installing the Panel service as user '{default_panel_run_user()}'")
    panel_ok, message = install_panel_service(panel, service_name=panel_service, env_mode=env_mode)
    ui.result(panel_ok, message)

    return core_ok, panel_ok


def full_install(deployment: Deployment, args: argparse.Namespace) -> None:
    ui.banner(VERSION)
    print("This wizard provisions Nebula Core and Nebula Panel on a single Linux host.\n")
    deployment.describe()
    print()

    core = deployment.require_core()
    panel = deployment.require_panel()

    origins = panel_origins(args.panel_host, args.panel_port)
    core_url = f"http://{args.core_host}:{args.core_port}"

    core_values = prepare_core(core, args.core_host, args.core_port, origins)
    prepare_panel(panel, core_values, core_url, args.panel_host, args.panel_port)
    ensure_docker()

    core_running = False
    if ui.ask_yes_no("Install Nebula as systemd services and start them on boot?", default=True):
        core_ok, panel_ok = install_services(
            core, panel, args.core_service_name, args.panel_service_name, args.env_mode
        )
        if core_ok:
            ok, message = service_action(args.core_service_name, "restart")
            ui.result(ok, message or f"Core service started: {args.core_service_name}")
            core_running = ok
        if panel_ok:
            ok, message = service_action(args.panel_service_name, "restart")
            ui.result(ok, message or f"Panel service started: {args.panel_service_name}")

    if core_running:
        ui.step("Waiting for Nebula Core to come online")
        if not core_api.wait_until_ready(args.core_host, args.core_port):
            ui.error("Nebula Core did not become ready in time. Check its logs.")
            sys.exit(1)
        ui.ok("Nebula Core is online")
        bootstrap_admin(core, args.core_host, args.core_port, core_values["NEBULA_INSTALLER_TOKEN"])
    else:
        ui.warn("Core is not running as a service, so the first admin was not created.")
        print("Start Core, then run:  ./nebulactl.sh create-admin")

    print()
    ui.rule()
    ui.ok("Installation complete.")
    print(f"  Panel : http://{args.panel_host}:{args.panel_port}")
    print(f"  Core  : {core_url}")
    print("\nManage the deployment with:")
    print("  ./nebulactl.sh status | restart | logs")


def create_admin_only(deployment: Deployment, args: argparse.Namespace) -> None:
    ui.banner(VERSION)
    core = deployment.require_core()
    token = env_module.read_env_file(core / ".env").get("NEBULA_INSTALLER_TOKEN", "")
    if not token:
        ui.error(f"NEBULA_INSTALLER_TOKEN is not set in {core / '.env'}")
        sys.exit(1)

    if not core_api.wait_until_ready(args.core_host, args.core_port, timeout=10):
        ui.error("Nebula Core is offline. Start it first.")
        sys.exit(1)

    bootstrap_admin(core, args.core_host, args.core_port, token)


def fetch_components(deployment: Deployment, args: argparse.Namespace) -> None:
    ui.banner(VERSION)
    root = paths.deployment_root()
    ui.step(f"Fetching missing components into {root}")

    if deployment.core_ready:
        ui.ok(f"Core already present: {deployment.core}")
    else:
        ui.result(*components.clone_core(root / "Nebula-Core", args.branch))
        deployment.core = paths.core_dir()

    if deployment.panel_ready:
        ui.ok(f"Panel already present: {deployment.panel}")
    else:
        ui.result(*components.clone_panel(root / "Nebula-Panel", args.branch))
        deployment.panel = paths.panel_dir()


def show_status(deployment: Deployment, args: argparse.Namespace) -> None:
    ui.banner(VERSION)
    print(" Components")
    ui.rule()
    deployment.describe()

    print("\n Host")
    ui.rule()
    docker_ok, _ = docker_setup.daemon_status()
    print(f" Docker  : {'ready' if docker_ok else 'unavailable'}")
    print(f" Node.js : {components.node_version() or 'not found'}")
    print(f" systemd : {'available' if systemd_available() else 'unavailable'}")

    print("\n Services")
    ui.rule()
    for service in (args.core_service_name, args.panel_service_name):
        active, _ = service_action(service, "status")
        print(f" {service:<16}: {'active' if active else 'inactive'}")

    if deployment.core_ready:
        online = core_api.wait_until_ready(args.core_host, args.core_port, timeout=2)
        admin = core_api.admin_exists(paths.core_database(deployment.core))
        print("\n Core API")
        ui.rule()
        print(f" Endpoint      : http://{args.core_host}:{args.core_port}")
        print(f" Reachable     : {'yes' if online else 'no'}")
        print(f" Administrator : {'configured' if admin else 'missing'}")


def manage_services_interactive(args: argparse.Namespace) -> None:
    print("\n=== Service control ===")
    if not systemd_available():
        ui.warn("systemd is not available on this host.")
        return

    target = ui.ask("Target [all/core/panel]", "all").lower()
    print(" [1] start   [2] stop   [3] restart   [4] status   [5] logs   [0] back")
    action = {"1": "start", "2": "stop", "3": "restart", "4": "status", "5": "logs"}.get(
        input("Select >> ").strip()
    )
    if not action:
        return

    for service in resolve_services(target, args):
        ok, output = service_action(service, action, lines=args.log_lines)
        print(f"\n--- {service} ---")
        print(output or ("done" if ok else "failed"))


def resolve_services(target: str, args: argparse.Namespace) -> list[str]:
    if target == "core":
        return [args.core_service_name]
    if target == "panel":
        return [args.panel_service_name]
    return [args.core_service_name, args.panel_service_name]


def run_interactive(deployment: Deployment, args: argparse.Namespace) -> None:
    while True:
        ui.banner(VERSION)
        deployment.describe()
        ui.rule()
        print(" [1] Full install (Core + Panel + services)")
        print(" [2] Fetch missing components from Git")
        print(" [3] Create the first administrator")
        print(" [4] Install / start Docker")
        print(" [5] Install or update systemd services")
        print(" [6] Manage services")
        print(" [7] Rebuild the Panel")
        print(" [8] Show deployment status")
        print(" [0] Exit")
        ui.rule()

        choice = input("SELECT >> ").strip()
        if choice == "1":
            full_install(deployment, args)
        elif choice == "2":
            fetch_components(deployment, args)
        elif choice == "3":
            create_admin_only(deployment, args)
        elif choice == "4":
            ensure_docker()
        elif choice == "5":
            install_services(
                deployment.require_core(),
                deployment.require_panel(),
                args.core_service_name,
                args.panel_service_name,
                args.env_mode,
            )
        elif choice == "6":
            manage_services_interactive(args)
        elif choice == "7":
            ui.result(*components.build_panel(deployment.require_panel()))
        elif choice == "8":
            show_status(deployment, args)
        elif choice == "0":
            return
        else:
            continue
        ui.pause()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nebula-installer",
        description="Install and manage a Nebula deployment (Core + Panel).",
    )
    parser.add_argument("--version", action="version", version=f"nebula-installer {VERSION}")

    location = parser.add_argument_group("component locations")
    location.add_argument("--core-dir", default="", help="path to the Nebula Core checkout")
    location.add_argument("--panel-dir", default="", help="path to the Nebula Panel checkout")

    network = parser.add_argument_group("network")
    network.add_argument("--core-host", default=DEFAULT_CORE_HOST)
    network.add_argument("--core-port", type=int, default=DEFAULT_CORE_PORT)
    network.add_argument("--panel-host", default=DEFAULT_PANEL_HOST)
    network.add_argument("--panel-port", type=int, default=DEFAULT_PANEL_PORT)

    services = parser.add_argument_group("services")
    services.add_argument("--core-service-name", default=DEFAULT_CORE_SERVICE)
    services.add_argument("--panel-service-name", default=DEFAULT_PANEL_SERVICE)
    services.add_argument("--env-mode", default="production")
    services.add_argument("--log-lines", type=int, default=120)

    actions = parser.add_argument_group("actions")
    actions.add_argument("--install", action="store_true", help="run the full guided install")
    actions.add_argument("--fetch", action="store_true", help="clone missing components")
    actions.add_argument("--branch", default="", help="branch to clone with --fetch")
    actions.add_argument("--create-admin", action="store_true", help="create the first admin")
    actions.add_argument("--build-panel", action="store_true", help="rebuild the panel bundle")
    actions.add_argument("--status", action="store_true", help="print deployment status")
    actions.add_argument("--check", action="store_true", help="exit 0 when the install is complete")
    actions.add_argument("--install-services", action="store_true", help="install systemd units")
    actions.add_argument(
        "--service-action",
        choices=["start", "stop", "restart", "status", "logs", "enable", "disable"],
        default="",
    )
    actions.add_argument("--service-target", choices=["all", "core", "panel"], default="all")
    return parser


def check_install(deployment: Deployment) -> int:
    if not deployment.core_ready or not deployment.panel_ready:
        return 2
    if not (deployment.core / ".env").exists():
        return 2
    if not paths.core_database(deployment.core).exists():
        return 2
    return 0


def main() -> None:
    args = build_parser().parse_args()
    deployment = Deployment(args.core_dir, args.panel_dir)

    if args.check:
        sys.exit(check_install(deployment))

    if args.fetch:
        fetch_components(deployment, args)
        return

    if args.install:
        full_install(deployment, args)
        return

    if args.create_admin:
        create_admin_only(deployment, args)
        return

    if args.build_panel:
        ok, message = components.build_panel(deployment.require_panel())
        ui.result(ok, message)
        sys.exit(0 if ok else 1)

    if args.install_services:
        core_ok, panel_ok = install_services(
            deployment.require_core(),
            deployment.require_panel(),
            args.core_service_name,
            args.panel_service_name,
            args.env_mode,
        )
        sys.exit(0 if core_ok and panel_ok else 1)

    if args.service_action:
        failed = False
        for service in resolve_services(args.service_target, args):
            ok, output = service_action(service, args.service_action, lines=args.log_lines)
            print(f"--- {service} ---")
            print(output or ("done" if ok else "failed"))
            failed = failed or not ok
        sys.exit(1 if failed else 0)

    if args.status:
        show_status(deployment, args)
        return

    if node_binary() is None:
        ui.warn("Node.js was not found on PATH. The Panel cannot be built or started without it.")

    run_interactive(deployment, args)


if __name__ == "__main__":
    main()
