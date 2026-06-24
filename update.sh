#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/opt/server-monitor-agent"
BRANCH="${AGENT_UPDATE_BRANCH:-main}"

cd "${INSTALL_DIR}"
exec "${INSTALL_DIR}/venv/bin/python" -m server_monitor_agent update --branch "${BRANCH}"
