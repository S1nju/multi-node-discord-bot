import discord
from discord.ext import commands
import asyncio
import os
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from typing import Optional
from src.checks import check_chat

def get_proxy():
    host = os.getenv("PROXY_HOST")
    if not host:
        return None
    port = os.getenv("PROXY_PORT", "823")
    user = os.getenv("PROXY_USER")
    pw = os.getenv("PROXY_PASSWORD")
    if user and pw:
        return f"http://{user}:{pw}@{host}:{port}"
    return f"http://{host}:{port}"

PROXY_URL = get_proxy()

def get_ffmpeg_options():
    before = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
    if PROXY_URL:
        before += f' -http_proxy "{PROXY_URL}"'
    return {
        'before_options': before,
        'options': '-vn'
    }

FFMPEG_OPTIONS = get_ffmpeg_options()

if not os.path.exists("./cache"):
    os.makedirs("./cache")

ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': './cache/%(extractor)s-%(id)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0', 
}
if PROXY_URL:
    ytdl_format_options['proxy'] = PROXY_URL

if os.path.exists('cookies.txt'):
    ytdl_format_options['cookiefile'] = 'cookies.txt'

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

SP_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SP_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
sp = None
if SP_CLIENT_ID and SP_CLIENT_SECRET:
    try:
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(client_id=SP_CLIENT_ID, client_secret=SP_CLIENT_SECRET))
    except Exception as e:
        print("Spotify auth failed:", e)

class PlayerControls(discord.ui.View):
    def __init__(self, vc, connection_lock, cog):
        super().__init__(timeout=None)
        self.vc = vc
        self._connection_lock = connection_lock
        self.cog = cog

    @discord.ui.button(label="⏸️ Pause", style=discord.ButtonStyle.secondary)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.vc and self.vc.is_playing():
            self.vc.pause()
            await interaction.response.send_message("⏸️ تم الإيقاف المؤقت (Paused)", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing is playing...", ephemeral=True)

    @discord.ui.button(label="▶️ Resume", style=discord.ButtonStyle.success)
    async def resume_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.vc and self.vc.is_paused():
            self.vc.resume()
            await interaction.response.send_message("▶️ تم الاستكمال (Resumed)", ephemeral=True)
        else:
            await interaction.response.send_message("Not paused...", ephemeral=True)

    @discord.ui.button(label="⏭️ Skip", style=discord.ButtonStyle.primary)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.vc and (self.vc.is_playing() or self.vc.is_paused()):
            self.vc.stop()
            await interaction.response.send_message("⏭️ تم التخطي (Skipped)", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing to skip...", ephemeral=True)

    @discord.ui.button(label="⏹️ Stop", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.cog.queue.clear()
        if self.vc and (self.vc.is_playing() or self.vc.is_paused()):
            self.vc.stop()
            await interaction.response.send_message("⏹️ تم الإيقاف و مسح القائمة (Stopped & Cleared)", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing to stop...", ephemeral=True)


class MusicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._connection_lock = asyncio.Lock()
        self.queue = []

    @commands.Cog.listener()
    async def on_ready(self):
        target_channel_id = getattr(self.bot, 'channel_id', None)
        if target_channel_id and str(target_channel_id).isdigit():
            target_voice_channel = self.bot.get_channel(int(target_channel_id))
            if target_voice_channel:
                async with self._connection_lock:
                    if not target_voice_channel.guild.voice_client:
                        try:
                            await target_voice_channel.connect(timeout=10.0, reconnect=True)
                            print(f"✅ Auto-Joined voice channel: {target_voice_channel.name}")
                        except Exception as e:
                            print(f"❌ Failed auto-join: {e}")

    async def search_ytdl(self, query: str):
        if not query.startswith('http'):
            query = f'ytsearch:{query}'
        
        loop = asyncio.get_event_loop()
        try:
            # Phase 1: Fast metadata extraction to detect exact ID and cache footprint
            data_meta = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False))
        except Exception as e:
            print(f"Failed yt-dlp metadata extraction: {e}")
            return None, None
            
        if 'entries' in data_meta:
            data_meta = data_meta['entries'][0]
            
        video_id = data_meta.get('id')
        filename = ytdl.prepare_filename(data_meta)
        
        if not video_id or os.path.exists(filename):
            # Already formally cached or uncacheable, return instantly!
            return filename, data_meta.get('title', 'Unknown Title')
            
        # Phase 2: Active download sequence protected by cross-container I/O locks
        lock_path = f"./cache/{video_id}.lock"
        
        while os.path.exists(lock_path):
            await asyncio.sleep(1)
            
        # Check one more time if the ghost process finished downloading it while we waited
        if os.path.exists(filename):
            return filename, data_meta.get('title', 'Unknown Title')
            
        try:
            # Trap the global cache lock for this track ID footprint
            with open(lock_path, 'w') as f:
                f.write(str(self.bot.bot_index))
                
            data_full = await loop.run_in_executor(None, lambda: ytdl.extract_info(data_meta['webpage_url'], download=True))
            if 'entries' in data_full:
                data_full = data_full['entries'][0]
            filename = ytdl.prepare_filename(data_full)
        except Exception as e:
            print(f"Failed yt-dlp locked extraction: {e}")
            return None, None
        finally:
            if os.path.exists(lock_path):
                try:
                    os.remove(lock_path)
                except:
                    pass
                    
        return filename, data_meta.get('title', 'Unknown Title')

    def play_next(self, error, target_channel):
        if error:
            print(f"Playback error: {error}")
            
        if len(self.queue) > 0:
            search_term, original_message = self.queue.pop(0)
            asyncio.run_coroutine_threadsafe(self.process_and_play(search_term, original_message, target_channel), self.bot.loop)
        else:
            print("Queue finished.")

    async def process_and_play(self, search_term, message, target_channel):
        audio_url, title = await self.search_ytdl(search_term)
        if not audio_url:
            await message.channel.send(f"❌ حدث خطأ في استخراج المسار من الطابور! (Failed to fetch track from queue): {search_term}")
            # Try next in queue if this fails
            self.play_next(None, target_channel)
            return
            
        await self.start_playback(audio_url, title, message, target_channel)

    async def start_playback(self, audio_url, title, message, target_channel):
        async with self._connection_lock:
            vc = message.guild.voice_client
            if not vc or not vc.is_connected():
                try:
                    vc = await target_channel.connect(timeout=10.0, reconnect=True)
                except Exception as e:
                    print(f"Voice Connection Error: {e}")
                    await message.channel.send("❌ Error connecting to voice channel. Please try again.")
                    return

            try:
                # If we are pulling a local cached file, we ditch the proxy and HTTP reconnect bindings!
                if audio_url.startswith("http"):
                    play_options = FFMPEG_OPTIONS
                else:
                    play_options = {'options': '-vn'}

                vc.play(discord.FFmpegPCMAudio(audio_url, **play_options), after=lambda e: self.play_next(e, target_channel))
                
                try:
                    await message.remove_reaction("🔍", self.bot.user)
                    await message.add_reaction("🎵")
                except:
                    pass
                
                view = PlayerControls(vc, self._connection_lock, self)
                embed = discord.Embed(
                    title="🎵 جاري التشغيل / Now Playing", 
                    description=f"**{title}**",
                    color=discord.Color.green()
                )
                await message.channel.send(embed=embed, view=view)
            except Exception as e:
                print(f"Playback error: {e}")
                await message.channel.send("❌ فشل التشغيل! يرجى المحاولة مرة أخرى. (Playback failed).")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.id != self.bot.user.id:
            return
            
        target_channel_id = getattr(self.bot, 'channel_id', None)
        if not target_channel_id or not str(target_channel_id).isdigit():
            return
            
        target_voice_channel = self.bot.get_channel(int(target_channel_id))
        if not target_voice_channel:
            return

        if after.channel != target_voice_channel:
            async with self._connection_lock:
                vc = member.guild.voice_client
                if after.channel is None:
                    if vc:
                        try:
                            await vc.disconnect(force=True)
                        except:
                            pass
                    try:
                        await target_voice_channel.connect(timeout=10.0, reconnect=True)
                    except:
                        pass
                else:
                    if vc and vc.is_connected():
                        await vc.move_to(target_voice_channel)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        play_letter = getattr(self.bot, 'play_letter', 'a')
        if not play_letter:
            return

        content = message.content.strip()
        letter_prefix = play_letter.lower() + " "
        
        if not content.lower().startswith(letter_prefix) and content.lower() != play_letter.lower():
            return

        raw_query = content[len(letter_prefix):].strip() if len(content) > len(letter_prefix) else ""
        if not raw_query:
            return

        target_channel_id = getattr(self.bot, 'channel_id', None)
        if target_channel_id and str(target_channel_id).isdigit():
            target_channel = self.bot.get_channel(int(target_channel_id))
        else:
            target_channel = message.author.voice.channel if message.author.voice else None

        if not target_channel:
            print("No target channel found.")
            await message.add_reaction("❌")
            return

        if getattr(self.bot, 'channel_id', None):
            if not message.author.voice or message.author.voice.channel.id != target_channel.id:
                await message.channel.send("❌ عذراً، لا يمكنك استخدام البوت إلا إذا كنت متواجداً في غرفته الصوتية! (You must be in the bot's designated voice channel to use commands.)")
                return

        lower_q = raw_query.lower()
        if lower_q in ("stop", "leave"):
            self.queue.clear()
            async with self._connection_lock:
                if message.guild.voice_client:
                    await message.guild.voice_client.disconnect(force=True)
            await message.add_reaction("⏹️")
            return
        elif lower_q in ("skip", "s", "سكب"):
            if message.guild.voice_client and message.guild.voice_client.is_playing():
                message.guild.voice_client.stop() 
            await message.add_reaction("⏭️")
            return
        elif lower_q in ("pause",):
            if message.guild.voice_client and message.guild.voice_client.is_playing():
                message.guild.voice_client.pause()
            await message.add_reaction("⏸️")
            return
        elif lower_q == "resume":
            if message.guild.voice_client and message.guild.voice_client.is_paused():
                message.guild.voice_client.resume()
            await message.add_reaction("▶️")
            return

        search_term = raw_query
        if lower_q.startswith("play "):
            search_term = raw_query[5:].strip()
        elif lower_q.startswith("p "):
            search_term = raw_query[2:].strip()

        if not search_term:
            return

        # Spotipy Parser
        if "open.spotify.com/track/" in search_term and sp:
            try:
                track = sp.track(search_term)
                search_term = f"{track['name']} {track['artists'][0]['name']}"
            except Exception as e:
                print("Failed to parse Spotify track:", e)
        elif "open.spotify.com/playlist/" in search_term and sp:
            try:
                playlist = sp.playlist_tracks(search_term)
                tracks = playlist['items']
                if len(tracks) > 0:
                    first_track = tracks[0]['track']
                    search_term = f"{first_track['name']} {first_track['artists'][0]['name']}"
                    for item in tracks[1:]:
                        if item['track']:
                            q_term = f"{item['track']['name']} {item['track']['artists'][0]['name']}"
                            self.queue.append((q_term, message))
                    await message.channel.send(f"✅ تم سحب قائمة Spotify! عدد المقاطع المضافة: {len(tracks)-1}")
            except Exception as e:
                print("Failed to parse Spotify playlist:", e)

        vc = message.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            self.queue.append((search_term, message))
            try:
                await message.remove_reaction("🔍", self.bot.user)
                await message.add_reaction("🎵")
            except:
                pass
            await message.channel.send(f"✅ تمت الإضافة إلى قائمة الانتظار (Added to queue)")
        else:
            await message.add_reaction("🔍")
            await self.process_and_play(search_term, message, target_channel)


    @commands.command(name="status", aliases=["info", "حالة"])
    @commands.check(check_chat)
    async def status(self, ctx: commands.Context):
        embed = discord.Embed(
            title=f"📡 حالة البوت #{self.bot.bot_index} (Native Player)",
            color=discord.Color.blue()
        )
        embed.add_field(name="حرف التفعيل", value=f"`{self.bot.play_letter}` (مثال: `{self.bot.play_letter} song name`)", inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="help", aliases=["مساعدة", "اوامر", "أوامر"])
    @commands.check(check_chat)
    async def help_cmd(self, ctx: commands.Context):
        letter = self.bot.play_letter
        embed = discord.Embed(
            title=f"📜 قائمة الأوامر للبوت المدمج #{self.bot.bot_index}",
            description=f"استخدم حرف التفعيل `{letter}`:",
            color=discord.Color.purple()
        )
        commands_list = [
            (f"🎵 تشغيل أغنية / اضافة للقائمة", f"`{letter} <اسم المقطع / رابط Spotify>`"),
            (f"⏭️ سكب / تخطي", f"`{letter} skip` / `{letter} s`"),
            (f"⏹️ إيقاف / مسح القائمة", f"`{letter} stop`"),
            (f"⏸️ إيقاف مؤقت", f"`{letter} pause`"),
            (f"▶️ استكمال", f"`{letter} resume`"),
        ]
        for name, value in commands_list:
            embed.add_field(name=name, value=value, inline=False)
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(MusicCog(bot))
