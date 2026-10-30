"""Look after /server settings changes between commands, for each game that has settings.

Every minute it:
- writes the waiting changes into the settings file once the server is off, however it was
  stopped (a start through Compabot writes them too, just before the start script runs);
- restarts the server when someone asked for restart:True and the last player has left;
- once the server is back up, checks with the server itself that the changes took effect, and
  posts what changed in ALERT_CHANNEL_ID.
"""
import asyncio
import logging

from .control import ServerError, label
from .settings import same, show

log = logging.getLogger('compabot.settings')

EVERY = 60


class SettingsWatch:
    def __init__(self, control, notify):
        self.control = control
        self.notify = notify

    async def run(self):
        while True:
            await asyncio.sleep(EVERY)
            try:
                await self.check()
            except Exception:  # keep the loop alive; the next check tries again
                log.exception('%s settings check failed', self.control.game.name)

    async def check(self):
        control, store = self.control, self.control.settings
        waiting = store.pending()
        applied = await asyncio.to_thread(store.applied)
        if control.busy or not (waiting['changes'] or applied):
            return
        try:
            state = await control.status()
        except ServerError as error:
            log.warning('Could not check %s for its settings: %s', control.game.name, error)
            return
        if control.busy:  # something started during the status check
            return
        if not state['running']:
            if waiting['changes']:
                written = await asyncio.to_thread(store.apply_pending)
                log.info('Wrote the waiting %s settings while the server is off: %s', control.game.name, written)
            return
        if applied and state.get('ready'):
            await self.confirm(applied)
        if waiting['changes'] and waiting['restart'] and not state['players'].players:
            store.want_restart(False)
            keys = ', '.join(f'`{key}`' for key in waiting['changes'])
            try:
                text = await control.restart()
            except ServerError as error:
                text = str(error)
            await self.notify(f'Everyone left {label(control)}, so I restarted it to apply {keys}.\n{text}')

    async def confirm(self, applied):
        """Compare what was written with what the running server uses, then post the result."""
        store = self.control.settings
        try:
            live = await asyncio.to_thread(store.live)
        except (OSError, ValueError) as error:
            log.info('Could not read the running %s settings yet: %r', self.control.game.name, error)
            return
        done, wrong = [], []
        for key, change in applied.items():
            setting = store.catalogue.get(key)
            if setting is None:
                continue
            line = f"`{key}` {show(setting, change.get('old'))} → {show(setting, change['value'])} (set by {change['by']})"
            if key not in live or same(setting, live[key], change['value']):
                done.append(line)
            else:
                wrong.append(f'{line}, but the server is using {show(setting, live[key])}')
        store.clear_applied()
        text = f'{label(self.control)} settings now in effect:\n' + '\n'.join(done) if done else ''
        if wrong:
            text += ('\n' if text else '') + (
                f'{label(self.control)} did not pick up these settings:\n' + '\n'.join(wrong)
                + '\nA WorldOption.sav in the world folder, or a typo in the settings file, can cause this.')
        if text:
            await self.notify(text)
