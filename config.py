import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int = 0) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _list(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD_ID = _int("GUILD_ID")
ALERT_CHANNEL_ID = _int("ALERT_CHANNEL_ID")
ALERT_ROLE_ID = _int("ALERT_ROLE_ID")
TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Jakarta"))
DB_PATH = os.getenv("DB_PATH", "bot.sqlite3")
PORT = _int("PORT")  # diisi otomatis oleh Render; 0 = tanpa health server (jalan lokal)

CAL_CURRENCIES = {c.upper() for c in _list("CAL_CURRENCIES", "USD")}
CAL_ALERT_IMPACTS = set(_list("CAL_ALERT_IMPACTS", "High"))
CAL_ALERT_LEADS = sorted(
    {int(x) for x in _list("CAL_ALERT_LEAD_MINUTES", "30,5") if x.isdigit()}
)
BRIEFING_HOUR = _int("BRIEFING_HOUR", 7)

GEO_INTERVAL_MIN = max(1, _int("GEO_INTERVAL_MIN", 5))
GEO_THRESHOLD = _int("GEO_THRESHOLD", 5)
GEO_MAX_AGE_HOURS = _int("GEO_MAX_AGE_HOURS", 3)

# Berita pasar (Bloomberg + Forex Factory) untuk XAUUSD / BTCUSD / IHSG
NEWS_INTERVAL_MIN = max(1, _int("NEWS_INTERVAL_MIN", 10))
NEWS_THRESHOLD = _int("NEWS_THRESHOLD", 4)        # skor minimum untuk alert otomatis
NEWS_MAX_AGE_HOURS = _int("NEWS_MAX_AGE_HOURS", 3)
NEWS_PING = _int("NEWS_PING", 0) == 1             # 1 = ping ALERT_ROLE_ID saat alert berita
NEWS_EXTRA_FEEDS = _list("NEWS_EXTRA_FEEDS", "")  # format: Nama|https://url, dipisah koma

IPO_SOURCE_URLS = _list("IPO_SOURCE_URLS", "https://e-ipo.co.id")
IPO_ALERT_HOUR = _int("IPO_ALERT_HOUR", 8)
IPO_SEED_FILE = os.getenv("IPO_SEED_FILE", "data/ipo_seed.json")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")  # mode GitHub Actions (run_once.py)
