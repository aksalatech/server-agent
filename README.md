# Server Monitor Agent

Agent Python yang di-install di VM client untuk memantau status layanan (nginx, apache, mysql, postgres, mariadb, dll) dan melaporkannya ke server monitoring pusat.

## Persyaratan

- Linux dengan systemd
- Python 3.9+
- Git (untuk clone otomatis)
- Akses jaringan ke server monitoring

## Setup Cepat (Disarankan)

1. Buat host di dashboard → **Agents** → **Tambah Agent**, salin **registration token**
2. Di VM client, jalankan satu perintah:

```bash
git clone git@github-aksala:aksalatech/server-agent.git /tmp/server-agent
sudo bash /tmp/server-agent/setup.sh \
  --server-url https://monitoring.example.com \
  --token YOUR_REGISTRATION_TOKEN
```

`setup.sh` akan otomatis:
- Install dependensi dan agent ke `/opt/server-monitor-agent`
- Mendeteksi layanan systemd yang aktif (nginx, mysql, postgres, dll)
- Mendaftarkan agent ke server monitoring
- Menjalankan dan mengaktifkan service systemd

Sudah punya folder agent? Cukup jalankan `sudo bash setup.sh` dari dalam folder tersebut.

## Instalasi Manual

Jika Anda perlu langkah terpisah:

```bash
sudo bash install.sh
sudo bash setup.sh --server-url https://monitoring.example.com --token TOKEN
```

Atau tanpa `setup.sh`:

```bash
sudo bash install.sh
sudo nano /etc/server-monitor-agent/config.yaml   # set server_url
sudo /opt/server-monitor-agent/venv/bin/python -m server_monitor_agent detect --write
sudo /opt/server-monitor-agent/venv/bin/python -m server_monitor_agent register \
  --server-url https://monitoring.example.com --token TOKEN
sudo systemctl enable --now server-monitor-agent
```

## Tipe Check

| Tipe | Deskripsi | Contoh target |
|------|-----------|---------------|
| `systemd` | Cek status unit systemd | `nginx`, `mysql`, `postgresql` |
| `tcp` | Cek koneksi TCP | `127.0.0.1:3306`, `80` |

## Konfigurasi Hybrid

Agent menggabungkan config lokal (`/etc/server-monitor-agent/config.yaml`) dengan override dari dashboard:

- Service **remote** dari dashboard menggantikan service lokal dengan nama yang sama
- Service lokal yang tidak ada di dashboard tetap dipantau
- Dashboard bisa menambah atau menonaktifkan check tanpa mengedit file di VM

## Perintah

```bash
# Setup lengkap (disarankan)
sudo bash setup.sh --server-url URL --token TOKEN

# Deteksi layanan aktif
python -m server_monitor_agent detect
python -m server_monitor_agent detect --write

# Registrasi manual
python -m server_monitor_agent register --server-url URL --token TOKEN

# Jalankan sekali (debug)
python -m server_monitor_agent run --once

# Jalankan loop (default via systemd)
python -m server_monitor_agent run
```

## Log

```bash
journalctl -u server-monitor-agent -f
```

## Troubleshooting

- **Unauthorized saat register**: Token sudah digunakan atau kedaluwarsa. Generate token baru dari halaman detail host.
- **Agent offline di dashboard**: Cek `systemctl status server-monitor-agent` dan pastikan `server_url` benar.
- **Service selalu down**: Pastikan nama unit systemd benar (`systemctl status nginx`).
- **Tidak ada service terdeteksi**: Tambahkan manual di config atau dari dashboard, atau jalankan `detect --write` setelah install layanan baru.
