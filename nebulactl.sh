#!/usr/bin/env bash
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${NEBULA_PYTHON:-python3}"

CORE_SERVICE="${NEBULA_CORE_SERVICE:-nebula-core}"
PANEL_SERVICE="${NEBULA_PANEL_SERVICE:-nebula-panel}"
LOG_LINES="${NEBULA_LOG_LINES:-120}"

usage() {
  cat <<'USAGE'
Usage: ./nebulactl.sh <command> [target]

Setup
  install            Run the guided installer (Core + Panel + services)
  fetch              Clone any missing component repositories
  create-admin       Create the first administrator account
  build-panel        Reinstall panel dependencies and rebuild the bundle
  install-services   Install or update the systemd units

Operations
  start [target]     Start services
  stop [target]      Stop services
  restart [target]   Restart services
  status             Show deployment status (components, host, services)
  logs [target]      Tail recent service logs
  check              Exit 0 when the deployment looks complete

  target: all (default) | core | panel

Environment overrides
  NEBULA_CORE_DIR       Path to the Nebula Core checkout
  NEBULA_PANEL_DIR      Path to the Nebula Panel checkout
  NEBULA_ROOT_DIR       Deployment root used when fetching (default /opt/nebula)
  NEBULA_CORE_SERVICE   Core systemd unit name    (default nebula-core)
  NEBULA_PANEL_SERVICE  Panel systemd unit name   (default nebula-panel)
  NEBULA_LOG_LINES      Lines shown by `logs`     (default 120)
  NEBULA_PYTHON         Python interpreter        (default python3)
USAGE
}

run_installer() {
  "$PYTHON" "$SCRIPT_DIR/main.py" \
    --core-service-name "$CORE_SERVICE" \
    --panel-service-name "$PANEL_SERVICE" \
    "$@"
}

command="${1:-}"
target="${2:-all}"

case "$command" in
  install)          run_installer --install ;;
  fetch)            run_installer --fetch ;;
  create-admin)     run_installer --create-admin ;;
  build-panel)      run_installer --build-panel ;;
  install-services) run_installer --install-services ;;
  status)           run_installer --status ;;
  check)            run_installer --check ;;
  start|stop|restart)
    run_installer --service-action "$command" --service-target "$target"
    ;;
  logs)
    run_installer --service-action logs --service-target "$target" --log-lines "$LOG_LINES"
    ;;
  menu|"")
    run_installer
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage
    exit 2
    ;;
esac
