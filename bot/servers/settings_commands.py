"""/server settings: change a game's settings file from Discord (Palworld so far).

The settings file is only written while the server is off. A change made while it runs waits
for the next start, and with restart:True the server restarts to pick it up, but only once
nobody is on (straight away if it is already empty, otherwise when the last player leaves).
"""
import asyncio
from typing import Optional

import discord
from discord import app_commands

from .control import LABELS, MAX_CHOICES, ServerError, fit, online
from .settings import parse, same, show, suggestions


def install(server, shared, controls):
    """Add the settings group to /server, for the games whose profile has settings."""
    games = {key: control for key, control in controls.items() if control.game.supports('settings')}
    if not games:
        return
    default = next(iter(games.values()))
    admins = shared.admins
    choices = [app_commands.Choice(name=control.game.name, value=key) for key, control in games.items()]
    pick_game = f'Which game server. Leave empty for {default.game.name}.'
    group = app_commands.Group(name='settings', description="Change a game server's settings (server admins only).")

    def chosen(interaction):
        game = getattr(getattr(interaction, 'namespace', None), 'game', None)
        return games.get((game or default.game.key).lower())

    async def begin(interaction, game):
        if game is None:
            game = default.game.key
        control = await shared.begin(interaction, game)
        if control:
            try:
                shared.needs(control, 'settings', 'Changing settings')
            except ServerError as error:
                await interaction.followup.send(str(error))
                return None
        return control

    def find(control, key):
        setting = control.settings.catalogue.get((key or '').strip())
        if setting is None:
            lowered = (key or '').strip().lower()
            setting = next((s for s in control.settings.catalogue.values() if s.key.lower() == lowered), None)
        if setting is None:
            raise ServerError(f'`{discord.utils.escape_markdown(key or "")}` is not a {control.game.name} setting '
                              'Compabot can change. Pick one from the list as you type.')
        return setting

    async def settings_autocomplete(interaction: discord.Interaction, current: str):
        control = chosen(interaction)
        if interaction.user.id not in admins or not control:
            return []
        try:
            values = await asyncio.to_thread(control.settings.read)
        except ServerError:
            values = {}
        typed = current.lower()
        found = []
        for setting in control.settings.catalogue.values():
            if typed in setting.key.lower() or typed in setting.label.lower():
                now = f' (now {show(setting, values[setting.key])})' if values.get(setting.key) is not None else ''
                found.append(app_commands.Choice(name=f'{setting.key} · {setting.label}{now}'[:100], value=setting.key))
        return found[:MAX_CHOICES]

    async def value_autocomplete(interaction: discord.Interaction, current: str):
        control = chosen(interaction)
        if interaction.user.id not in admins or not control:
            return []
        key = getattr(interaction.namespace, 'setting', None)
        try:
            setting = find(control, key)
        except ServerError:
            return []
        try:
            now = (await asyncio.to_thread(control.settings.read)).get(setting.key)
        except ServerError:
            now = None
        default = (await asyncio.to_thread(control.settings.defaults)).get(setting.key)
        return [app_commands.Choice(name=name, value=value)
                for name, value in suggestions(setting, now, default, current)][:MAX_CHOICES]

    @group.command(name='set', description='Change one game server setting. Waits for the next start if the server is on.')
    @app_commands.describe(setting='The setting to change. Type to search.',
                           value='The new value. Pick one, or type a number in the range shown.',
                           restart='Restart to apply it now, once nobody is on (straight away if the server is empty).',
                           game=pick_game)
    @app_commands.autocomplete(setting=settings_autocomplete, value=value_autocomplete)
    @app_commands.choices(game=choices)
    async def set_setting(interaction: discord.Interaction, setting: str, value: str, restart: bool = False,
                          game: Optional[str] = None):
        if not (control := await begin(interaction, game)):
            return
        try:
            text = await change(control, find(control, setting), value, restart, interaction.user.display_name)
        except ServerError as error:
            text = str(error)
        await interaction.followup.send(fit(text))

    async def change(control, setting, typed, restart, who):
        store = control.settings
        value = parse(setting, typed)
        if world := await asyncio.to_thread(store.world_option):
            raise ServerError(f'`{world}` overrides the settings file, so a change there would do nothing. '
                              'Stop the server, back up that world folder and delete WorldOption.sav first.')
        old = (await asyncio.to_thread(store.read)).get(setting.key)
        state = await control.status()
        name = f'**{control.name}**'
        what = f'`{setting.key}` {show(setting, old)} → {show(setting, value)}'
        if not state['running'] and not control.busy:
            dropped = store.cancel(setting.key)
            if same(setting, value, old):
                return f'`{setting.key}` is already {show(setting, value)}.' + (' Dropped the change that was waiting.' if dropped else '')
            await asyncio.to_thread(store.write, {setting.key: value}, who)
            text = f'{what}. Saved to the settings file; it takes effect when {name} starts.'
            if restart:
                text += f" It's off, so there was nothing to restart. Start it with {control.slash('start')}."
            return text
        waiting = store.add_pending(setting.key, value, old, who)['changes']
        if setting.key not in waiting:
            text = f'`{setting.key}` is already {show(setting, value)} in the settings file, so nothing is waiting for it now.'
            return text + waiting_line(control, waiting)
        if not state['running']:  # off, but busy with an update or similar
            return f'{what} is waiting. {control.game.name} is busy right now; the change is written before it next starts.'
        players = state['players']
        if not restart:
            who_on = f'{online(players)}, so I' if players.players else 'I'
            return (f'{what} is waiting. {name} is running and {who_on} did not restart it. It takes effect at the next start '
                    f"(or use `restart:True` to restart once nobody is on).{waiting_line(control, waiting, setting.key)}")
        if players.players or control.busy:
            store.want_restart()
            reason = online(players) if players.players else f'{LABELS[control.busy]} is running'
            return f'{what} is waiting. {reason}, so I will restart {name} once nobody is on.'
        text = f'{what}. Nobody is on, so restarting {name} to apply it.\n'
        return text + await control.restart()

    def waiting_line(control, waiting, skip=None):
        others = [key for key in waiting if key != skip]
        return f'\nAlso waiting: {", ".join(f"`{key}`" for key in others)}.' if others else ''

    @group.command(name='show', description="Show a setting's value, range and default, or every setting changed from its default.")
    @app_commands.describe(setting='One setting to show. Leave empty to list everything changed from the default.',
                           game=pick_game)
    @app_commands.autocomplete(setting=settings_autocomplete)
    @app_commands.choices(game=choices)
    async def show_settings(interaction: discord.Interaction, setting: Optional[str] = None, game: Optional[str] = None):
        if not (control := await begin(interaction, game)):
            return
        try:
            text = await describe_one(control, find(control, setting)) if setting else await describe_all(control)
        except ServerError as error:
            text = str(error)
        await interaction.followup.send(fit(text))

    async def describe_one(control, setting):
        store = control.settings
        values = await asyncio.to_thread(store.read)
        default = (await asyncio.to_thread(store.defaults)).get(setting.key)
        lines = [f'**{setting.key}**: {setting.label}.',
                 f'Settings file: {show(setting, values.get(setting.key))}. Default: {show(setting, default)}.',
                 f'Allowed: {setting.allowed()}.']
        if setting.kind == 'choice':
            lines[-1] = 'Allowed: ' + '; '.join(f'{value} ({meaning})' for value, meaning in setting.options) + '.'
        waiting = store.pending()['changes'].get(setting.key)
        if waiting:
            lines.append(f"Waiting for the next start: {show(setting, waiting['value'])} (set by {waiting['by']}).")
        if (live := await live_values(control)) and setting.key in live:
            lines.append(f'The running server is using: {show(setting, live[setting.key])}.')
        return '\n'.join(lines)

    async def describe_all(control):
        store = control.settings
        values = await asyncio.to_thread(store.read)
        defaults = await asyncio.to_thread(store.defaults)
        changed = [f'`{key}` {show(s, values[key])} (default {show(s, defaults[key])})'
                   for key, s in store.catalogue.items()
                   if values.get(key) is not None and not same(s, values[key], defaults[key])]
        lines = [f'**{control.game.name} settings changed from the default:** ' + (', '.join(changed) if changed else 'none.')]
        lines += pending_lines(control)
        return '\n'.join(lines)

    async def live_values(control):
        if not control.settings.live or not (await control.status())['running']:
            return None
        try:
            return await asyncio.to_thread(control.settings.live)
        except (OSError, ValueError):
            return None

    def pending_lines(control):
        state = control.settings.pending()
        if not state['changes']:
            return ['Nothing is waiting for the next start.']
        catalogue = control.settings.catalogue
        lines = ['**Waiting for the next start:**']
        for key, change in state['changes'].items():
            setting = catalogue[key]
            lines.append(f"`{key}` {show(setting, change.get('old'))} → {show(setting, change['value'])} (set by {change['by']})")
        if state['restart']:
            lines.append('I will restart the server to apply these once nobody is on.')
        return lines

    @group.command(name='pending', description='List the setting changes waiting for the next start.')
    @app_commands.describe(game=pick_game)
    @app_commands.choices(game=choices)
    async def pending(interaction: discord.Interaction, game: Optional[str] = None):
        if not (control := await begin(interaction, game)):
            return
        await interaction.followup.send(fit('\n'.join(pending_lines(control))))

    @group.command(name='cancel', description='Drop a setting change that is waiting for the next start, or all of them.')
    @app_commands.describe(setting='The waiting change to drop. Leave empty to drop them all.', game=pick_game)
    @app_commands.autocomplete(setting=settings_autocomplete)
    @app_commands.choices(game=choices)
    async def cancel(interaction: discord.Interaction, setting: Optional[str] = None, game: Optional[str] = None):
        if not (control := await begin(interaction, game)):
            return
        try:
            key = find(control, setting).key if setting else None
            dropped = control.settings.cancel(key)
            text = (f'Dropped {", ".join(f"`{k}`" for k in dropped)}.' if dropped
                    else f'`{key}` had no change waiting.' if key else 'Nothing was waiting.')
        except ServerError as error:
            text = str(error)
        await interaction.followup.send(text)

    @group.command(name='undo', description='Put back the settings file from before the last change (server must be off).')
    @app_commands.describe(game=pick_game)
    @app_commands.choices(game=choices)
    async def undo(interaction: discord.Interaction, game: Optional[str] = None):
        if not (control := await begin(interaction, game)):
            return
        try:
            control.check_free()
            if (await control.status())['running']:
                raise ServerError(f"Stop **{control.name}** first: the settings file is only changed while it is off. "
                                  f"To drop a change that is still waiting, use `/server settings cancel`.")
            name = await asyncio.to_thread(control.settings.undo, interaction.user.display_name)
            text = f'Put back the settings file from `{name}`. It takes effect when **{control.name}** starts.'
        except (ServerError, OSError) as error:
            text = str(error)
        await interaction.followup.send(text)

    server.add_command(group)
