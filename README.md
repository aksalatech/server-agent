# Server Monitor Agent

Agent Python yang di-install di VM client untuk memantau status layanan (nginx, apache, mysql, postgres, mariadb, dll) dan melaporkannya ke server monitoring pusat.

## Persyaratan

- Linux dengan systemd
- Python 3.9+
- Akses jaringan ke server monitoring

## Instalasi

### 1. Buat host di dashboard

1. Login ke aplikasi monitoring
2. Buka menu **Agents** → **Tambah Agent**
3. Isi nama host dan salin **registration token** (hanya ditampilkan sekali)

### 2. Install di VM client

Salin folder `agent/` ke VM, lalu jalankan:

```bash
cd agent
sudo bash install.sh
```

### 3. Konfigurasi

Edit `/etc/server-monitor-agent/config.yaml`:

```yaml
server_url: "https://monitoring.example.com"
interval_seconds: 30
services:
  - name: nginx
    type: systemd
    unit: nginx
  - name: mysql
    type: systemd
    unit: mysql
```

### 4. Registrasi

```bash
sudo /opt/server-monitor-agent/venv/bin/python -m server_monitor_agent register \
  --server-url https://monitoring.example.com \
  --token YOUR_REGISTRATION_TOKEN
```

API key disimpan di `/etc/server-monitor-agent/credentials.json`.

### 5. Jalankan service

```bash
sudo systemctl enable --now server-monitor-agent
sudo systemctl status server-monitor-agent
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
# Registrasi
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
