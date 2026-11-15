from __future__ import annotations

import logging
from datetime import datetime, time as dtime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from services.calendar import CalendarService, day_label, filter_events, fmt_event

log = logging.getLogger(__name__)

IMPACTS = {"high": {"High"}, "medium": {"High", "Medium"}, "all": {"High", "Medium", "Low"}}
NOTE = "Volatilitas XAUUSD/BTC biasanya meningkat di sekitar rilis data AS. Bukan sinyal trading."


def chunk(lines: list[str], limit: int = 3800) -> list[str]:
    out, cur = [], ""
    for line in lines:
        if cur and len(cur) + len(line) + 1 > limit:
            out.append(cur)
            cur = ""
        cur += line + "\n"
    if cur:
        out.append(cur)
    return out


class CalendarCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.svc = CalendarService(bot.session)

    async def cog_load(self):
        self.alert_loop.start()
        self.briefing_loop.start()

    async def cog_unload(self):
        self.alert_loop.cancel()
        self.briefing_loop.cancel()

    # ---------- slash command ----------
    @app_commands.command(name="kalender", description="Kalender ekonomi global (waktu lokal)")
    @app_commands.describe(rentang="Hari ini atau 7 hari ke depan", impact="Tingkat dampak minimum")
    @app_commands.choices(
        rentang=[
            app_commands.Choice(name="Hari ini", value="today"),
            app_commands.Choice(name="7 hari ke depan", value="week"),
        ],
        impact=[
            app_commands.Choice(name="High saja", value="high"),
            app_commands.Choice(name="High + Medium", value="medium"),
            app_commands.Choice(name="Semua", value="all"),
        ],
    )
    async def kalender(self, interaction: discord.Interaction, rentang: str = "today", impact: str = "high"):
        await interaction.response.defer()
        try:
            events = await self.svc.get_events()
        except Exception as exc:  # noqa: BLE001
            log.warning("kalender gagal: %s", exc)
            await interaction.followup.send("Kalender belum bisa diambil, coba lagi sebentar lagi.")
            return

        now_local = datetime.now(config.TZ)
        start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1 if rentang == "today" else 7)
        picked = filter_events(events, config.CAL_CURRENCIES, IMPACTS.get(impact, {"High"}),
                               start.astimezone(timezone.utc), end.astimezone(timezone.utc))
        if not picked:
            await interaction.followup.send("Tidak ada event yang cocok dengan filter tersebut.")
            return

        lines, last_day = [], None
        for ev in picked:
            local = ev.when.astimezone(config.TZ)
            if local.date() != last_day:
                last_day = local.date()
                lines.append(f"\n**{day_label(local)}**")
            lines.append(fmt_event(ev, config.TZ))

        embeds = []
        for i, text in enumerate(chunk(lines)[:10]):
            e = discord.Embed(description=text, color=discord.Color.red())
            if i == 0:
                e.title = "📅 Kalender Ekonomi"
            embeds.append(e)
        embeds[-1].set_footer(
            text=f"{'/'.join(sorted(config.CAL_CURRENCIES))} · {config.TZ.key} · F=forecast P=previous · Sumber: Forex Factory (tidak resmi)"
        )
        await interaction.followup.send(embeds=embeds)

    # ---------- alert H-30 / H-5 menit ----------
    @tasks.loop(minutes=1)
    async def alert_loop(self):
        try:
            events = await self.svc.get_events()
            now = datetime.now(timezone.utc)
            leads = config.CAL_ALERT_LEADS
            if not leads:
                return
            window = filter_events(events, config.CAL_CURRENCIES, config.CAL_ALERT_IMPACTS,
                                   now, now + timedelta(minutes=max(leads)))
            due = []
            for ev in window:
                delta = (ev.when - now).total_seconds()
                lead = next((l for l in leads if delta <= l * 60), None)  # lead terkecil yang berlaku
                if lead is None:
                    continue
                key = f"cal:{ev.key}:{lead}"
                if not self.bot.db.was_sent(key):
                    due.append((ev, delta, key))
            if not due:
                return
            lines = []
            for ev, delta, _ in due:
                lines.append(f"{fmt_event(ev, config.TZ)}  → **{max(1, round(delta / 60))} menit lagi**")
            embed = discord.Embed(
                title="⏰ Rilis data ekonomi segera",
                description="\n".join(lines) + f"\n\n_{NOTE}_",
                color=discord.Color.red(),
            )
            if await self.bot.send_alert(embed, ping=True):
                self.bot.db.mark_sent(*(k for _, _, k in due))
        except Exception:  # noqa: BLE001
            log.exception("alert_loop error")

    @alert_loop.before_loop
    async def _before_alert(self):
        await self.bot.wait_until_ready()

    # ---------- ringkasan harian ----------
    @tasks.loop(time=dtime(hour=config.BRIEFING_HOUR, tzinfo=config.TZ))
    async def briefing_loop(self):
        try:
            events = await self.svc.get_events()
            start = datetime.now(config.TZ).replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            picked = filter_events(events, config.CAL_CURRENCIES, {"High", "Medium"},
                                   start.astimezone(timezone.utc), end.astimezone(timezone.utc))
            body = "\n".join(fmt_event(e, config.TZ) for e in picked) or "Tidak ada rilis penting hari ini."
            embed = discord.Embed(
                title=f"🗓️ Agenda ekonomi hari ini — {day_label(start)}",
                description=body[:4000],
                color=discord.Color.orange(),
            )
            await self.bot.send_alert(embed)
        except Exception:  # noqa: BLE001
            log.exception("briefing_loop error")

    @briefing_loop.before_loop
    async def _before_briefing(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(CalendarCog(bot))
