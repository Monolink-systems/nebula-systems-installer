#!/usr/bin/env bash
# Nebula one-command bootstrap for Ubuntu.
set -Eeuo pipefail

INSTALLER_REPO="https://github.com/Monolink-systems/nebula-systems-installer.git"
SCRIPT_DIR=""
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "Nebula Installer supports Linux hosts with systemd." >&2
  exit 1
fi

if [[ ! -r /etc/os-release ]]; then
  echo "Cannot identify this Linux distribution." >&2
  exit 1
fi

# One authentication prompt up front. The Python installer refreshes this lease
# while downloads and builds are running.
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  if ! command -v sudo >/dev/null 2>&1; then
    echo "sudo is required. Install it or run this bootstrap as root." >&2
    exit 1
  fi
  if [[ -r /dev/tty ]]; then
    sudo -v </dev/tty
  else
    sudo -n true
  fi
fi

sudo_cmd=()
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  sudo_cmd=(sudo)
fi

if ! command -v python3 >/dev/null 2>&1 \
  || ! command -v git >/dev/null 2>&1 \
  || ! command -v curl >/dev/null 2>&1; then
  "${sudo_cmd[@]}" apt-get -o DPkg::Lock::Timeout=120 -o Acquire::Retries=3 update
  "${sudo_cmd[@]}" env DEBIAN_FRONTEND=noninteractive \
    apt-get -o DPkg::Lock::Timeout=120 -o Acquire::Retries=3 install -y \
    ca-certificates curl git python3
fi

if [[ -n "$SCRIPT_DIR" && -f "$SCRIPT_DIR/main.py" ]]; then
  installer_dir="$SCRIPT_DIR"
else
  cache_root="${XDG_CACHE_HOME:-$HOME/.cache}/nebula-installer"
  if [[ -d "$cache_root/.git" ]]; then
    if ! git -C "$cache_root" pull --ff-only; then
      cache_root="$(mktemp -d /tmp/nebula-installer.XXXXXX)"
      git clone --depth 1 "$INSTALLER_REPO" "$cache_root"
    fi
  else
    mkdir -p "$(dirname "$cache_root")"
    git clone --depth 1 "$INSTALLER_REPO" "$cache_root"
  fi
  installer_dir="$cache_root"
fi

python_command=(python3)
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  uv_bootstrap_dir="${XDG_CACHE_HOME:-$HOME/.cache}/nebula-installer/uv"
  if [[ ! -x "$uv_bootstrap_dir/uv" ]]; then
    mkdir -p "$uv_bootstrap_dir"
    curl --proto '=https' --tlsv1.2 -LsSf https://astral.sh/uv/install.sh \
      | env UV_UNMANAGED_INSTALL="$uv_bootstrap_dir" UV_NO_MODIFY_PATH=1 sh
  fi
  python_command=("$uv_bootstrap_dir/uv" run --no-project --python 3.11 python)
fi

if [[ -r /dev/tty ]]; then
  exec "${python_command[@]}" "$installer_dir/main.py" install "$@" </dev/tty
fi
exec "${python_command[@]}" "$installer_dir/main.py" install "$@"
