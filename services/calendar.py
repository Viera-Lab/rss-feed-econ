from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from services.http import get_text

log = logging.getLogger(__name__)

# Feed tidak resmi milik Forex Factory (dipakai banyak tools). Bisa berubah/rate-limit sewaktu-waktu.
FF_URLS = (
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
)
EMOJI = {"High": "🔴", "Medium": "🟠", "Low": "🟡"}
HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


@dataclass(frozen=True)
class Event:
    title: str
    currency: str
    when: datetime  # timezone-aware (UTC)
    impact: str
    forecast: str
    previous: str

    @property
    def key(self) -> str:
        raw = f"{self.currency}|{self.title}|{self.when.isoformat()}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]


def parse_events(raw: list[dict]) -> list[Event]:
    events = []
    for item in raw:
        try:
            when = datetime.fromisoformat(item["date"])
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            events.append(
                Event(
                    title=str(item.get("title", "")).strip(),
                    currency=str(item.get("country", "")).upper(),
                    when=when.astimezone(timezone.utc),
                    impact=str(item.get("impact", "")),
                    forecast=str(item.get("forecast") or "").strip(),
                    previous=str(item.get("previous") or "").strip(),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    return events


def filter_events(events, currencies, impacts, start: datetime, end: datetime):
    return [
        e
        for e in events
        if e.currency in currencies and e.impact in impacts and start <= e.when < end
    ]


def day_label(dt: datetime) -> str:
    return f"{HARI[dt.weekday()]}, {dt.day} {BULAN[dt.month - 1]}"


def fmt_event(ev: Event, tz) -> str:
    t = ev.when.astimezone(tz).strftime("%H:%M")
    extra = []
    if ev.forecast:
        extra.append(f"F: {ev.forecast}")
    if ev.previous:
        extra.append(f"P: {ev.previous}")
    tail = f" · {' · '.join(extra)}" if extra else ""
    return f"`{t}` {EMOJI.get(ev.impact, '⚪')} **{ev.currency}** {ev.title}{tail}"


class CalendarService:
    TTL = 3600  # detik; jangan terlalu sering polling feed

    def __init__(self, session):
        self.session = session
        self._events: dict[str, Event] = {}
        self._fetched = 0.0
        self.refreshed = False  # True jika run ini berhasil mengambil data baru dari jaringan
        self._lock = asyncio.Lock()

    async def _fetch(self, url: str) -> list[Event]:
        return parse_events(json.loads(await get_text(self.session, url)))

    async def get_events(self) -> list[Event]:
        async with self._lock:
            if self._events and time.monotonic() - self._fetched < self.TTL:
                return sorted(self._events.values(), key=lambda e: e.when)

            results = await asyncio.gather(*(self._fetch(u) for u in FF_URLS), return_exceptions=True)
            ok = False
            for r in results:
                if isinstance(r, BaseException):
                    log.warning("Gagal ambil kalender: %s", r)
                    continue
                ok = True
                for ev in r:
                    self._events[ev.key] = ev

            if ok:
                self.refreshed = True
                self._fetched = time.monotonic()
                cutoff = datetime.now(timezone.utc) - timedelta(days=3)
                self._events = {k: e for k, e in self._events.items() if e.when >= cutoff}
            elif not self._events:
                raise RuntimeError("Kalender ekonomi tidak bisa diambil saat ini")
            else:  # pakai cache lama, coba lagi 5 menit lagi
                self._fetched = time.monotonic() - self.TTL + 300

            return sorted(self._events.values(), key=lambda e: e.when)

    # ---- persistensi antar-run (mode GitHub Actions) ----
    def export_raw(self) -> list[dict]:
        return [
            {"title": e.title, "country": e.currency, "date": e.when.isoformat(),
             "impact": e.impact, "forecast": e.forecast, "previous": e.previous}
            for e in self._events.values()
        ]

    def import_raw(self, raw: list[dict], age_seconds: float) -> None:
        for ev in parse_events(raw):
            self._events[ev.key] = ev
        if self._events:
            self._fetched = time.monotonic() - age_seconds
