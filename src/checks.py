from discord.ext import commands
import discord
from src.store import CHAT_IDS

async def check_chat(ctx: commands.Context) -> bool:
    """Check if the command is executed in the designated command channel (or if none is set)."""
    if not ctx.guild:
        return False
    chat_id = CHAT_IDS.get(ctx.guild.id)
    if chat_id is None:
        return True
    
    if ctx.channel.id != chat_id:
        try:
            designated = ctx.guild.get_channel(chat_id)
            if designated:
                await ctx.send(f"⚠️ يرجى استخدام الأوامر في الشات المخصص: {designated.mention}", delete_after=10)
            else:
                await ctx.send("⚠️ لا يمكن العثور على الشات المخصص، يرجى تعيين شات جديد.", delete_after=10)
        except Exception:
            pass
        return False
    return True
