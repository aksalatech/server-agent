#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/opt/server-monitor-agent"
CONFIG_DIR="/etc/server-monitor-agent"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${EUID:-0}" -ne 0 ]]; then
  echo "Jalankan script ini sebagai root (sudo)." >&2
  exit 1
fi

echo "==> Installing Server Monitor Agent to ${INSTALL_DIR}"

apt-get update -qq || yum makecache -q || true

if command -v apt-get >/dev/null 2>&1; then
  apt-get install -y python3 python3-venv python3-pip
elif command -v yum >/dev/null 2>&1; then
  yum install -y python3 python3-pip
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y python3 python3-pip
else
  echo "Package manager tidak dikenali. Pastikan python3 dan venv tersedia." >&2
fi

mkdir -p "${INSTALL_DIR}"
mkdir -p "${CONFIG_DIR}"

rsync -a --delete \
  --exclude venv \
  --exclude __pycache__ \
  "${SCRIPT_DIR}/" "${INSTALL_DIR}/"

python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install --upgrade pip
"${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

if [[ ! -f "${CONFIG_DIR}/config.yaml" ]]; then
  cp "${INSTALL_DIR}/config.example.yaml" "${CONFIG_DIR}/config.yaml"
  echo "==> Config dibuat di ${CONFIG_DIR}/config.yaml (edit server_url sebelum register)"
fi

cp "${INSTALL_DIR}/server-monitor-agent.service" /etc/systemd/system/server-monitor-agent.service
systemctl daemon-reload

echo ""
echo "Instalasi selesai."
echo ""
echo "Langkah berikutnya — jalankan setup (disarankan):"
echo "  sudo bash setup.sh --server-url https://YOUR_SERVER --token YOUR_REGISTRATION_TOKEN"
echo ""
echo "Token didapat dari dashboard → Agents → Tambah Agent."
