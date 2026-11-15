"""Berita pasar dari Bloomberg + Forex Factory, diberi skor relevansi per aset (XAUUSD, BTCUSD, IHSG)."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import config
from services.rss import NewsItem, fetch_feed

log = logging.getLogger(__name__)

# Bloomberg memblokir User-Agent "bot" di beberapa kondisi, jadi pakai header mirip browser.
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
}

_GN = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
_GN_ID = "https://news.google.com/rss/search?q={q}&hl=id&gl=ID&ceid=ID:id"
FEEDS: list[tuple[str, str]] = [
    # Bloomberg (feed resmi; feeds.bloomberg.com meneruskan ke www.bloomberg.com)
    ("Bloomberg Markets", "https://www.bloomberg.com/feeds/markets/news.rss"),
    ("Bloomberg Economics", "https://www.bloomberg.com/feeds/economics/news.rss"),
    ("Bloomberg Crypto", "https://www.bloomberg.com/feeds/crypto/news.rss"),
    ("Bloomberg Politics", "https://www.bloomberg.com/feeds/politics/news.rss"),
    # Cadangan jika feed resmi diblokir / kosong
    ("Bloomberg (GNews)", _GN.format(
        q="when:6h+site:bloomberg.com+(gold+OR+bitcoin+OR+Fed+OR+Indonesia+OR+rupiah+OR+inflation)")),
    # Forex Factory: feed RSS-nya tidak resmi dan sering macet, jadi ada cadangan lewat Google News
    ("Forex Factory", "http://cdn.rss.forexfactory.net/news/all.xml"),
    ("Forex Factory (GNews)", _GN.format(q="when:6h+site:forexfactory.com")),
    # Pasar Indonesia (IHSG). Feed RSS langsung situs-situs ini tidak saya verifikasi,
    # jadi dipakai lewat Google News (site:) yang lebih stabil dan tidak diblokir.
    ("CNBC Indonesia (GNews)", _GN_ID.format(q="when:6h+site:cnbcindonesia.com+(IHSG+OR+rupiah+OR+emas+OR+bitcoin)")),
    ("Kontan (GNews)", _GN_ID.format(q="when:6h+site:kontan.co.id+(IHSG+OR+rupiah+OR+emas+OR+bitcoin)")),
    ("Bisnis.com (GNews)", _GN_ID.format(q="when:6h+site:bisnis.com+(IHSG+OR+rupiah+OR+emas+OR+bitcoin)")),
    ("Investortrust", "https://investortrust.id/rss/market.rss"),
]

# ---- aturan skor per aset: (regex, bobot) ----
_RULES: dict[str, list[tuple[str, int]]] = {
    "XAUUSD": [
        (r"\bgold\b|\bbullion\b|\bxau\b|\bemas\b", 3),
        (r"safe[- ]haven", 2),
        (r"\bfed\b|\bfomc\b|\bpowell\b|federal reserve|the fed", 2),
        (r"rate (cut|hike|decision)s?|interest rates?|suku bunga", 2),
        (r"\bcpi\b|\bpce\b|inflation|inflasi|payrolls?|nonfarm|jobless|unemployment", 2),
        (r"\bdxy\b|dollar index|\bdollar\b|treasury yields?|\byields?\b", 1),
        (r"central banks? (buy|purchas)\w*|\btariffs?\b", 1),
    ],
    "BTCUSD": [
        (r"\bbitcoin\b|\bbtc\b", 3),
        (r"\bcrypto\w*|\bkripto\w*|\bethereum\b|\bstablecoins?\b|\bether\b", 1),
        (r"bitcoin etf|spot etf|\bmicrostrategy\b|\bcoinbase\b|\bbinance\b", 1),
        (r"\bfed\b|\bfomc\b|rate (cut|hike)s?|\bliquidity\b|\bsec\b", 1),
    ],
    "IHSG": [
        (r"\bihsg\b|indeks harga saham gabungan|jakarta composite|\bidx composite\b|indonesia\w* stocks?|\bjci\b", 4),
        (r"\bindonesia\w*|\brupiah\b|bank indonesia|\bjakarta\b|\bidx\b|\bbei\b|\bojk\b|\bbi rate\b", 2),
        (r"\bsaham\b|\bbursa\b|\bemiten\b|net (sell|buy) asing|\bmsci\b", 2),
        (r"\bmsci\b|emerging[- ]market|asian (stocks|markets|shares)|foreign outflows?", 1),
        (r"\bfed\b|\bfomc\b|rate (cut|hike)s?|\bchina\b", 1),
    ],
}
_MOVER = re.compile(r"\b(surge[sd]?|soar\w*|plunge[sd]?|tumble[sd]?|crash\w*|slump\w*|record|rall(y|ies|ied)|"
                    r"sell-?off|spike[sd]?|jump\w*|slide[sd]?|sinks?|tank\w*|anjlok|jeblok|ambruk|longsor|melonjak|meroket|rekor|terjun|melemah tajam)\b")
_RULES_RE = {a: [(re.compile(p), w) for p, w in rules] for a, rules in _RULES.items()}
MIN_ASSET_SCORE = 2  # skor minimum agar sebuah aset dianggap "terkait" dengan headline


def score_title(title: str) -> tuple[int, tuple[str, ...]]:
    """Return (skor tertinggi, aset terkait). Kata 'mover' (surge/plunge/…) +1 untuk aset yang sudah relevan."""
    t = title.lower()
    mover = 1 if _MOVER.search(t) else 0
    per: dict[str, int] = {}
    for asset, rules in _RULES_RE.items():
        s = sum(w for p, w in rules if p.search(t))
        if s > 0:
            per[asset] = s + mover
    if not per:
        return 0, ()
    assets = tuple(a for a, s in per.items() if s >= MIN_ASSET_SCORE)
    return max(per.values()), assets


def _extra_feeds() -> list[tuple[str, str]]:
    out = []
    for raw in config.NEWS_EXTRA_FEEDS:
        name, _, url = raw.partition("|")
        if url.startswith("http"):
            out.append((name.strip() or url, url.strip()))
    return out


class NewsService:
    def __init__(self, session):
        self.session = session
        self.feeds = FEEDS + _extra_feeds()

    async def scan(self, max_age_hours: float) -> list[NewsItem]:
        results = await asyncio.gather(
            *(fetch_feed(self.session, n, u, BROWSER_HEADERS) for n, u in self.feeds),
            return_exceptions=True,
        )
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        best: dict[str, NewsItem] = {}
        for (name, _), res in zip(self.feeds, results):
            if isinstance(res, BaseException):
                log.warning("Feed %s gagal: %s", name, res)
                continue
            for it in res:
                if it.published and it.published < cutoff:
                    continue
                it.score, it.assets = score_title(it.title)
                if it.score <= 0:
                    continue
                if it.key not in best or it.score > best[it.key].score:
                    best[it.key] = it
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        return sorted(best.values(), key=lambda i: (i.score, i.published or epoch), reverse=True)


def format_line(item: NewsItem, threshold: int, tz) -> str:
    when = f" · {item.published.astimezone(tz).strftime('%H:%M')}" if item.published else ""
    title = item.title.replace("[", "(").replace("]", ")")
    title = title if len(title) <= 130 else title[:129] + "…"
    tags = f" · `{' '.join(item.assets)}`" if item.assets else ""
    mark = "🚨" if item.score >= threshold else "•"
    return f"{mark} **{item.score}** [{title}]({item.link}) — {item.source}{when}{tags}"
