import asyncio
import logging
import os
import time
from pathlib import Path

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from auto_update import AutoUpdater
from features import install_features
from server_control import DEFAULT_SCRIPTS_DIR, ServerControl, ServerError, fit, install_server_commands
from storage import Store

BASE = Path(__file__).resolve().parent
log = logging.getLogger('compabot')

class CompaBot(commands.Bot):
    def __init__(self, guild_id=None, server_admins=(), scripts_dir=DEFAULT_SCRIPTS_DIR,
                 update_hours=0, update_channel_id=None):
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
        self.server = ServerControl(server_admins, scripts_dir)
        self.update_channel_id = update_channel_id
        self.updater = AutoUpdater(self.server, self.announce, update_hours) if update_hours > 0 else None
        self.update_task = None

    async def setup_hook(self):
        self.store = Store(BASE / 'data' / 'compabot.sqlite3')
        install_features(self)
        install_server_commands(self, self.server)
        if self.server.windows:
            self.auto_backup.start()
        if self.updater and self.server.windows and self.server.scripts_dir.is_dir():
            self.update_task = asyncio.create_task(self.updater.run())
        if self.guild_id:
            guild = discord.Object(id=self.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    @tasks.loop(minutes=5)
    async def auto_backup(self):
        """Hourly backups while someone is on the Icarus server."""
        try:
            result = await self.server.auto_backup()
        except ServerError as error:
            log.warning('Scheduled backup skipped: %s', error)
            return
        except Exception:  # keep the loop alive; the next check tries again
            log.exception('Scheduled backup check failed')
            return
        if isinstance(result, str):
            log.debug('Scheduled backup skipped: %s', result)
        elif result[0] not in (0, None):
            # Retried at the next check, since the newest backup is still old.
            log.warning('Scheduled backup failed (exit code %s)', result[0])

    async def close(self):
        self.auto_backup.cancel()
        if self.update_task:
            self.update_task.cancel()
        try:
            await super().close()
        finally:
            if self.store:
                self.store.close()
                self.store = None

    async def announce(self, text):
        """Post an automatic update message in AUTO_UPDATE_CHANNEL_ID, if set."""
        log.info('%s', text)
        if not self.update_channel_id:
            return
        await self.wait_until_ready()
        try:
            channel = self.get_channel(self.update_channel_id) or await self.fetch_channel(self.update_channel_id)
            await channel.send(fit(text))
        except discord.HTTPException:
            log.exception('Could not post in channel %s', self.update_channel_id)

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


def create_bot(guild_id=None, server_admins=(), scripts_dir=DEFAULT_SCRIPTS_DIR, update_hours=0, update_channel_id=None):
    bot = CompaBot(guild_id, server_admins, scripts_dir, update_hours, update_channel_id)

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
    raw_admins = [part.strip() for part in os.getenv('SERVER_ADMIN_IDS', '').split(',') if part.strip()]
    if not all(part.isdecimal() for part in raw_admins):
        raise SystemExit('SERVER_ADMIN_IDS must be comma-separated numeric Discord user IDs, or blank.')
    scripts_dir = os.getenv('ICARUS_SCRIPTS_DIR', '').strip() or DEFAULT_SCRIPTS_DIR
    raw_hours = os.getenv('AUTO_UPDATE_HOURS', '').strip() or '2'
    try:
        update_hours = float(raw_hours)
    except ValueError:
        raise SystemExit('AUTO_UPDATE_HOURS must be a number of hours, or 0 to turn automatic updates off.') from None
    raw_channel = os.getenv('AUTO_UPDATE_CHANNEL_ID', '').strip()
    if raw_channel and not raw_channel.isdecimal():
        raise SystemExit('AUTO_UPDATE_CHANNEL_ID must be a numeric Discord channel ID, or blank.')
    create_bot(int(raw_guild) if raw_guild else None, [int(part) for part in raw_admins], scripts_dir,
               update_hours, int(raw_channel) if raw_channel else None).run(token)
