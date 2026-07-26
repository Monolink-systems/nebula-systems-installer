# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
"""Console output helpers shared by the interactive and non-interactive flows."""
import os

WIDTH = 72


def clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def banner(version: str) -> None:
    clear()
    print("=" * WIDTH)
    print("NEBULA SYSTEMS INSTALLER".center(WIDTH))
    print(f"v{version}".center(WIDTH))
    print("=" * WIDTH)


def rule() -> None:
    print("-" * WIDTH)


def step(message: str) -> None:
    print(f"\n[>] {message}")


def ok(message: str) -> None:
    print(f"[OK] {message}")


def warn(message: str) -> None:
    print(f"[WARN] {message}")


def error(message: str) -> None:
    print(f"[ERROR] {message}")


def result(success: bool, message: str) -> None:
    (ok if success else error)(message)


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    raw = input(prompt + suffix).strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}


def ask(prompt: str, default: str = "") -> str:
    label = f"{prompt} [{default}]: " if default else f"{prompt}: "
    return input(label).strip() or default


def pause(message: str = "Press Enter to continue...") -> None:
    input(f"\n{message}")
