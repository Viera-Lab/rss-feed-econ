import asyncio
import logging

import aiohttp
import discord
from discord.ext import commands

import config
from db import Database
from keepalive import start_health_server

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("econ-bot")

EXTENSIONS = ("cogs.calendar_cog", "cogs.geo_cog", "cogs.ipo_cog")


class EconBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())
        self.db = Database(config.DB_PATH)
        self.session: aiohttp.ClientSession | None = None
        self._health_runner = None

    async def setup_hook(self):
        self.session = aiohttp.ClientSession(
            headers={"User-Agent": "Mozilla/5.0 (compatible; econ-discord-bot/1.0)"},
            timeout=aiohttp.ClientTimeout(total=20),
        )
        self.db.cleanup()
        if config.PORT:
            self._health_runner = await start_health_server(self, config.PORT)
        for ext in EXTENSIONS:
            await self.load_extension(ext)
        if config.GUILD_ID:
            guild = discord.Object(config.GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def close(self):
        if self._health_runner:
            await self._health_runner.cleanup()
        if self.session:
            await self.session.close()
        await super().close()

    async def send_alert(self, embed: discord.Embed, ping: bool = False) -> bool:
        channel = self.get_channel(config.ALERT_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.fetch_channel(config.ALERT_CHANNEL_ID)
            except discord.HTTPException:
                log.warning("ALERT_CHANNEL_ID tidak valid / bot tidak punya akses.")
                return False
        content = f"<@&{config.ALERT_ROLE_ID}>" if ping and config.ALERT_ROLE_ID else None
        try:
            await channel.send(
                content=content,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True, everyone=False, users=False),
            )
            return True
        except discord.HTTPException as exc:
            log.warning("Gagal kirim alert: %s", exc)
            return False

    async def on_ready(self):
        log.info("Login sebagai %s", self.user)


async def main():
    if not config.TOKEN:
        raise SystemExit("DISCORD_TOKEN belum diisi di .env")
    async with EconBot() as bot:
        await bot.start(config.TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
