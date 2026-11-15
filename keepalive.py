"""Server HTTP mini supaya platform hosting (Render) & UptimeRobot punya URL untuk dicek."""
from __future__ import annotations

import logging
import math

from aiohttp import web

log = logging.getLogger(__name__)


def build_app(bot) -> web.Application:
    async def root(_request):
        return web.Response(text="econ-bot alive")

    async def health(_request):
        ready = bot.is_ready()
        latency = bot.latency
        body = {
            "status": "ok" if ready else "starting",
            "latency_ms": round(latency * 1000) if ready and not math.isnan(latency) else None,
        }
        return web.json_response(body, status=200 if ready else 503)

    app = web.Application()
    app.router.add_get("/", root)        # GET & HEAD
    app.router.add_get("/health", health)
    return app


async def start_health_server(bot, port: int) -> web.AppRunner:
    runner = web.AppRunner(build_app(bot))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Health server aktif di port %d", port)
    return runner
