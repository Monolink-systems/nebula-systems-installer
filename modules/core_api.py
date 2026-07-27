# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
"""Minimal HTTP client for the Core endpoints the installer needs.

Deliberately built on the standard library: the installer must be able to run
before any dependency has been installed.
"""

import json
import socket
import time
import urllib.error
import urllib.request


def _socket_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def request_json(
    method: str,
    url: str,
    *,
    payload: dict | None = None,
    headers: dict | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict, str]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)

    request = urllib.request.Request(
        url, data=body, headers=request_headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, (json.loads(raw) if raw else {}), raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {"detail": raw}
        return exc.code, data, raw
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, {"detail": str(exc)}, str(exc)


def wait_until_ready(host: str, port: int, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _socket_open(host, port):
            time.sleep(1)
            continue
        try:
            status, data, _ = request_json(
                "GET", f"http://{host}:{port}/system/status", timeout=2
            )
            if status == 200 and isinstance(data, dict) and data.get("status") == "ok":
                return True
        except OSError:
            pass
        time.sleep(1)
    return False


def create_admin(
    host: str, port: int, username: str, password: str, installer_token: str
) -> tuple[bool, str]:
    status, data, raw = request_json(
        "POST",
        f"http://{host}:{port}/system/internal/core/init-admin",
        payload={"username": username, "password": password},
        headers={"X-Nebula-Token": installer_token},
        timeout=8,
    )
    if status in {200, 201}:
        return True, "Administrator account created"
    if status == 409:
        return True, "Administrator already exists"
    detail = data.get("detail") if isinstance(data, dict) else raw
    return False, str(detail or "Core rejected the admin setup request")


def admin_count(host: str, port: int, installer_token: str) -> int | None:
    status, data, _ = request_json(
        "GET",
        f"http://{host}:{port}/system/internal/core/status",
        headers={"X-Nebula-Token": installer_token},
        timeout=5,
    )
    if status != 200 or not isinstance(data, dict):
        return None
    try:
        return int(data.get("active_admins", 0))
    except (TypeError, ValueError):
        return None
