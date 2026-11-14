"""Voice-channel music playback using direct FFmpeg streams."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Optional

import discord
from discord.ext import commands


logger = logging.getLogger(__name__)


@dataclass
class Track:
    query: str
    title: str
    webpage_url: str
    stream_url: str
    duration: Optional[int] = None


class Music(commands.Cog):
    """Play YouTube/search audio in a guild voice channel without saving files."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.queues: dict[int, list[Track]] = {}
        self.text_channels: dict[int, discord.abc.Messageable] = {}
        self.volumes: dict[int, float] = {}
        self.playback_generation: dict[int, int] = {}

    @staticmethod
    def _extract(query: str) -> Track:
        from yt_dlp import YoutubeDL

        search = query if query.startswith(("http://", "https://")) else f"ytsearch1:{query}"
        options = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "default_search": "ytsearch",
            "source_address": "0.0.0.0",
        }
        with YoutubeDL(options) as downloader:
            info = downloader.extract_info(search, download=False)
        if info and info.get("entries"):
            info = next((entry for entry in info["entries"] if entry), None)
        if not info or not info.get("url"):
            raise RuntimeError("No playable audio was found")
        return Track(
            query=query,
            title=str(info.get("title") or "Unknown track"),
            webpage_url=str(info.get("webpage_url") or query),
            stream_url=str(info["url"]),
            duration=info.get("duration"),
        )

    async def _refresh_stream_url(self, track: Track) -> Track:
        refreshed = await asyncio.to_thread(self._extract, track.query)
        return refreshed

    async def _play_next(self, guild_id: int, *, refresh_first: bool = True) -> None:
        generation = self.playback_generation.get(guild_id, 0)
        while True:
            guild = self.bot.get_guild(guild_id)
            voice = guild.voice_client if guild else None
            queue = self.queues.get(guild_id, [])
            if voice is None or not queue:
                return
            queued_track = queue[0]
            if refresh_first:
                try:
                    track = await self._refresh_stream_url(queued_track)
                except Exception as error:
                    logger.warning("Could not refresh music stream in guild %s: %s", guild_id, error)
                    if generation != self.playback_generation.get(guild_id, 0):
                        return
                    queue = self.queues.get(guild_id, [])
                    if queue and queue[0] is queued_track:
                        queue.pop(0)
                    continue
            else:
                track = queued_track
                refresh_first = True
            if generation != self.playback_generation.get(guild_id, 0):
                return
            queue = self.queues.get(guild_id, [])
            if not queue or queue[0] is not queued_track:
                return
            queue.pop(0)
            break
        volume = self.volumes.get(guild_id, 0.5)
        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(
                track.stream_url,
                before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                options="-vn",
            ),
            volume=volume,
        )

        def playback_finished(error: Optional[Exception]) -> None:
            if error:
                logger.warning("Music playback failed in guild %s: %s", guild_id, error)
            if generation != self.playback_generation.get(guild_id, 0):
                return
            asyncio.run_coroutine_threadsafe(self._play_next(guild_id), self.bot.loop)

        voice.play(source, after=playback_finished)
        channel = self.text_channels.get(guild_id)
        if channel:
            await channel.send(f"🎶 Now playing **{track.title}**")

    async def _voice_for(self, ctx: commands.Context) -> Optional[discord.VoiceClient]:
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("❌ Join a voice channel first.", ephemeral=True)
            return None
        target = ctx.author.voice.channel
        voice = ctx.guild.voice_client
        if voice is None:
            voice = await target.connect()
        elif voice.channel != target:
            await voice.move_to(target)
        return voice

    @commands.hybrid_command(name="play", description="Play or queue a YouTube/search audio track.")
    @commands.guild_only()
    async def play(self, ctx: commands.Context, *, query: str) -> None:
        generation = self.playback_generation.get(ctx.guild.id, 0)
        await ctx.defer()
        voice = await self._voice_for(ctx)
        if voice is None:
            return
        if generation != self.playback_generation.get(ctx.guild.id, 0):
            if voice.is_connected() and not voice.is_playing() and not voice.is_paused():
                await voice.disconnect()
            await ctx.send("⏹️ Playback was stopped before the voice connection completed.", ephemeral=True)
            return
        try:
            track = await asyncio.to_thread(self._extract, query)
        except Exception:
            await ctx.send("❌ I could not find playable audio for that query.", ephemeral=True)
            return
        if generation != self.playback_generation.get(ctx.guild.id, 0):
            await ctx.send("⏹️ Playback was stopped before the search completed.", ephemeral=True)
            return
        queue = self.queues.setdefault(ctx.guild.id, [])
        self.text_channels[ctx.guild.id] = ctx.channel
        was_idle = not voice.is_playing() and not voice.is_paused() and not queue
        queue.append(track)
        if was_idle:
            await self._play_next(ctx.guild.id, refresh_first=False)
        else:
            await ctx.send(f"➕ Queued **{track.title}** (position {len(queue)}).")

    @commands.hybrid_command(name="skip", description="Skip the current track.")
    @commands.guild_only()
    async def skip(self, ctx: commands.Context) -> None:
        voice = ctx.guild.voice_client
        if not voice or not voice.is_playing():
            await ctx.send("❌ Nothing is playing.", ephemeral=True)
            return
        voice.stop()
        await ctx.send("⏭️ Skipped.")

    @commands.hybrid_command(name="pause", description="Pause music.")
    @commands.guild_only()
    async def pause(self, ctx: commands.Context) -> None:
        voice = ctx.guild.voice_client
        if voice and voice.is_playing():
            voice.pause()
            await ctx.send("⏸️ Paused.")
        else:
            await ctx.send("❌ Nothing is playing.", ephemeral=True)

    @commands.hybrid_command(name="resume", description="Resume music.")
    @commands.guild_only()
    async def resume(self, ctx: commands.Context) -> None:
        voice = ctx.guild.voice_client
        if voice and voice.is_paused():
            voice.resume()
            await ctx.send("▶️ Resumed.")
        else:
            await ctx.send("❌ Music is not paused.", ephemeral=True)

    @commands.hybrid_command(name="queue", description="Show the music queue.")
    @commands.guild_only()
    async def queue(self, ctx: commands.Context) -> None:
        tracks = self.queues.get(ctx.guild.id, [])
        if not tracks:
            await ctx.send("The queue is empty.", ephemeral=True)
            return
        lines = [f"{index}. {track.title}" for index, track in enumerate(tracks, 1)]
        await ctx.send(embed=discord.Embed(title="Music queue", description="\n".join(lines[:20])))

    @commands.hybrid_command(name="volume", description="Set music volume from 1 to 100.")
    @commands.guild_only()
    async def volume(self, ctx: commands.Context, percent: commands.Range[int, 1, 100]) -> None:
        self.volumes[ctx.guild.id] = percent / 100
        voice = ctx.guild.voice_client
        if voice and isinstance(voice.source, discord.PCMVolumeTransformer):
            voice.source.volume = percent / 100
        await ctx.send(f"🔊 Volume set to {percent}%.")

    @commands.hybrid_command(name="stop", description="Stop music and clear the queue.")
    @commands.guild_only()
    async def stop(self, ctx: commands.Context) -> None:
        self.queues[ctx.guild.id] = []
        self.playback_generation[ctx.guild.id] = self.playback_generation.get(ctx.guild.id, 0) + 1
        if ctx.guild.voice_client:
            ctx.guild.voice_client.stop()
        await ctx.send("⏹️ Stopped and cleared the queue.")

    @commands.hybrid_command(name="leave", description="Disconnect the bot from voice.")
    @commands.guild_only()
    async def leave(self, ctx: commands.Context) -> None:
        self.queues[ctx.guild.id] = []
        self.playback_generation[ctx.guild.id] = self.playback_generation.get(ctx.guild.id, 0) + 1
        if ctx.guild.voice_client:
            await ctx.guild.voice_client.disconnect()
        await ctx.send("👋 Left the voice channel.")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        if self.bot.user is None or member.id != self.bot.user.id or before.channel is None or after.channel is not None:
            return
        guild_id = member.guild.id
        self.queues[guild_id] = []
        self.playback_generation[guild_id] = self.playback_generation.get(guild_id, 0) + 1


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
