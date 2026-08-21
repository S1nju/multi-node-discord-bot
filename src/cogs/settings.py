import discord
from discord.ext import commands
import asyncio
from src.store import CHAT_IDS, C247_IDS
from src.checks import check_chat

class SettingsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.id != self.bot.user.id:
            return

        c_247_id = C247_IDS.get(member.guild.id)
        if not c_247_id:
            return

        forced_move = False
        if after.channel is None:
            forced_move = True
        elif after.channel.id != c_247_id:
            forced_move = True

        if forced_move:
            print(f"Bot was moved/disconnected from 24/7 channel in {member.guild.id}. Waiting 20s before reconnecting...")
            
            if member.guild.voice_client:
                try:
                    await member.guild.voice_client.disconnect(force=True)
                except:
                    pass

            await asyncio.sleep(20.0)
            
            c_247_id_new = C247_IDS.get(member.guild.id)
            if not c_247_id_new:
                return
                
            channel = member.guild.get_channel(c_247_id_new)
            vc = member.guild.voice_client
            
            if channel:
                if not vc or getattr(vc.channel, 'id', None) != c_247_id_new:
                    try:
                        if vc:
                            await vc.disconnect(force=True)
                        await channel.connect()
                    except Exception as e:
                        print(f"Failed to reconnect back to 24/7 channel: {e}")

    @commands.command(name="chat")
    @commands.has_permissions(administrator=True)
    async def set_bot_chat(self, ctx, channel: discord.TextChannel = None):
        chat_channel = channel or ctx.channel
        CHAT_IDS[ctx.guild.id] = chat_channel.id
        await ctx.send(f"✅ تم تعيين شات الأوامر إلى {chat_channel.mention}")

    @commands.command(name="setting")
    @commands.check(check_chat)
    async def view_settings(self, ctx):
        chat_id = CHAT_IDS.get(ctx.guild.id)
        c_247_id = C247_IDS.get(ctx.guild.id)

        chat_mention = f"<#{chat_id}>" if chat_id else "بلا (الكل)"
        c247_mention = f"<#{c_247_id}>" if c_247_id else "معطل"

        embed = discord.Embed(title="⚙️ إعدادات البوت", color=discord.Color.blurple())
        embed.add_field(name="حرف التفعيل", value=f"`{getattr(self.bot, 'play_letter', 'a')}`", inline=True)
        embed.add_field(name="شات الأوامر", value=chat_mention, inline=True)
        embed.add_field(name="غرفة 24/7", value=c247_mention, inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="ping")
    @commands.check(check_chat)
    async def check_ping(self, ctx):
        latency = round(self.bot.latency * 1000)
        embed = discord.Embed(title="🏓 سرعة اتصال البوت", description=f"نبض البوت الحالي: `{latency}ms`", color=discord.Color.green())
        await ctx.send(embed=embed)

    @commands.command(name="come")
    @commands.has_permissions(administrator=True)
    async def toggle_247(self, ctx):
        if not ctx.author.voice:
            return await ctx.send("❌ يجب أن تكون في غرفة صوتية لتفعيل هذه الخاصية.")
        
        channel = ctx.author.voice.channel
        C247_IDS[ctx.guild.id] = channel.id
        
        if ctx.guild.voice_client:
            await ctx.guild.voice_client.disconnect(force=True)
        
        try:
            await channel.connect()
        except Exception as e:
            return await ctx.send(f"❌ حدث خطأ أثناء الاتصال: `{e}`")

        await ctx.send(f"✅ تم تفعيل وضع 24/7 والبقاء الدائم في الغرفة: {channel.mention}")

    @commands.command(name="leave_247")
    @commands.has_permissions(administrator=True)
    async def disable_247(self, ctx):
        C247_IDS.pop(ctx.guild.id, None)
        if ctx.guild.voice_client:
            await ctx.guild.voice_client.disconnect(force=True)
        await ctx.send("✅ تم تعطيل وضع 24/7 ومغادرة الغرفة بنجاح.")

async def setup(bot):
    await bot.add_cog(SettingsCog(bot))
