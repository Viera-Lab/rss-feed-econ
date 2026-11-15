from __future__ import annotations

import logging
import re
from datetime import date, datetime, time as dtime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from services.calendar import BULAN
from services.ipo import IpoService, load_seed

log = logging.getLogger(__name__)


def fmt_date(iso: str | None) -> str:
    if not iso:
        return "?"
    d = date.fromisoformat(iso)
    return f"{d.day} {BULAN[d.month - 1]} {d.year}"


def fmt_range(a: str | None, b: str | None) -> str:
    if not a and not b:
        return "?"
    if a == b or not b:
        return fmt_date(a)
    return f"{fmt_date(a)} – {fmt_date(b)}"


def _parse_iso(value: str | None):
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        raise ValueError(f"Format tanggal harus YYYY-MM-DD, bukan '{value}'")


class IpoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.svc = IpoService(bot.session, bot.db, config.IPO_SOURCE_URLS)

    async def cog_load(self):
        log.info("Seed IPO: %d entri", load_seed(self.bot.db, config.IPO_SEED_FILE))
        self.refresh_loop.start()
        self.daily_loop.start()

    async def cog_unload(self):
        self.refresh_loop.cancel()
        self.daily_loop.cancel()

    # ---------- /ipo ----------
    @app_commands.command(name="ipo", description="IPO saham Indonesia yang akan datang / sedang berjalan")
    async def ipo(self, interaction: discord.Interaction):
        await interaction.response.defer()
        today = datetime.now(config.TZ).date().isoformat()
        rows = self.bot.db.upcoming_ipos(today)[:10]
        headlines = await self.svc.headlines(5)

        embeds = []
        if rows:
            blocks = []
            for r in rows:
                blocks.append(
                    f"**{r['code']}** — {r['name'] or '-'}\n"
                    f"Harga: {r['price'] or '?'} · Penawaran: {fmt_range(r['offer_start'], r['offer_end'])}"
                    f" · Listing: {fmt_date(r['listing_date'])}"
                )
            embeds.append(discord.Embed(title="🇮🇩 IPO Indonesia", description="\n\n".join(blocks)[:4000],
                                        color=discord.Color.green()))
        else:
            embeds.append(discord.Embed(
                title="🇮🇩 IPO Indonesia",
                description="Belum ada data IPO tersimpan. Tambahkan manual dengan `/ipo_tambah`, "
                            "atau cek berita di bawah.",
                color=discord.Color.green()))
        if headlines:
            news = "\n".join(
                f"• [{h.title.replace('[', '(').replace(']', ')')[:120]}]({h.link})" for h in headlines
            )
            embeds.append(discord.Embed(title="📰 Berita IPO terbaru", description=news, color=discord.Color.blurple()))
        embeds[-1].set_footer(text="Verifikasi jadwal & harga di e-ipo.co.id / prospektus sebelum memesan.")
        await interaction.followup.send(embeds=embeds)

    # ---------- input manual ----------
    @app_commands.command(name="ipo_tambah", description="Tambah/ubah data IPO secara manual")
    @app_commands.describe(kode="Kode saham 4 huruf", nama="Nama perusahaan", harga="Harga/rentang, mis. Rp446-515",
                           mulai="Awal penawaran umum (YYYY-MM-DD)", akhir="Akhir penawaran umum (YYYY-MM-DD)",
                           listing="Tanggal listing (YYYY-MM-DD, opsional)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def ipo_tambah(self, interaction: discord.Interaction, kode: str, nama: str, harga: str,
                         mulai: str, akhir: str, listing: str | None = None):
        kode = kode.strip().upper()
        if not re.fullmatch(r"[A-Z]{4}", kode):
            await interaction.response.send_message("Kode saham harus 4 huruf, mis. `EMMI`.", ephemeral=True)
            return
        try:
            m, a, l = _parse_iso(mulai), _parse_iso(akhir), _parse_iso(listing)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        self.bot.db.upsert_ipo_manual(kode, nama.strip(), harga.strip(), m, a, l)
        await interaction.response.send_message(f"IPO **{kode}** tersimpan.", ephemeral=True)

    @app_commands.command(name="ipo_hapus", description="Hapus data IPO")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def ipo_hapus(self, interaction: discord.Interaction, kode: str):
        ok = self.bot.db.delete_ipo(kode.strip().upper())
        await interaction.response.send_message("Dihapus." if ok else "Kode tidak ditemukan.", ephemeral=True)

    # ---------- refresh sumber ----------
    @tasks.loop(hours=6)
    async def refresh_loop(self):
        try:
            n = await self.svc.refresh()
            log.info("Refresh IPO: %d baris terbaca", n)
        except Exception:  # noqa: BLE001
            log.exception("refresh_loop error")

    @refresh_loop.before_loop
    async def _before_refresh(self):
        await self.bot.wait_until_ready()

    # ---------- alert harian ----------
    @tasks.loop(time=dtime(hour=config.IPO_ALERT_HOUR, tzinfo=config.TZ))
    async def daily_loop(self):
        try:
            today = datetime.now(config.TZ).date()
            tomorrow = today + timedelta(days=1)
            lines, keys = [], []
            for r in self.bot.db.upcoming_ipos(today.isoformat()):
                for field, label in (("offer_start", "Penawaran umum dibuka"),
                                     ("offer_end", "Penawaran umum ditutup"),
                                     ("listing_date", "Listing di BEI")):
                    val = r[field]
                    if val not in (today.isoformat(), tomorrow.isoformat()):
                        continue
                    key = f"ipo:{r['code']}:{field}:{val}"
                    if self.bot.db.was_sent(key):
                        continue
                    when = "hari ini" if val == today.isoformat() else "besok"
                    lines.append(f"**{r['code']}** ({r['name'] or '-'}) — {label} **{when}** · Harga: {r['price'] or '?'}")
                    keys.append(key)
            if lines:
                embed = discord.Embed(title="📢 Pengingat IPO", description="\n".join(lines),
                                      color=discord.Color.green())
                embed.set_footer(text="Verifikasi di e-ipo.co.id / prospektus. Bukan rekomendasi investasi.")
                if await self.bot.send_alert(embed):
                    self.bot.db.mark_sent(*keys)
        except Exception:  # noqa: BLE001
            log.exception("daily_loop error")

    @daily_loop.before_loop
    async def _before_daily(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(IpoCog(bot))
