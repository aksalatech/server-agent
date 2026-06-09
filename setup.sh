#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-git@github-aksala:aksalatech/server-agent.git}"
INSTALL_DIR="/opt/server-monitor-agent"
CONFIG_DIR="/etc/server-monitor-agent"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLONE_DIR=""

SERVER_URL=""
TOKEN=""
NO_DETECT=false

usage() {
  cat <<'EOF'
Usage: sudo bash setup.sh --server-url URL --token TOKEN [options]

Setup lengkap agent dalam satu perintah: install, konfigurasi, registrasi, dan start service.

Options:
  --server-url URL   URL server monitoring (wajib)
  --token TOKEN      Registration token dari dashboard (wajib)
  --no-detect        Lewati auto-detect layanan systemd
  --repo-url URL     URL git repo agent (default: aksalatech/server-agent)
  -h, --help         Tampilkan bantuan ini

Contoh:
  sudo bash setup.sh \
    --server-url https://monitoring.example.com \
    --token YOUR_REGISTRATION_TOKEN

Clone dari git lalu setup:
  git clone git@github-aksala:aksalatech/server-agent.git
  cd server-agent
  sudo bash setup.sh --server-url https://monitoring.example.com --token TOKEN
EOF
}

cleanup() {
  if [[ -n "${CLONE_DIR}" && -d "${CLONE_DIR}" ]]; then
    rm -rf "${CLONE_DIR}"
  fi
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --server-url)
      SERVER_URL="${2:-}"
      shift 2
      ;;
    --token)
      TOKEN="${2:-}"
      shift 2
      ;;
    --no-detect)
      NO_DETECT=true
      shift
      ;;
    --repo-url)
      REPO_URL="${2:-}"
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "Opsi tidak dikenal: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ "${EUID:-0}" -ne 0 ]]; then
  echo "Jalankan script ini sebagai root (sudo)." >&2
  exit 1
fi

if [[ -z "${SERVER_URL}" && -t 0 ]]; then
  read -r -p "Server URL monitoring: " SERVER_URL
fi
if [[ -z "${TOKEN}" && -t 0 ]]; then
  read -r -p "Registration token: " TOKEN
fi

if [[ -z "${SERVER_URL}" || -z "${TOKEN}" ]]; then
  echo "server-url dan token wajib diisi." >&2
  echo "Buat host di dashboard → Agents → Tambah Agent, lalu salin token." >&2
  usage >&2
  exit 1
fi

SERVER_URL="${SERVER_URL%/}"

if [[ ! -f "${SCRIPT_DIR}/install.sh" ]]; then
  if ! command -v git >/dev/null 2>&1; then
    echo "git tidak ditemukan. Install git atau jalankan setup dari folder repo agent." >&2
    exit 1
  fi
  CLONE_DIR="$(mktemp -d)"
  echo "==> Mengunduh agent dari ${REPO_URL}"
  git clone --depth 1 "${REPO_URL}" "${CLONE_DIR}/server-agent"
  SCRIPT_DIR="${CLONE_DIR}/server-agent"
fi

echo "==> Menginstall agent"
bash "${SCRIPT_DIR}/install.sh"

PYTHON="${INSTALL_DIR}/venv/bin/python"

echo "==> Mengatur server URL"
SERVER_URL="${SERVER_URL}" "${PYTHON}" -c "
from server_monitor_agent.config import write_local_config
import os
write_local_config(server_url=os.environ['SERVER_URL'])
"

if [[ "${NO_DETECT}" == false ]]; then
  echo "==> Mendeteksi layanan yang aktif"
  if ! "${PYTHON}" -m server_monitor_agent detect --write; then
    echo "Auto-detect dilewati (tidak ada layanan dikenal atau systemctl tidak tersedia)"
  fi
else
  echo "==> Auto-detect dilewati (--no-detect)"
fi

echo "==> Mendaftarkan agent"
"${PYTHON}" -m server_monitor_agent register --server-url "${SERVER_URL}" --token "${TOKEN}"

echo "==> Menjalankan service"
systemctl enable --now server-monitor-agent

echo ""
echo "Setup selesai! Agent berjalan dan terhubung ke ${SERVER_URL}"
echo ""
systemctl status server-monitor-agent --no-pager || true
echo ""
echo "Cek log: journalctl -u server-monitor-agent -f"
