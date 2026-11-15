from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp


async def get_text(session: "aiohttp.ClientSession", url: str) -> str:
    async with session.get(url) as resp:
        resp.raise_for_status()
        return await resp.text()
