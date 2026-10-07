import logging
import random
from typing import Literal

import discord
from discord import app_commands

Category = Literal['game', 'movie', 'restaurant']
log = logging.getLogger('compabot.features')


def install_features(bot):
    @bot.tree.context_menu(name='Save quote')
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 5, key=lambda i: (i.guild_id, i.user.id))
    async def save_quote(interaction: discord.Interaction, message: discord.Message):
        if message.guild is None or message.guild.id != interaction.guild_id:
            await interaction.response.send_message('Save a message from this server.', ephemeral=True)
            return
        if not message.content.strip():
            await interaction.response.send_message('This quote book saves text messages only.', ephemeral=True)
            return
        saved = bot.store.save_quote(message.guild.id, message.channel.id, message.id,
                                     message.author.id, message.author.display_name,
                                     message.clean_content, message.jump_url, interaction.user.id)
        await interaction.response.send_message(
            'Saved, compa! Use /quote in the original channel.' if saved else 'That quote is already saved.', ephemeral=True)

    @bot.tree.command(description='Show a random saved quote from this channel.')
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 5, key=lambda i: (i.guild_id, i.user.id))
    async def quote(interaction: discord.Interaction):
        row = bot.store.quote(interaction.guild_id, interaction.channel_id)
        if row is None:
            await interaction.response.send_message('No quotes here yet. Right-click a text message > Apps > Save quote.', ephemeral=True)
            return
        embed = discord.Embed(description=row['body'][:4096], color=discord.Color.gold())
        embed.set_author(name=row['name'])
        embed.add_field(name='Original message', value=f"[Jump to message]({row['url']})")
        embed.set_footer(text=f"Quote #{row['id']}")
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(description='Delete a quote you wrote or saved; moderators can delete any.')
    @app_commands.guild_only()
    async def quote_remove(interaction: discord.Interaction, quote_id: int):
        removed = bot.store.remove_quote(interaction.guild_id, quote_id, interaction.user.id,
                                         interaction.permissions.manage_messages)
        await interaction.response.send_message('Quote removed.' if removed else 'Quote not found, or you cannot remove it.', ephemeral=True)

    favorites = app_commands.Group(name='favorites', description='Manage the server picks.', guild_only=True)

    @favorites.command(name='add', description='Add a game, movie, or restaurant to the server favorites.')
    async def add(interaction: discord.Interaction, category: Category, item: app_commands.Range[str, 1, 100]):
        item = item.strip()
        if not item:
            await interaction.response.send_message('Enter a name, compa.', ephemeral=True)
            return
        added = bot.store.add(interaction.guild_id, category, item)
        await interaction.response.send_message('Added!' if added else 'Already on the list.', ephemeral=True)

    @favorites.command(name='list', description='Show saved favorites, 15 per page.')
    async def list_items(interaction: discord.Interaction, category: Category, page: app_commands.Range[int, 1] = 1):
        items = bot.store.items(interaction.guild_id, category)
        pages = max(1, (len(items) + 14) // 15)
        if page > pages:
            await interaction.response.send_message(f'Choose a page between 1 and {pages}.', ephemeral=True)
            return
        body = '\n'.join(f'- {discord.utils.escape_markdown(item)}' for item in items[(page-1)*15:page*15])
        embed = discord.Embed(title=f'{category.title()} favorites', description=body or 'Nothing saved yet. Use /favorites add.')
        embed.set_footer(text=f'Page {page}/{pages}')
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @favorites.command(name='remove', description='Remove a favorite (Manage Messages required).')
    @app_commands.checks.has_permissions(manage_messages=True)
    async def remove(interaction: discord.Interaction, category: Category, item: app_commands.Range[str, 1, 100]):
        removed = bot.store.remove(interaction.guild_id, category, item.strip())
        await interaction.response.send_message('Removed.' if removed else 'That item is not on the list.', ephemeral=True)

    bot.tree.add_command(favorites)

    @bot.tree.command(description='Pick a random game, movie, or restaurant from server favorites.')
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 5, key=lambda i: (i.guild_id, i.user.id))
    async def pick(interaction: discord.Interaction, category: Category):
        items = bot.store.items(interaction.guild_id, category)
        if not items:
            await interaction.response.send_message(f'No {category} favorites yet. Add some with /favorites add.', ephemeral=True)
            return
        await interaction.response.send_message(f'Vamos, compa: **{discord.utils.escape_markdown(random.choice(items))}**')

    @bot.tree.error
    async def on_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            text = f'Slow down, compa. Try again in {error.retry_after:.0f}s.'
        elif isinstance(error, app_commands.MissingPermissions):
            text = 'You need Manage Messages in this channel for that command.'
        elif isinstance(error, app_commands.CheckFailure):
            text = 'That command is not available here.'
        else:
            log.error('Slash command failed', exc_info=(type(error), error, error.__traceback__))
            text = 'Something went wrong, compa. Check the bot logs.'
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
