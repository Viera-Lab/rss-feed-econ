import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS sent (key TEXT PRIMARY KEY, ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS ipo (
    code TEXT PRIMARY KEY,
    name TEXT,
    price TEXT,
    offer_start TEXT,
    offer_end TEXT,
    listing_date TEXT,
    source TEXT NOT NULL,
    url TEXT,
    updated_ts INTEGER NOT NULL
);
"""


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---- dedupe alert ----
    def was_sent(self, key: str) -> bool:
        return self.conn.execute("SELECT 1 FROM sent WHERE key=?", (key,)).fetchone() is not None

    def mark_sent(self, *keys: str) -> None:
        now = int(time.time())
        self.conn.executemany(
            "INSERT OR IGNORE INTO sent(key, ts) VALUES (?, ?)", [(k, now) for k in keys]
        )
        self.conn.commit()

    def cleanup(self, days: int = 14) -> None:
        self.conn.execute("DELETE FROM sent WHERE ts < ?", (int(time.time()) - days * 86400,))
        self.conn.commit()

    # ---- key-value (cache antar-run) ----
    def get_kv(self, key: str):
        row = self.conn.execute("SELECT value, ts FROM kv WHERE key=?", (key,)).fetchone()
        return (row["value"], row["ts"]) if row else None

    def set_kv(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO kv(key, value, ts) VALUES (?, ?, ?)",
                          (key, value, int(time.time())))
        self.conn.commit()

    # ---- IPO ----
    def upsert_ipo_scraped(self, code, name, price, offer_start, offer_end, listing_date, url=None):
        """Data hasil scrape tidak menimpa entri manual."""
        self.conn.execute(
            """
            INSERT INTO ipo(code, name, price, offer_start, offer_end, listing_date, source, url, updated_ts)
            VALUES (?, ?, ?, ?, ?, ?, 'scrape', ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                name = COALESCE(excluded.name, ipo.name),
                price = COALESCE(excluded.price, ipo.price),
                offer_start = COALESCE(excluded.offer_start, ipo.offer_start),
                offer_end = COALESCE(excluded.offer_end, ipo.offer_end),
                listing_date = COALESCE(excluded.listing_date, ipo.listing_date),
                url = COALESCE(excluded.url, ipo.url),
                updated_ts = excluded.updated_ts
            WHERE ipo.source != 'manual'
            """,
            (code, name, price, offer_start, offer_end, listing_date, url, int(time.time())),
        )
        self.conn.commit()

    def upsert_ipo_manual(self, code, name, price, offer_start, offer_end, listing_date):
        self.conn.execute(
            """
            INSERT OR REPLACE INTO ipo(code, name, price, offer_start, offer_end, listing_date,
                                       source, url, updated_ts)
            VALUES (?, ?, ?, ?, ?, ?, 'manual', NULL, ?)
            """,
            (code, name, price, offer_start, offer_end, listing_date, int(time.time())),
        )
        self.conn.commit()

    def delete_ipo(self, code: str) -> bool:
        cur = self.conn.execute("DELETE FROM ipo WHERE code=?", (code,))
        self.conn.commit()
        return cur.rowcount > 0

    def upcoming_ipos(self, today_iso: str):
        return self.conn.execute(
            """
            SELECT * FROM ipo
            WHERE MAX(COALESCE(listing_date,''), COALESCE(offer_end,''), COALESCE(offer_start,'')) >= ?
            ORDER BY COALESCE(offer_start, listing_date, offer_end)
            """,
            (today_iso,),
        ).fetchall()
