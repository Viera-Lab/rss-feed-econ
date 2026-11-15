from __future__ import annotations

import json
import logging
import os
import re
from datetime import date

from bs4 import BeautifulSoup

from services.http import get_text
from services.rss import NewsItem, fetch_feed

log = logging.getLogger(__name__)

NEWS_URL = (
    "https://news.google.com/rss/search?q=jadwal+IPO+saham+penawaran+umum+perdana+when:14d"
    "&hl=id&gl=ID&ceid=ID:id"
)

_BULAN = {
    "januari": 1, "jan": 1, "februari": 2, "feb": 2, "maret": 3, "mar": 3, "april": 4, "apr": 4,
    "mei": 5, "may": 5, "juni": 6, "jun": 6, "juli": 7, "jul": 7, "agustus": 8, "agu": 8,
    "agt": 8, "ags": 8, "aug": 8, "september": 9, "sep": 9, "oktober": 10, "okt": 10,
    "oct": 10, "november": 11, "nov": 11, "desember": 12, "des": 12, "dec": 12,
}
_DATE_RANGE_MONTHS = re.compile(r"(\d{1,2})\s+([A-Za-z]+)\s*[-–]\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})")
_DATE = re.compile(r"(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?\s+([A-Za-z]+)\s+(\d{4})")


def _mk(y: int, m: int | None, d: str) -> date | None:
    if not m:
        return None
    try:
        return date(y, m, int(d))
    except ValueError:
        return None


def extract_dates(text: str) -> list[date]:
    """Ambil tanggal dari teks seperti '22 Juni 2026', '22-24 Juni 2026', '30 Juni - 2 Juli 2026'."""
    out: list[date] = []
    consumed: list[tuple[int, int]] = []
    for m in _DATE_RANGE_MONTHS.finditer(text):
        d1, m1, d2, m2, yr = m.groups()
        out += [x for x in (_mk(int(yr), _BULAN.get(m1.lower()), d1),
                            _mk(int(yr), _BULAN.get(m2.lower()), d2)) if x]
        consumed.append(m.span())
    for m in _DATE.finditer(text):
        if any(s <= m.start() < e for s, e in consumed):
            continue
        d1, d2, mon, yr = m.groups()
        month = _BULAN.get(mon.lower())
        for d in (d1, d2):
            if d and (x := _mk(int(yr), month, d)):
                out.append(x)
    return sorted(out)


def _col(headers: list[str], *keys: str, exclude: tuple = ()) -> int | None:
    """Cari kolom berdasarkan urutan prioritas kata kunci; kolom di `exclude` dilewati."""
    for k in keys:
        for i, h in enumerate(headers):
            if i not in exclude and k in h:
                return i
    return None


def parse_ipo_tables(html: str) -> list[dict]:
    """Parser generik untuk tabel HTML IPO. Kolom dikenali dari teks header (kode/nama/harga/penawaran/pencatatan)."""
    soup = BeautifulSoup(html, "html.parser")
    found: list[dict] = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        headers = [c.get_text(" ", strip=True).lower() for c in rows[0].find_all(["th", "td"])]
        i_code = _col(headers, "kode", "code", "ticker")
        i_name = _col(headers, "nama", "perusahaan", "emiten", "company")
        i_price = _col(headers, "harga", "price")
        i_list = _col(headers, "pencatatan", "listing", exclude=(i_price,))
        i_offer = _col(headers, "masa", "periode", "penawaran umum", "offering", "jadwal", "penawaran",
                       exclude=(i_price, i_list, i_code, i_name))
        for tr in rows[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
            if not cells:
                continue
            get = lambda i: cells[i] if i is not None and i < len(cells) else ""  # noqa: E731
            name = get(i_name)
            code = get(i_code).strip().upper()
            if not re.fullmatch(r"[A-Z]{4}", code):
                m = re.search(r"\(([A-Z]{4})\)", name or " ".join(cells))
                code = m.group(1) if m else ""
            if not code:
                continue
            offer = extract_dates(get(i_offer))
            listing = extract_dates(get(i_list))
            found.append(
                {
                    "code": code,
                    "name": name or None,
                    "price": get(i_price) or None,
                    "offer_start": offer[0].isoformat() if offer else None,
                    "offer_end": offer[-1].isoformat() if offer else None,
                    "listing_date": listing[-1].isoformat() if listing else None,
                }
            )
    return found


class IpoService:
    def __init__(self, session, db, source_urls: list[str]):
        self.session = session
        self.db = db
        self.source_urls = source_urls

    async def refresh(self) -> int:
        """Pindai halaman sumber, simpan hasil ke DB. Return jumlah baris IPO yang terbaca."""
        total = 0
        for url in self.source_urls:
            try:
                rows = parse_ipo_tables(await get_text(self.session, url))
            except Exception as exc:  # noqa: BLE001
                log.warning("Gagal memindai %s: %s", url, exc)
                continue
            for r in rows:
                self.db.upsert_ipo_scraped(url=url, **r)
            total += len(rows)
        if total == 0:
            log.info("Scraper IPO tidak menemukan tabel; gunakan /ipo_tambah atau sesuaikan parser.")
        return total

    async def headlines(self, limit: int = 5) -> list[NewsItem]:
        try:
            items = await fetch_feed(self.session, "Google News", NEWS_URL)
        except Exception as exc:  # noqa: BLE001
            log.warning("Gagal ambil berita IPO: %s", exc)
            return []
        return items[:limit]


def load_seed(db, path: str) -> int:
    """Muat data IPO dari file JSON di repo (tetap ada setelah redeploy, tidak seperti SQLite di Render free)."""
    if not path or not os.path.exists(path):
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
    except (OSError, ValueError) as exc:
        log.warning("Seed IPO tidak bisa dibaca: %s", exc)
        return 0
    n = 0
    for r in rows if isinstance(rows, list) else []:
        code = str(r.get("code", "")).strip().upper()
        if not re.fullmatch(r"[A-Z]{4}", code):
            continue
        db.upsert_ipo_manual(code, r.get("name"), r.get("price"), r.get("offer_start"),
                             r.get("offer_end"), r.get("listing_date"))
        n += 1
    return n
