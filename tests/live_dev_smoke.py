"""Opt-in live smoke test against fresh Core and Panel GitHub checkouts.

Usage:
    NEBULA_TEST_UV=/path/to/uv python3 tests/live_dev_smoke.py
"""

from __future__ import annotations

import http.cookiejar
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules import components, core_api, database
from modules import env as env_module
from modules.config import DeploymentConfig
from modules.runner import Runner


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def stop(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass


def verify_container_endpoint(port: int, username: str, password: str) -> None:
    cookies = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cookies)
    )
    login_data = urllib.parse.urlencode(
        {"username": username, "password": password}
    ).encode("utf-8")
    login_request = urllib.request.Request(
        f"http://127.0.0.1:{port}/users/login?db_name=system.db",
        data=login_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with opener.open(login_request, timeout=8) as response:
            if response.status != 200:
                raise RuntimeError(f"Core login returned HTTP {response.status}")
        with opener.open(
            f"http://127.0.0.1:{port}/containers/list", timeout=15
        ) as response:
            if response.status != 200:
                raise RuntimeError(
                    f"Core container endpoint returned HTTP {response.status}"
                )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Core container integration failed with HTTP {exc.code}: {detail}"
        ) from exc


def main() -> int:
    uv = os.environ.get("NEBULA_TEST_UV", "")
    if not uv or not Path(uv).exists():
        raise SystemExit("Set NEBULA_TEST_UV to an executable uv binary")

    with tempfile.TemporaryDirectory(prefix="nebula-live-dev-") as directory:
        config = DeploymentConfig.create("dev", root=directory)
        config.core_port = free_port()
        config.panel_port = free_port()
        runner = Runner()

        for message in components.sync_sources(config, runner):
            print(message)
        ok, detail = components.validate_versions(config.core_path, config.panel_path)
        if not ok:
            raise RuntimeError(detail)
        components.prepare_core(config, runner, uv)
        values = env_module.configure_environment(config, runner)
        env_module.configure_core_profile(config, runner)
        env_module.prepare_storage(config, runner)
        database.ensure_system_database(config, runner)
        components.prepare_panel(config, runner)

        log_dir = Path(directory) / "smoke-logs"
        log_dir.mkdir()
        core_log = (log_dir / "core.log").open("wb")
        panel_log = (log_dir / "panel.log").open("wb")
        core_process: subprocess.Popen[bytes] | None = None
        panel_process: subprocess.Popen[bytes] | None = None
        succeeded = False
        try:
            core_process = subprocess.Popen(
                [str(config.core_path / ".venv/bin/python"), "-m", "nebula_core"],
                cwd=config.core_path,
                env={
                    **os.environ,
                    **values,
                    "NEBULA_CONFIG_PATH": str(config.core_path / "serviceconfig.yaml"),
                },
                stdout=core_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            if not core_api.wait_until_ready(
                config.core_host, config.core_port, timeout=60
            ):
                raise RuntimeError("Fresh Core checkout did not become ready")
            ok, detail = core_api.create_admin(
                config.core_host,
                config.core_port,
                "smoke_admin",
                "smoke-test-password-2026",
                values["NEBULA_INSTALLER_TOKEN"],
            )
            if not ok:
                raise RuntimeError(detail)
            verify_container_endpoint(
                config.core_port,
                "smoke_admin",
                "smoke-test-password-2026",
            )

            panel_process = subprocess.Popen(
                [
                    "npm",
                    "run",
                    "dev",
                    "--",
                    "--host",
                    config.panel_host,
                    "--port",
                    str(config.panel_port),
                ],
                cwd=config.panel_path,
                stdout=panel_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            deadline = time.time() + 60
            while time.time() < deadline:
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{config.panel_port}/login", timeout=3
                    ) as response:
                        if response.status == 200:
                            succeeded = True
                            print(
                                f"LIVE_SMOKE_OK core={config.core_port} "
                                f"panel={config.panel_port} admin=created "
                                "containers=ok"
                            )
                            return 0
                except OSError:
                    time.sleep(1)
            raise RuntimeError("Fresh Panel checkout did not render /login")
        finally:
            stop(panel_process)
            stop(core_process)
            core_log.close()
            panel_log.close()
            if not succeeded:
                for name in ("core.log", "panel.log"):
                    path = log_dir / name
                    print(f"\n--- {name} ---")
                    print(
                        "\n".join(
                            path.read_text(
                                encoding="utf-8", errors="replace"
                            ).splitlines()[-80:]
                        )
                    )


if __name__ == "__main__":
    raise SystemExit(main())
