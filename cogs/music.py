import discord
from discord.ext import commands
from discord import app_commands
import wavelink
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


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sp = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=os.getenv("SPOTIFY_CLIENT_ID"),
                client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
            ),
            requests_timeout=30,
        )

    @commands.Cog.listener()
    async def on_ready(self):
        if not wavelink.Pool.nodes:
            nodes = [wavelink.Node(uri="http://127.0.0.1:2333", password="shawtybot")]
            await wavelink.Pool.connect(nodes=nodes, client=self.bot)

    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload):
        log.info(f"Lavalink node ready: {payload.node.identifier}")

    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload):
        log.info(f"Now playing: {payload.track.title}")

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        player: wavelink.Player = payload.player
        if not player:
            return
        if player.queue:
            await player.play(player.queue.get())

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
        try:
            tracks = await wavelink.Playable.search(current, source=wavelink.TrackSource.YouTube)
            return [
                app_commands.Choice(name=t.title[:100], value=t.title)
                for t in tracks[:5]
            ]
        except Exception:
            return []

    @app_commands.command(name="play", description="Play a song by name, or a Spotify/YouTube link")
    @app_commands.describe(query="Song name, Spotify link, or YouTube link")
    @app_commands.autocomplete(query=query_autocomplete)
    async def play(self, interaction: discord.Interaction, query: str):
        log.info(f"{interaction.user} ran /play — query: {query}")

        if not interaction.user.voice:
            await interaction.response.send_message("You need to be in a voice channel first!", ephemeral=True)
            return

        await interaction.response.defer()

        player: wavelink.Player = interaction.guild.voice_client
        if not player:
            player = await interaction.user.voice.channel.connect(cls=wavelink.Player)

        if "spotify.com" in query:
            try:
                tracks = await wavelink.Playable.search(query)
            except Exception as e:
                log.error(f"Spotify error: {e}")
                await interaction.followup.send("Failed to fetch from Spotify.", ephemeral=True)
                return
            if not tracks:
                await interaction.followup.send("Couldn't find anything on Spotify for that link.")
                return

            if isinstance(tracks, wavelink.Playlist):
                added = len(tracks)
                for track in tracks:
                    await player.queue.put_wait(track)
                msg = f"Added **{added}** tracks from **{tracks.name}** to the queue."
            else:
                track = tracks[0]
                await player.queue.put_wait(track)
                msg = f"Added **{track.title}** to the queue."

            if not player.playing:
                await player.play(player.queue.get())

        else:
            tracks = await wavelink.Playable.search(query, source=wavelink.TrackSource.YouTube)
            if not tracks:
                await interaction.followup.send("No results found!")
                return

            if isinstance(tracks, wavelink.Playlist):
                added = len(tracks)
                for track in tracks:
                    await player.queue.put_wait(track)
                msg = f"Added **{added}** tracks from **{tracks.name}** to the queue."
            else:
                track = tracks[0]
                await player.queue.put_wait(track)
                msg = f"Added **{track.title}** to the queue."

            if not player.playing:
                await player.play(player.queue.get())
                if not isinstance(tracks, wavelink.Playlist):
                    msg = f"Now playing: **{track.title}**"

        await interaction.followup.send(msg)

    @app_commands.command(name="skip", description="Skip the current song")
    async def skip(self, interaction: discord.Interaction):
        log.info(f"{interaction.user} ran /skip")
        player: wavelink.Player = interaction.guild.voice_client
        if not player or not player.playing:
            await interaction.response.send_message("Nothing is playing!", ephemeral=True)
            return
        await player.skip()
        await interaction.response.send_message("Skipped!")

    @app_commands.command(name="queue", description="Show the current queue")
    async def queue(self, interaction: discord.Interaction):
        log.info(f"{interaction.user} ran /queue")
        player: wavelink.Player = interaction.guild.voice_client
        if not player or (not player.playing and not player.queue):
            await interaction.response.send_message("The queue is empty!", ephemeral=True)
            return
        lines = []
        if player.current:
            lines.append(f"**Now playing:** {player.current.title}")
        for i, track in enumerate(list(player.queue)[:10], 1):
            lines.append(f"{i}. {track.title}")
        if len(player.queue) > 10:
            lines.append(f"...and {len(player.queue) - 10} more")
        await interaction.response.send_message("\n".join(lines))

    @app_commands.command(name="shuffle", description="Shuffle the current queue")
    async def shuffle(self, interaction: discord.Interaction):
        log.info(f"{interaction.user} ran /shuffle")
        player: wavelink.Player = interaction.guild.voice_client
        if not player or not player.queue:
            await interaction.response.send_message("The queue is empty!", ephemeral=True)
            return
        player.queue.shuffle()
        await interaction.response.send_message(f"Queue shuffled! ({len(player.queue)} tracks)")

    @app_commands.command(name="stop", description="Stop playback and disconnect")
    async def stop(self, interaction: discord.Interaction):
        log.info(f"{interaction.user} ran /stop")
        player: wavelink.Player = interaction.guild.voice_client
        if not player:
            await interaction.response.send_message("Not connected!", ephemeral=True)
            return
        player.queue.clear()
        await player.disconnect()
        await interaction.response.send_message("Stopped and disconnected.")

    @app_commands.command(name="pause", description="Pause or resume playback")
    async def pause(self, interaction: discord.Interaction):
        log.info(f"{interaction.user} ran /pause")
        player: wavelink.Player = interaction.guild.voice_client
        if not player or not player.playing:
            await interaction.response.send_message("Nothing is playing!", ephemeral=True)
            return
        await player.pause(not player.paused)
        await interaction.response.send_message("Paused!" if player.paused else "Resumed!")

    @app_commands.command(name="lyrics", description="Get lyrics for the current song or a search query")
    @app_commands.describe(query="Song to get lyrics for (leave empty for current song)")
    async def lyrics(self, interaction: discord.Interaction, query: str = None):
        log.info(f"{interaction.user} ran /lyrics — query: {query}")

        if not query:
            player: wavelink.Player = interaction.guild.voice_client
            if not player or not player.current:
                await interaction.response.send_message("Nothing is playing and no query provided!", ephemeral=True)
                return
            query = player.current.title

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

        embed = discord.Embed(
            title=f"Lyrics — {query}",
            color=discord.Color.blurple()
        )

        if len(clean) <= 4096:
            embed.description = clean
            await interaction.followup.send(embed=embed)
        else:
            chunks = [clean[i:i+4096] for i in range(0, min(len(clean), 8192), 4096)]
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
        player: wavelink.Player = interaction.guild.voice_client
        if not player:
            await interaction.response.send_message("Not connected!", ephemeral=True)
            return
        await player.set_volume(level)
        await interaction.response.send_message(f"Volume set to **{level}%**")


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
