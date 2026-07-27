# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
"""Deployment profiles and the non-secret installer state file."""

from __future__ import annotations

import getpass
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .runner import Runner

MODE_DEV = "dev"
MODE_PROD = "prod"
VALID_MODES = {MODE_DEV, MODE_PROD}
DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)


def invoking_user() -> str:
    return (os.getenv("SUDO_USER") or os.getenv("USER") or getpass.getuser()).strip()


def invoking_home() -> Path:
    sudo_user = (os.getenv("SUDO_USER") or "").strip()
    if sudo_user:
        try:
            import pwd

            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except (ImportError, KeyError):
            pass
    return Path.home()


def normalize_domain(value: str) -> str:
    domain = value.strip().lower().rstrip(".")
    if domain and not DOMAIN_RE.fullmatch(domain):
        raise ValueError(f"Invalid public domain: {value!r}")
    return domain


@dataclass
class DeploymentConfig:
    mode: str
    root: str
    core_dir: str
    panel_dir: str
    installer_dir: str
    core_service: str = "nebula-core"
    panel_service: str = "nebula-panel"
    core_host: str = "127.0.0.1"
    core_port: int = 8000
    panel_host: str = "127.0.0.1"
    panel_port: int = 5173
    panel_domain: str = ""
    core_domain: str = ""
    core_user: str = ""
    panel_user: str = ""
    shared_group: str = ""
    installed_version: str = ""

    @classmethod
    def create(
        cls,
        mode: str,
        *,
        root: str = "",
        panel_domain: str = "",
        core_domain: str = "",
    ) -> DeploymentConfig:
        if mode not in VALID_MODES:
            raise ValueError(f"Unknown installation mode: {mode}")

        if mode == MODE_PROD:
            base = Path(root or "/opt/nebula").expanduser().resolve()
            core_user = "nebula-core"
            panel_user = "nebula-panel"
            shared_group = "nebula"
            panel_port = 3000
        else:
            base = (
                Path(root or invoking_home() / ".local/share/nebula")
                .expanduser()
                .resolve()
            )
            core_user = invoking_user()
            panel_user = invoking_user()
            shared_group = ""
            panel_port = 5173
        if any(character.isspace() for character in str(base)):
            raise ValueError("Nebula deployment root cannot contain whitespace")

        return cls(
            mode=mode,
            root=str(base),
            core_dir=str(base / "core"),
            panel_dir=str(base / "panel"),
            installer_dir=str(base / "installer"),
            panel_port=panel_port,
            panel_domain=normalize_domain(panel_domain),
            core_domain=normalize_domain(core_domain),
            core_user=core_user,
            panel_user=panel_user,
            shared_group=shared_group,
        )

    @property
    def root_path(self) -> Path:
        return Path(self.root)

    @property
    def core_path(self) -> Path:
        return Path(self.core_dir)

    @property
    def panel_path(self) -> Path:
        return Path(self.panel_dir)

    @property
    def core_env_path(self) -> Path:
        return self.root_path / "config/core.env"

    @property
    def installer_path(self) -> Path:
        return Path(self.installer_dir)

    @property
    def panel_url(self) -> str:
        if self.mode == MODE_PROD and self.panel_domain:
            return f"https://{self.panel_domain}"
        return f"http://127.0.0.1:{self.panel_port}"

    @property
    def public_core_url(self) -> str:
        if self.mode == MODE_PROD and self.core_domain:
            return f"https://{self.core_domain}"
        return f"http://127.0.0.1:{self.core_port}"

    @property
    def panel_core_url(self) -> str:
        # On a single host the panel should never send the root-equivalent
        # internal token over the public network.
        return f"http://127.0.0.1:{self.core_port}"

    @property
    def state_path(self) -> Path:
        if self.mode == MODE_PROD:
            return Path("/etc/nebula/installer.json")
        return self.root_path / "installer.json"

    def save(self, runner: Runner) -> None:
        payload = (
            json.dumps(asdict(self), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        )
        if self.mode == MODE_PROD:
            runner.install_text(self.state_path, payload, mode=0o644)
        else:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(payload, encoding="utf-8")
            self.state_path.chmod(0o600)

    @classmethod
    def load(cls, explicit: str = "") -> DeploymentConfig | None:
        candidates: list[Path] = []
        if explicit:
            candidates.append(Path(explicit).expanduser())
        configured = (os.getenv("NEBULA_STATE_FILE") or "").strip()
        if configured:
            candidates.append(Path(configured).expanduser())
        candidates.extend(
            [
                Path("/etc/nebula/installer.json"),
                invoking_home() / ".local/share/nebula/installer.json",
            ]
        )
        for candidate in candidates:
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                return cls(**data)
            except (OSError, ValueError, TypeError):
                continue
        return None
