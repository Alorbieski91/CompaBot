"""The /server commands every game has: status, start, stop, restart, backup and update.

A game with commands of its own (Icarus: ores and mods) adds them to the same /server group
from its folder in bot/games, through its profile's commands hook.
"""
import time
from types import SimpleNamespace
from typing import Optional

import discord
from discord import app_commands

from ..games import DEFAULT_GAME
from .control import LABELS, MAX_CHOICES, ServerControl, ServerError, describe, fit, report


def install_server_commands(bot, controls):
    """Add the /server group. controls maps each game's key to its ServerControl (a lone ServerControl works too)."""
    if isinstance(controls, ServerControl):
        controls = {controls.game.key: controls}
    default = controls.get(DEFAULT_GAME) or next(iter(controls.values()))
    admins = default.admins
    games = [app_commands.Choice(name=control.game.name, value=key) for key, control in controls.items()]
    pick_game = f'Which game server. Leave empty for {default.game.name}.'
    server = app_commands.Group(name='server', description='Control the game servers (server admins only).', guild_only=True)

    def pick(game):
        """The ServerControl for a game: option; blank means the default game."""
        control = controls.get((game or default.game.key).strip().lower())
        if control is None:
            known = ', '.join(c.game.name for c in controls.values())
            raise ServerError(f'Compabot does not run a `{discord.utils.escape_markdown(game)}` server. Games: {known}.')
        return control

    def needs(control, feature, what):
        """Refuse an extra (mods, worlds, ores) the chosen game does not have."""
        if not control.game.supports(feature):
            others = ' or '.join(c.game.name for c in controls.values() if c.game.supports(feature))
            raise ServerError(f'{what} only works for {others}, not {control.game.name}.')

    async def begin(interaction, game):
        """Check who is asking and pick the game, then show that the bot is thinking. Returns the control or None."""
        if interaction.user.id not in admins:
            await interaction.response.send_message('Only the server admins can use /server, compa.', ephemeral=True)
            return None
        try:
            control = pick(game)
        except ServerError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return None
        if not control.windows:
            await interaction.response.send_message('Server commands only work when Compabot runs on the Windows server host.', ephemeral=True)
            return None
        await interaction.response.defer(thinking=True)
        return control

    def chosen(interaction):
        """The control for the game: option typed so far, for autocomplete; None if it has no such game."""
        game = getattr(getattr(interaction, 'namespace', None), 'game', None)
        return controls.get((game or default.game.key).lower())

    @server.command(name='status', description='Show whether a game server is running and when it was last backed up.')
    @app_commands.describe(game=pick_game)
    @app_commands.choices(game=games)
    async def status(interaction: discord.Interaction, game: Optional[str] = None):
        if not (control := await begin(interaction, game)):
            return
        try:
            text = describe(control, await control.status())
        except ServerError as error:
            text = str(error)
        await interaction.followup.send(text)

    async def worlds(interaction: discord.Interaction, current: str):
        control = chosen(interaction)
        if interaction.user.id not in admins or not control or not control.game.supports('worlds'):
            return []
        names = [name for name in control.worlds.names() if current.lower() in name.lower()]
        return [app_commands.Choice(name=name, value=name) for name in names[:MAX_CHOICES]]

    @server.command(name='start', description='Back up and start a game server, optionally updating it (and its mods) first.')
    @app_commands.describe(update='Update the game (and its mods) before starting.',
                           world='Icarus only: saved world (prospect) to load. Leave empty to load the last one.',
                           game=pick_game)
    @app_commands.autocomplete(world=worlds)
    @app_commands.choices(game=games)
    async def start(interaction: discord.Interaction, update: bool = False, world: Optional[str] = None,
                    game: Optional[str] = None):
        if not (control := await begin(interaction, game)):
            return
        began = time.monotonic()
        try:
            if world:
                needs(control, 'worlds', 'Picking a world')
            if not update and not world:
                text = report('Start', *await control.run_action('start'))
            elif not update:
                text = f'Loading **{await control.worlds.choose(world)}**.\n' + report('Start', *await control.run_action('start'))
            else:
                text = await update_then_start(control, world, began)
        except ServerError as error:
            text = str(error)
        await interaction.followup.send(fit(text))

    async def update_then_start(control, world, began):
        if world:
            control.worlds.find(world)  # refuse an unknown world before spending time on the update
        code, text = await control.update_with_mods()
        if code is None:
            again = f' world:{control.worlds.find(world)}' if world else ''
            return text + f" After that, start it with {control.slash('start' + again)}."
        if code != 0:
            return text + '\nThe server was not started.'
        try:
            if world:
                text += f'\nLoading **{await control.worlds.choose(world)}**.'
            # Start after the mods are swapped, within what is left of the reply window.
            window = max(1, control.reply_window - (time.monotonic() - began))
            return text + '\n' + report('Start', *await control.run_action('start', window=window))
        except ServerError as error:
            return text + f'\n{error}\nThe server was not started.'
    @server.command(name='stop', description='Close a game server safely and take a final backup.')
    @app_commands.describe(force='Stop it even if players are online.', game=pick_game)
    @app_commands.choices(game=games)
    async def stop(interaction: discord.Interaction, force: bool = False, game: Optional[str] = None):
        if not (control := await begin(interaction, game)):
            return
        try:
            text = report('Stop', *await control.stop(force))
        except ServerError as error:
            text = str(error)
        await interaction.followup.send(text)

    @server.command(name='restart', description='Stop a game server, wait for it to close, then start it again (only when nobody is on).')
    @app_commands.describe(game=pick_game)
    @app_commands.choices(game=games)
    async def restart(interaction: discord.Interaction, game: Optional[str] = None):
        if not (control := await begin(interaction, game)):
            return
        try:
            text = await control.restart()
        except ServerError as error:
            text = str(error)
        await interaction.followup.send(fit(text))

    @server.command(name='backup', description="Back up a game server's save files now.")
    @app_commands.describe(game=pick_game)
    @app_commands.choices(game=games)
    async def backup(interaction: discord.Interaction, game: Optional[str] = None):
        if not (control := await begin(interaction, game)):
            return
        try:
            text = report(LABELS['backup'], *await control.run_action('backup'))
        except ServerError as error:
            text = str(error)
        await interaction.followup.send(text)

    @server.command(name='update', description='Update a game server through SteamCMD, then its mods (stop it first).')
    @app_commands.describe(game=pick_game)
    @app_commands.choices(game=games)
    async def update(interaction: discord.Interaction, game: Optional[str] = None):
        if not (control := await begin(interaction, game)):
            return
        try:
            text = (await control.update_with_mods())[1]
        except ServerError as error:
            text = str(error)
        await interaction.followup.send(text)

    # Each game's own commands, once per game.
    shared = SimpleNamespace(admins=admins, begin=begin, needs=needs, worlds=worlds, games=games, pick_game=pick_game)
    hooks = dict.fromkeys(control.game.commands for control in controls.values() if control.game.commands)
    for add_commands in hooks:
        add_commands(server, shared)

    bot.tree.add_command(server)
