"""Icarus's own /server commands: ores (the deep ore map) and mods."""
import asyncio
import io
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands

from ...servers.control import MAX_CHOICES, ServerError, fit
from .ore_deposits import ORE_MAP_SITE, ORES, ProspectError, listing, ore_label, read_deposits, summary

try:
    from . import ore_map
except ImportError:  # Pillow not installed yet; /server ores still lists the deposits
    ore_map = None

# Stay under Discord's 10 MB upload limit for servers without boosts.
MAX_ATTACHMENT = 9 * 1024 * 1024
# Prospect saves are around 1 MB; refuse anything far bigger before downloading it.
MAX_UPLOAD = 50 * 1024 * 1024


def install(server, shared):
    """Add the Icarus commands to the /server group. shared has what every /server command uses
    (admins, begin, needs, the game: choices and the world: autocomplete); see servers.commands."""
    admins, begin, needs, worlds, games, pick_game = (
        shared.admins, shared.begin, shared.needs, shared.worlds, shared.games, shared.pick_game)

    async def ores(interaction: discord.Interaction, current: str):
        if interaction.user.id not in admins:
            return []
        return [app_commands.Choice(name=ore_label(ore), value=ore) for ore in ORES
                if current.lower() in ore_label(ore).lower()][:MAX_CHOICES]

    async def read_upload(file):
        name = Path(file.filename).stem or 'Prospect'
        if not file.filename.lower().endswith('.json'):
            raise ServerError(f'`{discord.utils.escape_markdown(file.filename)}` is not a prospect save. Upload the .json '
                              r'from `%LOCALAPPDATA%\Icarus\Saved\PlayerData\<your Steam ID>\Prospects`.')
        if file.size > MAX_UPLOAD:
            raise ServerError(f'`{discord.utils.escape_markdown(file.filename)}` is too big to be a prospect save.')
        try:
            raw = await file.read()
            return (name, raw, *await asyncio.to_thread(read_deposits, raw))
        except (discord.HTTPException, ProspectError) as error:
            raise ServerError(f"Couldn't read `{discord.utils.escape_markdown(file.filename)}`: {error}") from None

    @server.command(name='ores', description='Icarus only: map the deep ore deposits and oil wells in a saved world.')
    @app_commands.describe(world='Saved world (prospect). Leave empty for the one the server loads.',
                           ore='Only show where this ore (or oil) is.',
                           file='A prospect .json from your own PC to read instead of a server world.',
                           game=pick_game)
    @app_commands.autocomplete(world=worlds, ore=ores)
    @app_commands.choices(game=games)
    async def deep_ores(interaction: discord.Interaction, world: Optional[str] = None, ore: Optional[str] = None,
                        file: Optional[discord.Attachment] = None, game: Optional[str] = None):
        if not (control := await begin(interaction, game)):
            return
        try:
            needs(control, 'ores', 'The ore map')
            if file:
                prospect, raw, found_map, deposits = await read_upload(file)
            else:
                prospect, raw, found_map, deposits = await asyncio.to_thread(control.worlds.read, world)
        except ServerError as error:
            await interaction.followup.send(str(error))
            return
        text = fit(summary(prospect, found_map, deposits, ore), 1800)
        files, size = [], 0
        if ore_map:
            image = await asyncio.to_thread(ore_map.render, prospect, found_map, deposits, ore)
            files.append(discord.File(io.BytesIO(image), filename=f'{prospect}-deep-ores.jpg'))
            size = len(image)
        else:
            text += '\nInstall the requirements (`pip install -r requirements.txt`) for a map picture.'
        files.append(discord.File(io.BytesIO(listing(prospect, found_map, deposits).encode('utf-8')),
                                  filename=f'{prospect}-deep-ores.txt'))
        if file:  # they already have the save they uploaded
            pass
        elif size + len(raw) <= MAX_ATTACHMENT:
            files.append(discord.File(io.BytesIO(raw), filename=f'{prospect}.json'))
            text += f'\nFor a map you can filter and zoom, open <{ORE_MAP_SITE}> and drop `{prospect}.json` on it.'
        else:
            text += f'\nThe save is too big to attach here, so get `{prospect}.json` from the server for <{ORE_MAP_SITE}>.'
        await interaction.followup.send(text, files=files)

    @server.command(name='mods', description='Icarus only: update the server mods to their latest release (stop the server first).')
    @app_commands.describe(game=pick_game)
    @app_commands.choices(game=games)
    async def mods(interaction: discord.Interaction, game: Optional[str] = None):
        if not (control := await begin(interaction, game)):
            return
        try:
            needs(control, 'mods', 'Mod updates')
            if (await control.status())['running']:
                raise ServerError(f'Stop the {control.game.name} server before updating its mods.')
            text = '\n'.join(await control.update_mods())
        except ServerError as error:
            text = str(error)
        await interaction.followup.send(text)
