# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
"""Dependency-free terminal UI with a quiet non-TTY fallback."""

from __future__ import annotations

import os
import sys

COLOR = sys.stdout.isatty() and os.getenv("NO_COLOR") is None


def _paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if COLOR else text


def banner(version: str) -> None:
    print()
    print(_paint("1;38;5;81", "NEBULA SYSTEMS INSTALLER"))
    print(f"Core + Panel | Version {version}")


def section(title: str) -> None:
    print()
    print(_paint("1;38;5;81", title))


def step(message: str) -> None:
    print(f"\n{_paint('1;38;5;81', '[STEP]')} {_paint('1', message)}")


def ok(message: str) -> None:
    print(f"{_paint('32', '[OK]')} {message}")


def warn(message: str) -> None:
    print(f"{_paint('33', '[WARNING]')} {message}")


def error(message: str) -> None:
    print(f"{_paint('31', '[ERROR]')} {message}", file=sys.stderr)


def info(message: str) -> None:
    print(f"{_paint('36', '[INFO]')} {message}")


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    while True:
        raw = input(prompt + suffix).strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        warn("Enter yes or no.")


def ask(prompt: str, default: str = "") -> str:
    label = f"{prompt} [{default}]: " if default else f"{prompt}: "
    return input(label).strip() or default


def choose_mode() -> str:
    print()
    print(
        f"  {_paint('1;38;5;81', '1')}  Developer  - local services, HMR, reduced security"
    )
    print(
        f"  {_paint('1;38;5;81', '2')}  Production - domains, HTTPS, hardening, backups"
    )
    while True:
        answer = input("\nInstallation mode [1]: ").strip().lower() or "1"
        if answer in {"1", "dev", "developer"}:
            return "dev"
        if answer in {"2", "prod", "production"}:
            return "prod"
        warn("Enter 1 for Developer or 2 for Production.")
