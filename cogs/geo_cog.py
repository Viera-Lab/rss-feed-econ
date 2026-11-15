from __future__ import annotations

import logging
from datetime import timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from services.geopolitics import GeoService

log = logging.getLogger(__name__)

NOTE = (
    "Eskalasi militer biasanya memicu risk-off: emas (XAU) sering menguat sebagai safe haven, "
    "aset berisiko seperti BTC bisa volatil. Skor berbasis kata kunci headline — bukan sinyal trading."
)


def _clean(title: str, limit: int = 140) -> str:
    title = title.replace("[", "(").replace("]", ")")
    return title if len(title) <= limit else title[: limit - 1] + "…"


def _line(item, threshold: int) -> str:
    when = ""
    if item.published:
        when = f" · {item.published.astimezone(config.TZ).strftime('%H:%M')}"
    mark = "🚨" if item.score >= threshold else "•"
    return f"{mark} **{item.score}** [{_clean(item.title)}]({item.link}) — {item.source}{when}"


class GeoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.svc = GeoService(bot.session)

    async def cog_load(self):
        self.scan_loop.change_interval(minutes=config.GEO_INTERVAL_MIN)
        self.scan_loop.start()

    async def cog_unload(self):
        self.scan_loop.cancel()

    @app_commands.command(name="geo", description="Headline geopolitik/perang paling relevan saat ini")
    async def geo(self, interaction: discord.Interaction):
        await interaction.response.defer()
        items = [i for i in await self.svc.scan(12) if i.score > 0][:8]
        if not items:
            await interaction.followup.send("Tidak ada headline geopolitik signifikan dalam 12 jam terakhir.")
            return
        embed = discord.Embed(
            title="🌍 Radar geopolitik (12 jam terakhir)",
            description="\n".join(_line(i, config.GEO_THRESHOLD) for i in items),
            color=discord.Color.dark_red(),
        )
        embed.set_footer(text=f"🚨 = skor ≥ {config.GEO_THRESHOLD} (ambang alert)")
        await interaction.followup.send(embed=embed)

    @tasks.loop(minutes=5)
    async def scan_loop(self):
        try:
            items = await self.svc.scan(config.GEO_MAX_AGE_HOURS)
            new = [i for i in items if not self.bot.db.was_sent(f"geo:{i.key}")]
            if not new:
                return
            hot = [i for i in new if i.score >= config.GEO_THRESHOLD][:5]
            if hot:
                embed = discord.Embed(
                    title="🚨 Alert geopolitik",
                    description="\n".join(_line(i, config.GEO_THRESHOLD) for i in hot) + f"\n\n_{NOTE}_",
                    color=discord.Color.dark_red(),
                )
                if not await self.bot.send_alert(embed, ping=True):
                    return  # jangan tandai sudah terkirim kalau gagal kirim
            self.bot.db.mark_sent(*(f"geo:{i.key}" for i in new))
        except Exception:  # noqa: BLE001
            log.exception("scan_loop error")

    @scan_loop.before_loop
    async def _before_scan(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(GeoCog(bot))
