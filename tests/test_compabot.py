import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from Compabot import create_bot
from bot.features import install_features
from bot.storage import Store


class StorageTests(unittest.TestCase):
    def test_persistence_isolation_duplicates_and_delete_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bot.sqlite3'
            store = Store(path)
            args = (1, 10, 100, 2, 'Andrew', 'A quote', 'https://discord.com/channels/1/10/100', 3)
            self.assertTrue(store.save_quote(*args))
            self.assertFalse(store.save_quote(*args))
            self.assertIsNone(store.quote(2, 10))
            self.assertIsNone(store.quote(1, 11))
            quote_id = store.quote(1, 10)['id']
            self.assertFalse(store.remove_quote(1, quote_id, 4))
            self.assertFalse(store.remove_quote(2, quote_id, 4, True))
            store.add(1, 'game', 'Halo')
            self.assertFalse(store.add(1, 'game', 'halo'))
            self.assertEqual(store.items(2, 'game'), [])
            self.assertEqual(store.items(1, 'movie'), [])
            store.close()
            store = Store(path)
            self.assertEqual(store.quote(1, 10)['body'], 'A quote')
            self.assertEqual(store.items(1, 'game'), ['Halo'])
            self.assertTrue(store.remove_quote(1, quote_id, 2))
            self.assertTrue(store.remove(1, 'game', 'HALO'))
            store.close()


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = create_bot()
        self.bot.store = Store(':memory:')
        install_features(self.bot)

    async def asyncTearDown(self):
        await self.bot.close()

    def interaction(self, manage=False):
        return SimpleNamespace(guild_id=1, channel_id=10, user=SimpleNamespace(id=2),
                               permissions=SimpleNamespace(manage_messages=manage),
                               response=SimpleNamespace(send_message=AsyncMock()))

    async def test_registration_and_empty_pick(self):
        self.assertEqual({c.name for c in self.bot.tree.get_commands()},
                         {'say', 'Save quote', 'quote', 'quote_remove', 'favorites', 'pick'})
        for command in self.bot.tree.get_commands():
            command.to_dict(self.bot.tree)
        interaction = self.interaction()
        await self.bot.tree.get_command('pick').callback(interaction, 'game')
        self.assertIn('No game favorites', interaction.response.send_message.call_args.args[0])
        self.assertTrue(interaction.response.send_message.call_args.kwargs['ephemeral'])

    async def test_picker_and_mention_defaults(self):
        self.bot.store.add(1, 'game', '@everyone')
        interaction = self.interaction()
        await self.bot.tree.get_command('pick').callback(interaction, 'game')
        self.assertIn('@everyone', interaction.response.send_message.call_args.args[0])
        self.assertFalse(self.bot.allowed_mentions.everyone)
        self.assertFalse(self.bot.allowed_mentions.users)
        self.assertFalse(self.bot.intents.presences)

    async def test_removal_requires_permission(self):
        command = self.bot.tree.get_command('favorites').get_command('remove')
        from discord import app_commands
        with self.assertRaises(app_commands.MissingPermissions):
            command.checks[0](self.interaction())
        self.assertTrue(command.checks[0](self.interaction(True)))

    async def test_bot_messages_are_ignored(self):
        self.bot.process_commands = AsyncMock()
        await self.bot.on_message(SimpleNamespace(author=SimpleNamespace(bot=True)))
        self.bot.process_commands.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
