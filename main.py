import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
from logging import getLogger

logger = getLogger(__name__)
load_dotenv()

class MusicBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        
        self.bot_index = os.getenv("BOT_INDEX", "1")
        self.channel_id = os.getenv(f"BOT_CHANNEL_ID{self.bot_index}") or os.getenv("BOT_CHANNEL_ID")
        self.play_letter = os.getenv(f"BOT_PLAY_LETTER{self.bot_index}") or os.getenv("BOT_PLAY_LETTER", "a")
        prefix = os.getenv(f"BOT_PREFIX{self.bot_index}") or os.getenv("BOT_PREFIX", "-")
        
        super().__init__(command_prefix=commands.when_mentioned_or(prefix), intents=intents, help_command=None)
        self.output_vc = None

    async def setup_hook(self):
        await self.load_extension("src.cogs.music")

    async def on_ready(self):
        print(f"Logged in as {self.user} (Bot #{self.bot_index}) - Trigger Letter: '{self.play_letter}'")
        print(f"✅ [Bot {self.bot_index}] Running native yt-dlp audio framework.")

bot = MusicBot()

if __name__ == "__main__":
    bot_index = os.getenv("BOT_INDEX", "1")
    token = os.getenv(f"BOT_TOKEN{bot_index}") or os.getenv("BOT_TOKEN")
    if token:
        bot.run(token)
    else:
        print(f"Please provide a valid BOT_TOKEN{bot_index} in the .env file.")
