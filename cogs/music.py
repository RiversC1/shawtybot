import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import syncedlyrics
import asyncio
import re
import random
import os
import logging

log = logging.getLogger("bot")

SPOTIFY_URL_RE = re.compile(r"https?://open\.spotify\.com/(track|playlist|album)/([A-Za-z0-9]+)")

_COOKIES = "/home/ubuntu/shawtybot/cookies.txt"

YDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "extractor_args": {"youtube": {"player_client": ["ios"]}},
    **({"cookiefile": _COOKIES} if os.path.exists(_COOKIES) else {}),
}

FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}


class GuildState:
    def __init__(self):
        self.queue: list[tuple[str, str]] = []  # (display title, url or search term)
        self.current: str | None = None
        self.volume: float = 1.0

    def shuffle(self):
        random.shuffle(self.queue)


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.states: dict[int, GuildState] = {}
        self.sp = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=os.getenv("SPOTIFY_CLIENT_ID"),
                client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
            ),
            requests_timeout=30,
        )

    def get_state(self, guild_id: int) -> GuildState:
        if guild_id not in self.states:
            self.states[guild_id] = GuildState()
        return self.states[guild_id]

    async def _extract(self, query: str, flat: bool = False) -> dict | None:
        opts = {**YDL_OPTS, "extract_flat": flat}
        loop = asyncio.get_running_loop()
        def _run():
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(query, download=False)
                if info and "entries" in info:
                    info = info["entries"][0] if info["entries"] else None
                return info
        try:
            return await loop.run_in_executor(None, _run)
        except Exception as e:
            log.error(f"yt-dlp failed for '{query}': {e}")
            return None

    async def search_tracks(self, query: str, count: int = 5) -> list[dict]:
        loop = asyncio.get_running_loop()
        def _run():
            opts = {**YDL_OPTS, "extract_flat": True, "noplaylist": False}
            with yt_dlp.YoutubeDL(opts) as ydl:
                results = ydl.extract_info(f"ytsearch{count}:{query}", download=False)
                return results.get("entries", []) if results else []
        try:
            return await loop.run_in_executor(None, _run)
        except Exception:
            return []

    async def play_next(self, guild_id: int):
        state = self.get_state(guild_id)
        guild = self.bot.get_guild(guild_id)
        vc: discord.VoiceClient | None = guild.voice_client if guild else None

        if not vc or not state.queue:
            state.current = None
            return

        title, search = state.queue.pop(0)
        state.current = title

        info = await self._extract(search)
        if not info or not info.get("url"):
            log.warning(f"No stream URL for '{title}', skipping")
            await self.play_next(guild_id)
            return

        def after(error):
            if error:
                log.error(f"Playback error: {error}")
            asyncio.run_coroutine_threadsafe(self.play_next(guild_id), self.bot.loop)

        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(info["url"], **FFMPEG_OPTS),
            volume=state.volume,
        )
        vc.play(source, after=after)
        log.info(f"Now playing: {title}")

    async def resolve_spotify(self, url: str) -> list[str]:
        match = SPOTIFY_URL_RE.match(url)
        if not match:
            return []
        loop = asyncio.get_running_loop()
        kind, spotify_id = match.groups()
        queries = []

        if kind == "track":
            t = await loop.run_in_executor(None, lambda: self.sp.track(spotify_id))
            queries.append(f"{t['artists'][0]['name']} - {t['name']}")
        elif kind == "playlist":
            results = await loop.run_in_executor(None, lambda: self.sp.playlist_tracks(spotify_id, market="US"))
            while results:
                for item in results["items"]:
                    t = item.get("track")
                    if t:
                        queries.append(f"{t['artists'][0]['name']} - {t['name']}")
                results = await loop.run_in_executor(None, lambda: self.sp.next(results)) if results["next"] else None
        elif kind == "album":
            results = await loop.run_in_executor(None, lambda: self.sp.album_tracks(spotify_id))
            for item in results["items"]:
                queries.append(f"{item['artists'][0]['name']} - {item['name']}")

        return queries

    async def query_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if not current or len(current) < 2 or current.startswith("http"):
            return []
        tracks = await self.search_tracks(current, 5)
        return [
            app_commands.Choice(
                name=t.get("title", "Unknown")[:100],
                value=t.get("webpage_url") or t.get("url") or current,
            )
            for t in tracks if t.get("title")
        ]

    @app_commands.command(name="play", description="Play a song by name, or a Spotify/YouTube link")
    @app_commands.describe(query="Song name, Spotify link, or YouTube link")
    @app_commands.autocomplete(query=query_autocomplete)
    async def play(self, interaction: discord.Interaction, query: str):
        log.info(f"{interaction.user} ran /play — query: {query}")

        if not interaction.user.voice:
            await interaction.response.send_message("You need to be in a voice channel first!", ephemeral=True)
            return

        await interaction.response.defer()

        vc: discord.VoiceClient = interaction.guild.voice_client
        if not vc:
            vc = await interaction.user.voice.channel.connect()

        state = self.get_state(interaction.guild_id)
        playing = vc.is_playing() or vc.is_paused()

        if "spotify.com" in query:
            try:
                search_queries = await self.resolve_spotify(query)
            except Exception as e:
                log.error(f"Spotify error: {e}")
                msg = (
                    "Spotify-curated playlists are restricted. Try a regular playlist."
                    if "404" in str(e)
                    else "Failed to fetch from Spotify."
                )
                await interaction.followup.send(msg, ephemeral=True)
                return

            if not search_queries:
                await interaction.followup.send("Couldn't find anything for that Spotify link.")
                return

            for q in search_queries:
                state.queue.append((q, f"ytsearch1:{q}"))

            if not playing:
                await self.play_next(interaction.guild_id)

            await interaction.followup.send(f"Added **{len(search_queries)}** track(s) from Spotify to the queue.")
            return

        # YouTube URL — extract title immediately so we can display it
        if "youtube.com" in query or "youtu.be" in query:
            info = await self._extract(query, flat=True)
            title = info.get("title", query) if info else query
            state.queue.append((title, query))
        else:
            # Plain text search
            results = await self.search_tracks(query, 1)
            if not results:
                await interaction.followup.send("No results found!")
                return
            title = results[0].get("title", query)
            url = results[0].get("webpage_url") or results[0].get("url") or f"ytsearch1:{query}"
            state.queue.append((title, url))

        if not playing:
            await self.play_next(interaction.guild_id)
            await interaction.followup.send(f"Now playing: **{state.current or title}**")
        else:
            await interaction.followup.send(f"Added **{title}** to the queue.")

    @app_commands.command(name="skip", description="Skip the current song")
    async def skip(self, interaction: discord.Interaction):
        log.info(f"{interaction.user} ran /skip")
        vc: discord.VoiceClient = interaction.guild.voice_client
        if not vc or not vc.is_playing():
            await interaction.response.send_message("Nothing is playing!", ephemeral=True)
            return
        vc.stop()
        await interaction.response.send_message("Skipped!")

    @app_commands.command(name="queue", description="Show the current queue")
    async def queue_cmd(self, interaction: discord.Interaction):
        log.info(f"{interaction.user} ran /queue")
        state = self.get_state(interaction.guild_id)
        vc: discord.VoiceClient = interaction.guild.voice_client

        if not state.current and not state.queue:
            await interaction.response.send_message("The queue is empty!", ephemeral=True)
            return

        lines = []
        if state.current:
            lines.append(f"**Now playing:** {state.current}")
        for i, (title, _) in enumerate(state.queue[:10], 1):
            lines.append(f"{i}. {title}")
        if len(state.queue) > 10:
            lines.append(f"...and {len(state.queue) - 10} more")
        await interaction.response.send_message("\n".join(lines))

    @app_commands.command(name="shuffle", description="Shuffle the current queue")
    async def shuffle(self, interaction: discord.Interaction):
        log.info(f"{interaction.user} ran /shuffle")
        state = self.get_state(interaction.guild_id)
        if not state.queue:
            await interaction.response.send_message("The queue is empty!", ephemeral=True)
            return
        state.shuffle()
        await interaction.response.send_message(f"Queue shuffled! ({len(state.queue)} tracks)")

    @app_commands.command(name="stop", description="Stop playback and disconnect")
    async def stop(self, interaction: discord.Interaction):
        log.info(f"{interaction.user} ran /stop")
        vc: discord.VoiceClient = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message("Not connected!", ephemeral=True)
            return
        state = self.get_state(interaction.guild_id)
        state.queue.clear()
        state.current = None
        await vc.disconnect()
        await interaction.response.send_message("Stopped and disconnected.")

    @app_commands.command(name="pause", description="Pause or resume playback")
    async def pause(self, interaction: discord.Interaction):
        log.info(f"{interaction.user} ran /pause")
        vc: discord.VoiceClient = interaction.guild.voice_client
        if not vc or (not vc.is_playing() and not vc.is_paused()):
            await interaction.response.send_message("Nothing is playing!", ephemeral=True)
            return
        if vc.is_paused():
            vc.resume()
            await interaction.response.send_message("Resumed!")
        else:
            vc.pause()
            await interaction.response.send_message("Paused!")

    @app_commands.command(name="lyrics", description="Get lyrics for the current song or a search query")
    @app_commands.describe(query="Song to get lyrics for (leave empty for current song)")
    async def lyrics(self, interaction: discord.Interaction, query: str = None):
        log.info(f"{interaction.user} ran /lyrics — query: {query}")

        if not query:
            state = self.get_state(interaction.guild_id)
            if not state.current:
                await interaction.response.send_message("Nothing is playing and no query provided!", ephemeral=True)
                return
            query = state.current

        await interaction.response.defer()

        loop = asyncio.get_running_loop()
        try:
            lrc = await loop.run_in_executor(None, lambda: syncedlyrics.search(query))
        except Exception as e:
            log.warning(f"Lyrics fetch failed: {e}")
            lrc = None

        if not lrc:
            await interaction.followup.send(f"No lyrics found for **{query}**.")
            return

        clean = re.sub(r"\[\d+:\d+\.\d+\]", "", lrc).strip()
        embed = discord.Embed(title=f"Lyrics — {query}", color=discord.Color.blurple())

        if len(clean) <= 4096:
            embed.description = clean
            await interaction.followup.send(embed=embed)
        else:
            chunks = [clean[i:i + 4096] for i in range(0, min(len(clean), 8192), 4096)]
            embed.description = chunks[0]
            await interaction.followup.send(embed=embed)
            for chunk in chunks[1:]:
                await interaction.followup.send(embed=discord.Embed(description=chunk, color=discord.Color.blurple()))

    @app_commands.command(name="volume", description="Set the volume (0-100)")
    @app_commands.describe(level="Volume level from 0 to 100")
    async def volume(self, interaction: discord.Interaction, level: int):
        log.info(f"{interaction.user} ran /volume {level}")
        if not 0 <= level <= 100:
            await interaction.response.send_message("Volume must be between 0 and 100.", ephemeral=True)
            return
        vc: discord.VoiceClient = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message("Not connected!", ephemeral=True)
            return
        state = self.get_state(interaction.guild_id)
        state.volume = level / 100
        if isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = state.volume
        await interaction.response.send_message(f"Volume set to **{level}%**")


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
