from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

from services.rss import NewsItem, fetch_feed

log = logging.getLogger(__name__)

FEEDS = [
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    (
        "Google News",
        "https://news.google.com/rss/search?q=when:6h+(war+OR+airstrike+OR+missile+OR+invasion"
        "+OR+ceasefire+OR+sanctions)&hl=en-US&gl=US&ceid=US:en",
    ),
]

# (regex, bobot). Skor hanya dihitung jika ada kata kejadian; kata wilayah hanya menambah (maks +2).
_EVENT = [
    (r"\bdeclar\w* war\b", 4),
    (r"\bwar\b", 1),
    (r"\binvasion\b|\binvad\w+", 3),
    (r"\bair ?strikes?\b", 3),
    (r"\bmissiles?\b", 3),
    (r"\bnuclear\b", 3),
    (r"\bescalat\w+", 2),
    (r"\bblockade\b", 2),
    (r"\bmobili[sz]\w+", 2),
    (r"\bshelling\b|\bbombard\w+", 2),
    (r"\bretaliat\w+", 2),
    (r"\bstrikes? on\b", 2),
    (r"\bcoup\b", 2),
    (r"\bdrones?\b", 1),
    (r"\bceasefire\b", 1),
    (r"\bsanctions?\b", 1),
    (r"\bkilled\b|\bcasualt\w+", 1),
    (r"\bconflict\b", 1),
]
_REGION = [
    (r"\bhormuz\b", 2),
    (r"\biran\b", 1),
    (r"\bisrael\b|\bgaza\b|\blebanon\b", 1),
    (r"\btaiwan\b", 1),
    (r"\bukraine\b|\brussia\b", 1),
    (r"\bchina\b", 1),
    (r"\bnorth korea\b", 1),
    (r"\bred sea\b|\bhouthi\w*|\byemen\b", 1),
    (r"\bnato\b", 1),
    (r"\bmiddle east\b", 1),
]
_EVENT_RE = [(re.compile(p), w) for p, w in _EVENT]
_REGION_RE = [(re.compile(p), w) for p, w in _REGION]


def score_title(title: str) -> int:
    t = title.lower()
    ev = sum(w for p, w in _EVENT_RE if p.search(t))
    if ev == 0:
        return 0
    rg = min(2, sum(w for p, w in _REGION_RE if p.search(t)))
    return ev + rg


class GeoService:
    def __init__(self, session):
        self.session = session

    async def scan(self, max_age_hours: int) -> list[NewsItem]:
        results = await asyncio.gather(
            *(fetch_feed(self.session, n, u) for n, u in FEEDS), return_exceptions=True
        )
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        best: dict[str, NewsItem] = {}
        for (name, _), res in zip(FEEDS, results):
            if isinstance(res, BaseException):
                log.warning("Feed %s gagal: %s", name, res)
                continue
            for it in res:
                if it.published and it.published < cutoff:
                    continue
                it.score = score_title(it.title)
                if it.key not in best or it.score > best[it.key].score:
                    best[it.key] = it
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        return sorted(best.values(), key=lambda i: (i.score, i.published or epoch), reverse=True)
