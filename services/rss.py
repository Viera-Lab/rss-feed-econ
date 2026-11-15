from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from services.http import get_text


@dataclass
class NewsItem:
    title: str
    link: str
    source: str
    published: datetime | None
    key: str
    score: int = 0


def _norm_title(title: str) -> str:
    title = re.sub(r"\s+-\s+[^-]{2,40}$", "", title)  # buang suffix " - Penerbit"
    return re.sub(r"\W+", " ", title.lower()).strip()


def parse_rss(xml_text: str, source: str) -> list[NewsItem]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items: list[NewsItem] = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        if not title:
            continue
        link = (it.findtext("link") or "").strip()
        published = None
        pub = it.findtext("pubDate")
        if pub:
            try:
                published = parsedate_to_datetime(pub)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                published = None
        key = hashlib.sha1(_norm_title(title).encode()).hexdigest()[:16]
        items.append(NewsItem(title, link, source, published, key))
    return items


async def fetch_feed(session, name: str, url: str) -> list[NewsItem]:
    return parse_rss(await get_text(session, url), name)
