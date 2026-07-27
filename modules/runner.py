# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
"""Small, auditable command runner used by the installer.

All privileged changes go through this module.  The installer asks for sudo once
and keeps that credential warm for the duration of the run.
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path


class CommandError(RuntimeError):
    """A host command failed."""


class Runner:
    def __init__(self, *, dry_run: bool = False, verbose: bool = False) -> None:
        self.dry_run = dry_run
        self.verbose = verbose
        self._sudo_stop = threading.Event()
        self._sudo_thread: threading.Thread | None = None

    @property
    def is_root(self) -> bool:
        return os.geteuid() == 0

    def _display(self, command: Sequence[str]) -> None:
        if self.verbose:
            print("  $ " + " ".join(str(item) for item in command))

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        capture: bool = True,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        cmd = [str(item) for item in command]
        self._display(cmd)
        if self.dry_run:
            return subprocess.CompletedProcess(cmd, 0, "", "")

        merged_env = os.environ.copy()
        if env:
            merged_env.update({str(key): str(value) for key, value in env.items()})
        result = subprocess.run(
            cmd,
            cwd=cwd,
            env=merged_env,
            text=True,
            check=False,
            input=input_text,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
        if check and result.returncode != 0:
            output = ((result.stdout or "") + (result.stderr or "")).strip()
            tail = "\n".join(output.splitlines()[-30:])
            raise CommandError(
                f"Command failed ({result.returncode}): {' '.join(cmd)}\n{tail}"
            )
        return result

    def sudo(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        capture: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        cmd = [str(item) for item in command]
        if not self.is_root:
            cmd = ["sudo", *cmd]
        return self.run(cmd, cwd=cwd, env=env, check=check, capture=capture)

    def as_user(
        self,
        username: str,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        capture: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        if self.is_root:
            cmd = ["runuser", "-u", username, "--", *map(str, command)]
        else:
            cmd = ["sudo", "-u", username, *map(str, command)]
        return self.run(cmd, cwd=cwd, env=env, check=check, capture=capture)

    def require_privileges(self) -> None:
        if self.is_root:
            return
        if not shutil.which("sudo"):
            raise CommandError(
                "sudo is required for host package and service installation"
            )
        self.run(["sudo", "-v"], capture=False)
        if self._sudo_thread:
            return

        def keepalive() -> None:
            while not self._sudo_stop.wait(45):
                subprocess.run(
                    ["sudo", "-n", "true"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )

        self._sudo_thread = threading.Thread(target=keepalive, daemon=True)
        self._sudo_thread.start()
        atexit.register(self.close)

    def close(self) -> None:
        self._sudo_stop.set()

    def install_text(
        self,
        destination: Path,
        content: str,
        *,
        mode: int = 0o644,
        owner: str = "root",
        group: str = "root",
    ) -> None:
        # Keep the destination itself as the replacement target. resolve()
        # would follow a pre-existing symlink and overwrite its target.
        destination = destination.expanduser().absolute()
        destination.parent.mkdir(parents=True, exist_ok=True) if self.is_root else None
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(content)
            temporary = Path(handle.name)
        try:
            self.sudo(["mkdir", "-p", str(destination.parent)])
            self.sudo(
                [
                    "install",
                    "-o",
                    owner,
                    "-g",
                    group,
                    "-m",
                    f"{mode:o}",
                    str(temporary),
                    str(destination),
                ]
            )
        finally:
            temporary.unlink(missing_ok=True)

    def ensure_directory(
        self,
        path: Path,
        *,
        owner: str,
        group: str,
        mode: int = 0o755,
        privileged: bool = True,
    ) -> None:
        if privileged:
            self.sudo(
                [
                    "install",
                    "-d",
                    "-o",
                    owner,
                    "-g",
                    group,
                    "-m",
                    f"{mode:o}",
                    str(path),
                ]
            )
            return
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(mode)
