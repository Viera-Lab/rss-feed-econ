# Discord Econ Bot

Fitur tahap 1:
- `/kalender` — kalender ekonomi global (filter impact, waktu WIB) + alert otomatis H-30 & H-5 menit untuk rilis high-impact + agenda harian
- `/geo` + alert otomatis — radar headline perang/geopolitik (BBC, Al Jazeera, Google News) dengan skor kata kunci
- `/ipo` — IPO Indonesia (scrape e-IPO + input manual) + pengingat harian (penawaran buka/tutup, listing)
- `/ipo_tambah`, `/ipo_hapus` — kelola data IPO manual (perlu izin Manage Server)

## Setup
1. https://discord.com/developers/applications → New Application → Bot → copy token.
2. Invite: OAuth2 → URL Generator → scope `bot` + `applications.commands`, permission *Send Messages* + *Embed Links*.
3. Aktifkan Developer Mode di Discord, klik kanan channel alert → Copy ID.
4. ```
   python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   cp .env.example .env     # isi DISCORD_TOKEN, ALERT_CHANNEL_ID, (GUILD_ID)
   python main.py
   ```
Tidak butuh privileged intents.

## Catatan penting
- Kalender memakai feed tidak resmi Forex Factory (di-cache 1 jam; jangan restart bot berulang-ulang agar tidak kena rate limit).
- Skor geopolitik adalah heuristik kata kunci pada judul berita. Atur `GEO_THRESHOLD` (naikkan jika terlalu berisik). Bukan sinyal trading.
- Scraper IPO bersifat generik (membaca tabel HTML dan mengenali kolom dari header). Jika e-ipo.co.id memuat datanya via JavaScript, hasilnya kosong; pakai `/ipo_tambah`, atau ganti `IPO_SOURCE_URLS` ke halaman yang memuat tabel HTML.
- Data alert yang sudah terkirim disimpan di SQLite (`bot.sqlite3`) agar tidak dobel setelah restart.

## Deploy (Render + GitHub + UptimeRobot)
- Bot membuka endpoint `GET /` dan `GET /health` pada `$PORT` (otomatis di Render) untuk dicek UptimeRobot.
- `render.yaml` disertakan (Blueprint). Environment variable rahasia diisi di dashboard Render, bukan di repo.
- Filesystem Render free bersifat sementara (SQLite hilang saat restart/redeploy). Data IPO manual yang ingin permanen
  taruh di `data/ipo_seed.json` (format: `[{"code":"ABCD","name":"PT ... Tbk","price":"Rp100-120","offer_start":"2026-12-01","offer_end":"2026-12-03","listing_date":"2026-12-08"}]`).

## Mode GitHub Actions (tanpa server, alert saja)
Tanpa hosting lain: `.github/workflows/alerts.yml` menjalankan `run_once.py` tiap ~10 menit dan mengirim alert lewat Discord webhook.
- Tidak ada slash command (`/kalender`, `/ipo`, `/geo`) di mode ini, karena itu butuh bot yang online terus (`main.py`).
- Secrets repo: `DISCORD_WEBHOOK_URL` (wajib), `ALERT_ROLE_ID` (opsional).
- State alert disimpan lewat `actions/cache`. Jadwal Actions best-effort: bisa telat 5-30 menit.
- Scheduled workflow otomatis nonaktif jika repo tanpa commit 60 hari: push commit kecil atau klik "Enable workflow".
- Data IPO manual: edit `data/ipo_seed.json` lalu commit.
