import logging
import os
import time
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

from features import install_features
from storage import Store

BASE = Path(__file__).resolve().parent
log = logging.getLogger('compabot')

class CompaBot(commands.Bot):
    def __init__(self, guild_id=None):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True
        intents.guild_messages = True
        intents.message_content = True
        super().__init__(command_prefix=commands.when_mentioned_or('!'),
                         intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.guild_id = guild_id
        self.greetings = {}
        self.store = None

    async def setup_hook(self):
        self.store = Store(BASE / 'data' / 'compabot.sqlite3')
        install_features(self)
        if self.guild_id:
            guild = discord.Object(id=self.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def close(self):
        try:
            await super().close()
        finally:
            if self.store:
                self.store.close()
                self.store = None

    async def on_ready(self):
        log.info('Logged in as %s (%s)', self.user, self.user.id)

    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return
        replies = {'hello': 'Saludos compa!', 'saludos': 'Saludos compa!', 'bye': 'Ahi nos vemos compa.'}
        reply = replies.get(message.content.strip().lower())
        now = time.monotonic()
        if reply and now - self.greetings.get(message.channel.id, -60) >= 30:
            self.greetings[message.channel.id] = now
            try:
                await message.channel.send(reply)
            except discord.HTTPException:
                log.exception('Could not send greeting')
        await self.process_commands(message)

    async def on_member_join(self, member):
        if member.guild.system_channel:
            try:
                await member.guild.system_channel.send(f'Well, well, well... {member.mention} decided to show up...')
            except discord.HTTPException:
                log.exception('Could not send welcome')

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.CommandOnCooldown):
            text = f'Slow down, compa. Try again in {error.retry_after:.0f}s.'
        elif isinstance(error, commands.UserInputError):
            text = 'Usage: !say your message (1-2,000 characters).'
        elif isinstance(error, commands.CheckFailure):
            text = 'You need Manage Messages in this channel to use say.'
        else:
            log.error('Command failed', exc_info=(type(error), error, error.__traceback__))
            text = 'Something went wrong, compa. Check the bot logs.'
        await ctx.send(text, ephemeral=ctx.interaction is not None)


def create_bot(guild_id=None):
    bot = CompaBot(guild_id)

    @bot.hybrid_command(description='Have Compabot repeat a message (Manage Messages required).')
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @commands.cooldown(1, 10, commands.BucketType.member)
    async def say(ctx, *, text: str):
        if not 1 <= len(text.strip()) <= 2000:
            raise commands.BadArgument('Invalid message length')
        await ctx.send(text)

    return bot


if __name__ == '__main__':
    load_dotenv(BASE / '.env')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    token = os.getenv('TOKEN', '').strip()
    if not token or token == 'your_discord_bot_token':
        raise SystemExit('Set TOKEN in .env before starting Compabot. See .env.example.')
    raw_guild = os.getenv('GUILD_ID', '').strip()
    if raw_guild and (not raw_guild.isdecimal() or int(raw_guild) <= 0):
        raise SystemExit('GUILD_ID must be your numeric Discord server ID, or blank.')
    create_bot(int(raw_guild) if raw_guild else None).run(token)
