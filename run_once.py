"""Mode one-shot untuk GitHub Actions.

Menjalankan semua pengecekan satu kali lalu keluar. Alert dikirim lewat Discord webhook
(tanpa bot yang online terus-menerus, jadi tidak ada slash command di mode ini).
State (alert yang sudah terkirim, cache kalender) disimpan di SQLite dan dipulihkan lewat actions/cache.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import config
from db import Database
from services.calendar import BULAN, CalendarService, day_label, filter_events, fmt_event
from services.geopolitics import GeoService
from services.ipo import IpoService, load_seed
from services.news import NewsService, format_line

log = logging.getLogger("run_once")

RED, ORANGE, DARK_RED, GREEN, BLUE = 0xE74C3C, 0xE67E22, 0x992D22, 0x2ECC71, 0x3498DB
CAL_NOTE = "Volatilitas XAUUSD/BTC biasanya meningkat di sekitar rilis data AS. Bukan sinyal trading."
GEO_NOTE = (
    "Eskalasi militer biasanya memicu risk-off: emas (XAU) sering menguat sebagai safe haven, "
    "aset berisiko seperti BTC bisa volatil. Skor berbasis kata kunci headline — bukan sinyal trading."
)


class Webhook:
    def __init__(self, session, url: str):
        self.session, self.url = session, url

    async def send(self, embed: dict, ping: bool = False) -> bool:
        payload: dict = {"embeds": [embed], "allowed_mentions": {"parse": []}}
        if ping and config.ALERT_ROLE_ID:
            payload["content"] = f"<@&{config.ALERT_ROLE_ID}>"
            payload["allowed_mentions"] = {"roles": [str(config.ALERT_ROLE_ID)]}
        for _ in range(3):
            async with self.session.post(self.url, json=payload) as resp:
                if resp.status == 429:
                    try:
                        wait = float((await resp.json()).get("retry_after", 2))
                    except Exception:  # noqa: BLE001
                        wait = 2
                    await asyncio.sleep(min(wait, 10))
                    continue
                if resp.status >= 400:
                    log.error("Webhook gagal %s: %s", resp.status, (await resp.text())[:200])
                    return False
                return True
        return False


def embed(title: str, description: str, color: int, footer: str | None = None) -> dict:
    e = {"title": title, "description": description[:4000], "color": color}
    if footer:
        e["footer"] = {"text": footer}
    return e


# ------------------------------------------------------------------ kalender
async def check_calendar(notify, svc: CalendarService, db: Database, now: datetime) -> None:
    events = await svc.get_events()

    leads = config.CAL_ALERT_LEADS
    if leads:
        window = filter_events(events, config.CAL_CURRENCIES, config.CAL_ALERT_IMPACTS,
                               now, now + timedelta(minutes=max(leads)))
        due = []
        for ev in window:
            delta = (ev.when - now).total_seconds()
            lead = next((l for l in leads if delta <= l * 60), None)  # lead terkecil yang berlaku
            if lead is None:
                continue
            key = f"cal:{ev.key}:{lead}"
            if not db.was_sent(key):
                due.append((ev, delta, key))
        if due:
            lines = [f"{fmt_event(ev, config.TZ)}  → **{max(1, round(d / 60))} menit lagi**" for ev, d, _ in due]
            ok = await notify.send(embed("⏰ Rilis data ekonomi segera",
                                         "\n".join(lines) + f"\n\n_{CAL_NOTE}_", RED), ping=True)
            if ok:
                db.mark_sent(*(k for _, _, k in due))

    local = now.astimezone(config.TZ)
    if config.BRIEFING_HOUR <= local.hour < config.BRIEFING_HOUR + 4:
        key = f"brief:{local.date().isoformat()}"
        if not db.was_sent(key):
            start = local.replace(hour=0, minute=0, second=0, microsecond=0)
            picked = filter_events(events, config.CAL_CURRENCIES, {"High", "Medium"},
                                   start.astimezone(timezone.utc),
                                   (start + timedelta(days=1)).astimezone(timezone.utc))
            body = "\n".join(fmt_event(e, config.TZ) for e in picked) or "Tidak ada rilis penting hari ini."
            if await notify.send(embed(f"🗓️ Agenda ekonomi hari ini — {day_label(start)}", body, ORANGE)):
                db.mark_sent(key)


# ----------------------------------------------------------------- geopolitik
def _geo_line(item) -> str:
    when = f" · {item.published.astimezone(config.TZ).strftime('%H:%M')}" if item.published else ""
    title = item.title.replace("[", "(").replace("]", ")")
    title = title if len(title) <= 140 else title[:139] + "…"
    mark = "🚨" if item.score >= config.GEO_THRESHOLD else "•"
    return f"{mark} **{item.score}** [{title}]({item.link}) — {item.source}{when}"


async def check_geo(notify, svc: GeoService, db: Database) -> None:
    items = await svc.scan(config.GEO_MAX_AGE_HOURS)
    new = [i for i in items if not db.was_sent(f"geo:{i.key}")]
    if not new:
        return
    hot = [i for i in new if i.score >= config.GEO_THRESHOLD][:5]
    if hot:
        ok = await notify.send(embed("🚨 Alert geopolitik",
                                     "\n".join(_geo_line(i) for i in hot) + f"\n\n_{GEO_NOTE}_", DARK_RED),
                               ping=True)
        if not ok:
            return  # jangan tandai terkirim jika gagal kirim
    db.mark_sent(*(f"geo:{i.key}" for i in new))


# ---------------------------------------------------------------------- berita
NEWS_NOTE = (
    "Headline Bloomberg & Forex Factory diberi skor berdasarkan kata kunci untuk XAUUSD, BTCUSD, dan IHSG. "
    "Bukan sinyal trading."
)


async def check_news(notify, svc: NewsService, db: Database) -> None:
    items = await svc.scan(config.NEWS_MAX_AGE_HOURS)
    new = [i for i in items if not db.was_sent(f"news:{i.key}")]
    if not new:
        return
    hot = [i for i in new if i.score >= config.NEWS_THRESHOLD][:6]
    if hot:
        body = "\n".join(format_line(i, config.NEWS_THRESHOLD, config.TZ) for i in hot)
        if not await notify.send(embed("📰 Berita pasar penting", body + f"\n\n_{NEWS_NOTE}_", BLUE),
                                 ping=config.NEWS_PING):
            return
    db.mark_sent(*(f"news:{i.key}" for i in new))


# ------------------------------------------------------------------------ IPO
def _fmt_date(iso: str | None) -> str:
    if not iso:
        return "?"
    d = date.fromisoformat(iso)
    return f"{d.day} {BULAN[d.month - 1]} {d.year}"


async def check_ipo(notify, svc: IpoService, db: Database, now: datetime) -> None:
    log.info("Seed IPO: %d entri", load_seed(db, config.IPO_SEED_FILE))
    last = db.get_kv("ipo_refresh")
    if not last or time.time() - last[1] > 6 * 3600:
        await svc.refresh()
        db.set_kv("ipo_refresh", "1")

    local = now.astimezone(config.TZ)
    if not (config.IPO_ALERT_HOUR <= local.hour < config.IPO_ALERT_HOUR + 6):
        return
    today, tomorrow = local.date(), local.date() + timedelta(days=1)
    lines, keys = [], []
    for r in db.upcoming_ipos(today.isoformat()):
        for field, label in (("offer_start", "Penawaran umum dibuka"),
                             ("offer_end", "Penawaran umum ditutup"),
                             ("listing_date", "Listing di BEI")):
            val = r[field]
            if val not in (today.isoformat(), tomorrow.isoformat()):
                continue
            key = f"ipo:{r['code']}:{field}:{val}"
            if db.was_sent(key):
                continue
            when = "hari ini" if val == today.isoformat() else "besok"
            lines.append(f"**{r['code']}** ({r['name'] or '-'}) — {label} **{when}** · Harga: {r['price'] or '?'}")
            keys.append(key)
    if lines and await notify.send(embed("📢 Pengingat IPO", "\n".join(lines), GREEN,
                                         "Verifikasi di e-ipo.co.id / prospektus. Bukan rekomendasi investasi.")):
        db.mark_sent(*keys)


# ----------------------------------------------------------------------- main
async def run_all(notify, session, db: Database, now: datetime) -> dict[str, bool]:
    cal = CalendarService(session)
    cached = db.get_kv("calendar")
    if cached:
        try:
            cal.import_raw(json.loads(cached[0]), time.time() - cached[1])
        except ValueError:
            pass

    async def cal_job():
        try:
            await check_calendar(notify, cal, db, now)
        finally:
            if cal.refreshed:
                db.set_kv("calendar", json.dumps(cal.export_raw()))

    jobs = {
        "kalender": cal_job(),
        "geopolitik": check_geo(notify, GeoService(session), db),
        "berita": check_news(notify, NewsService(session), db),
        "ipo": check_ipo(notify, IpoService(session, db, config.IPO_SOURCE_URLS), db, now),
    }
    results = await asyncio.gather(*jobs.values(), return_exceptions=True)
    status = {}
    for name, res in zip(jobs, results):
        status[name] = not isinstance(res, BaseException)
        if isinstance(res, BaseException):
            log.error("Job %s gagal", name, exc_info=res)
    return status


async def main() -> int:
    import aiohttp

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.WEBHOOK_URL:
        log.error("DISCORD_WEBHOOK_URL belum diisi")
        return 1
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    db = Database(config.DB_PATH)
    db.cleanup()
    async with aiohttp.ClientSession(
        headers={"User-Agent": "Mozilla/5.0 (compatible; econ-discord-bot/1.0)"},
        timeout=aiohttp.ClientTimeout(total=20),
    ) as session:
        status = await run_all(Webhook(session, config.WEBHOOK_URL), session, db, datetime.now(timezone.utc))
    log.info("Selesai: %s", status)
    # Gagal total hanya jika semua job error (satu sumber down tidak menggagalkan run)
    return 0 if any(status.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
