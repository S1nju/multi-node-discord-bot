import discord
from discord.ext import commands
import asyncio
import os
import yt_dlp
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

ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
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

class PlayerControls(discord.ui.View):
    def __init__(self, vc, connection_lock):
        super().__init__(timeout=None)
        self.vc = vc
        self._connection_lock = connection_lock

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

    @discord.ui.button(label="⏭️ Skip / ⏹️ Stop", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.vc and (self.vc.is_playing() or self.vc.is_paused()):
            self.vc.stop()
            await interaction.response.send_message("⏹️ تم الإيقاف (Stopped)", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing to stop...", ephemeral=True)

class MusicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._connection_lock = asyncio.Lock()

    async def search_ytdl(self, query: str):
        # yt-dlp expects 'ytsearch:' prefix for generic searches
        if not query.startswith('http'):
            query = f'ytsearch:{query}'
        
        loop = asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False))
        except Exception as e:
            print(f"Failed yt-dlp extraction: {e}")
            return None, None
            
        if 'entries' in data:
            data = data['entries'][0]
            
        return data['url'], data.get('title', 'Unknown Title')

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        # Anti-Hijack / Unkickable protocol
        if member.id != self.bot.user.id:
            return
            
        target_channel_id = getattr(self.bot, 'channel_id', None)
        if not target_channel_id or not str(target_channel_id).isdigit():
            return
            
        target_voice_channel = self.bot.get_channel(int(target_channel_id))
        if not target_voice_channel:
            return

        # If the bot was moved or disconnected forcefully
        if after.channel != target_voice_channel:
            async with self._connection_lock:
                vc = member.guild.voice_client
                if vc:
                    # Force moved to another channel -> drag it immediately back
                    await vc.move_to(target_voice_channel)
                elif after.channel is None:
                    # Kicked intentionally -> violently connect back
                    try:
                        await target_voice_channel.connect(timeout=10.0, reconnect=True)
                    except Exception:
                        pass

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

        # STRICT LOCK: Only allow usage if the user is in the designated voice channel!
        if not message.author.voice or message.author.voice.channel.id != target_channel.id:
            await message.channel.send("❌ عذراً، لا يمكنك استخدام البوت إلا إذا كنت متواجداً في غرفته الصوتية! (You must be in the bot's designated voice channel to use commands.)")
            return

        lower_q = raw_query.lower()
        if lower_q in ("stop", "leave"):
            async with self._connection_lock:
                if message.guild.voice_client:
                    await message.guild.voice_client.disconnect(force=True)
            await message.add_reaction("⏹️")
            return
        elif lower_q in ("pause", "s"):
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

        async with self._connection_lock:
            vc = message.guild.voice_client
            if vc and not vc.is_connected():
                try:
                    await vc.disconnect(force=True)
                except:
                    pass
                vc = None

            if not vc:
                try:
                    vc = await target_channel.connect(timeout=10.0, reconnect=True)
                except Exception as e:
                    print(f"Voice Connection Error: {e}")
                    await message.add_reaction("❌")
                    await message.channel.send("❌ Error connecting to voice channel. Please try again.")
                    return

            if vc.is_playing():
                vc.stop()

        await message.add_reaction("🔍")
        
        audio_url, title = await self.search_ytdl(search_term)
        if not audio_url:
            await message.remove_reaction("🔍", self.bot.user)
            await message.add_reaction("❌")
            await message.channel.send("❌ حدث خطأ، يرجى المحاولة مرة أخرى! (Error fetching track, please try again).")
            return

        try:
            vc.play(discord.FFmpegPCMAudio(audio_url, **FFMPEG_OPTIONS))
            await message.remove_reaction("🔍", self.bot.user)
            await message.add_reaction("🎵")
            print(f"Now playing: {title}")
            
            # Send Interactive Message
            view = PlayerControls(vc, self._connection_lock)
            embed = discord.Embed(
                title="🎵 جاري التشغيل / Now Playing", 
                description=f"**{title}**",
                color=discord.Color.green()
            )
            await message.channel.send(embed=embed, view=view)
        except Exception as e:
            print(f"Playback error: {e}")
            await message.remove_reaction("🔍", self.bot.user)
            await message.add_reaction("❌")
            await message.channel.send("❌ فشل التشغيل! يرجى المحاولة مرة أخرى. (Playback failed, please try again).")
        if not audio_url:
            await message.remove_reaction("🔍", self.bot.user)
            await message.add_reaction("❌")
            return

        try:
            vc.play(discord.FFmpegPCMAudio(audio_url, **FFMPEG_OPTIONS))
            await message.remove_reaction("🔍", self.bot.user)
            await message.add_reaction("🎵")
            print(f"Now playing: {title}")
        except Exception as e:
            print(f"Playback error: {e}")
            await message.remove_reaction("🔍", self.bot.user)
            await message.add_reaction("❌")

    @commands.command(name="status", aliases=["info", "حالة"])
    @commands.check(check_chat)
    async def status(self, ctx: commands.Context):
        embed = discord.Embed(
            title=f"📡 حالة البوت #{self.bot.bot_index} (Native Invidious Player)",
            color=discord.Color.blue()
        )
        embed.add_field(name="حرف التفعيل", value=f"`{self.bot.play_letter}` (مثال: `{self.bot.play_letter} song name`)", inline=True)
        embed.add_field(name="النظام", value="Invidious API Native Parsing", inline=False)
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
            (f"🎵 تشغيل أغنية", f"`{letter} <اسم المقطع>`"),
            (f"⏹️ إيقاف / مغادرة", f"`{letter} stop`"),
            (f"⏸️ إيقاف مؤقت", f"`{letter} pause` / `{letter} s`"),
            (f"▶️ استكمال", f"`{letter} resume`"),
        ]
        for name, value in commands_list:
            embed.add_field(name=name, value=value, inline=False)
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(MusicCog(bot))
