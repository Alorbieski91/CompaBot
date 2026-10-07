import asyncio
import io
import os
import socket
import struct
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.icarus.monitor import ServerMonitor, crash_archive, crash_summary, latest_crash, post
from bot.icarus.server_control import ServerError
from bot.icarus.steam_query import Info, parse_info, query_info

CONTEXT = """<?xml version="1.0" encoding="UTF-8"?>
<FGenericCrashContext><RuntimeProperties>
<ErrorMessage>Unhandled Exception: EXCEPTION_ACCESS_VIOLATION reading address 0xffffffffffffffff</ErrorMessage>
<CallStack>IcarusServer_Win64_Shipping!FObjectReplicator::PostSendBunch() [C:\\BA\\work\\Engine\\DataReplication.cpp:1818]
IcarusServer_Win64_Shipping!UActorChannel::ReplicateActor() [C:\\BA\\work\\DataChannel.cpp:3226]</CallStack>
</RuntimeProperties></FGenericCrashContext>
"""


def info_reply(players, max_players):
    return (b'\xff\xff\xff\xffI\x11' + b'CompaWorld\x00' + b'Map\x00' + b'Icarus\x00' + b'Icarus\x00'
            + struct.pack('<HBBB', 0, players, max_players, 0) + b'dw\x00\x01')


class SteamQueryTests(unittest.TestCase):
    def test_parse_info(self):
        self.assertEqual(parse_info(info_reply(1, 2)), Info(1, 2))
        self.assertIsNone(parse_info(b'\xff\xff\xff\xffX'))
        self.assertIsNone(parse_info(info_reply(1, 2)[:20]))

    def test_query_answers_challenge(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(('127.0.0.1', 0))
        received = []

        def serve():
            data, addr = server.recvfrom(1400)
            received.append(data)
            server.sendto(b'\xff\xff\xff\xffA1234', addr)
            data, addr = server.recvfrom(1400)
            received.append(data)
            server.sendto(info_reply(2, 2), addr)

        thread = threading.Thread(target=serve)
        thread.start()
        try:
            self.assertEqual(query_info(server.getsockname()[1]), Info(2, 2))
        finally:
            thread.join(5)
            server.close()
        self.assertTrue(received[1].endswith(b'\x001234'))

    def test_query_returns_none_when_nothing_answers(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        try:
            self.assertIsNone(query_info(port, timeout=0.2))
        finally:
            sock.close()


class FakeControl:
    def __init__(self, root):
        self.name = 'CompaWorld'
        self.install_root = Path(root)
        self.query_port = 27015
        self.stopped_at = float('-inf')
        self.info = None
        self.running = False
        self.ready = True
        self.started = 1_000

    def query(self, port):
        return self.info

    async def status(self):
        if not self.running:
            return {'running': False}
        return {'running': True, 'started': self.started, 'players': Info(1, 2, ('Legiterately',))}

    def listening(self, started):
        return self.ready


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.control = FakeControl(self.folder.name)
        self.now = 10_000
        self.monitor = ServerMonitor(self.control, clock=lambda: self.now)

    def tearDown(self):
        self.folder.cleanup()

    async def poll(self, times=1):
        result = None
        for _ in range(times):
            result = self.monitor.step(*await self.monitor.observe()) or result
            self.now += 60
        return result

    def up(self, players=1):
        self.control.running, self.control.info = True, Info(players, 2)

    def down(self):
        self.control.running, self.control.info = False, None

    def crash(self, stamp):
        folder = self.control.install_root / 'data' / 'Saved' / 'Crashes' / f'UE4CC-Windows-{stamp}_0000'
        folder.mkdir(parents=True)
        (folder / 'CrashContext.runtime-xml').write_text(CONTEXT)
        (folder / 'Icarus.log').write_text('LogInit: hello\n' * 1000)
        os.utime(folder, (stamp, stamp))
        return folder

    async def test_state_at_startup_is_not_announced(self):
        self.up()
        self.assertIsNone(await self.poll(3))
        self.assertEqual(self.monitor.state, 'up')

    async def test_one_missed_poll_is_not_announced(self):
        self.up()
        await self.poll()
        self.down()
        self.assertIsNone(await self.poll())
        self.up()
        self.assertIsNone(await self.poll(3))

    async def test_crash_is_announced_with_its_report(self):
        self.up()
        await self.poll()
        self.crash(1)  # From before this run of the server: ignored.
        folder = self.crash(self.now + 30)
        self.down()
        self.assertIsNone(await self.poll())
        text, crash = await self.poll()
        self.assertEqual(crash, folder)
        self.assertIn(f'**CompaWorld** crashed <t:{self.now - 90}:R>.', text)
        self.assertIn('EXCEPTION_ACCESS_VIOLATION', text)
        self.assertIn('FObjectReplicator::PostSendBunch()', text)
        self.assertNotIn('C:\\BA', text)
        self.assertTrue(text.endswith('\nUse `/server start` to bring it back.'))
        self.assertIsNone(await self.poll(3))

    async def test_down_without_crash_report(self):
        self.up()
        await self.poll()
        self.down()
        text, crash = await self.poll(2)
        self.assertIsNone(crash)
        self.assertIn('went down. No crash report was written', text)

    async def test_server_stop_is_not_announced_but_return_is(self):
        self.up()
        await self.poll()
        self.control.stopped_at = self.now
        self.down()
        self.assertIsNone(await self.poll(3))
        self.assertEqual(self.monitor.state, 'down')
        self.control.running, self.control.info, self.control.ready = True, None, False
        self.assertIsNone(await self.poll(3))  # Still loading.
        self.up(players=0)
        self.assertEqual(await self.poll(2), ('**CompaWorld** is back online and ready for players (0/2 players).', None))

    async def test_hung_server(self):
        self.up()
        await self.poll()
        self.control.info = None  # Still running and was ready, but the query port went quiet.
        text, crash = await self.poll(2)
        self.assertIn('stopped answering on query port 27015', text)
        self.up()
        self.assertEqual((await self.poll(2))[0], '**CompaWorld** is answering again (1/2 players).')

    async def test_without_query_answers_the_log_decides(self):
        self.control.running = True
        await self.poll()
        self.assertEqual(self.monitor.state, 'up')
        self.down()
        self.assertIn('went down', (await self.poll(2))[0])
        self.control.running = True
        self.assertEqual((await self.poll(2))[0], '**CompaWorld** is back online and ready for players (1/2 players).')

    async def test_status_errors_are_skipped(self):
        self.up()
        await self.poll()
        self.down()
        self.control.status = AsyncMock(side_effect=ServerError('Access denied'))
        self.assertIsNone(await self.poll(3))
        self.assertEqual(self.monitor.state, 'up')


class CrashFileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.crash = Path(self.folder.name) / 'UE4CC-Windows-AB_0000'
        self.crash.mkdir()
        (self.crash / 'CrashContext.runtime-xml').write_text(CONTEXT)
        (self.crash / 'Icarus.log').write_text('LogNet: something\n' * 5000)

    def tearDown(self):
        self.folder.cleanup()

    def test_latest_crash(self):
        older = Path(self.folder.name) / 'UE4CC-Windows-CD_0000'
        older.mkdir()
        os.utime(older, (100, 100))
        self.assertEqual(latest_crash(self.folder.name, 0), self.crash)
        self.assertIsNone(latest_crash(self.folder.name, self.crash.stat().st_mtime + 1))
        self.assertIsNone(latest_crash(Path(self.folder.name) / 'missing', 0))

    def test_summary_and_archive(self):
        self.assertEqual(crash_summary(self.crash),
                         'Unhandled Exception: EXCEPTION_ACCESS_VIOLATION reading address 0xffffffffffffffff\n'
                         'IcarusServer_Win64_Shipping!FObjectReplicator::PostSendBunch()')
        name, data = crash_archive(self.crash)
        self.assertEqual(name, 'UE4CC-Windows-AB_0000.zip')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(sorted(archive.namelist()), ['CrashContext.runtime-xml', 'Icarus.log'])
        self.assertIsNone(crash_archive(self.crash, limit=10))

    async def test_post_attaches_or_points_to_the_report(self):
        channel = SimpleNamespace(send=AsyncMock())
        await post(channel, 'Crashed.', self.crash)
        text = channel.send.call_args.args[0]
        self.assertEqual(text, 'Crashed.\nThe crash report is attached.')
        self.assertEqual(channel.send.call_args.kwargs['files'][0].filename, 'UE4CC-Windows-AB_0000.zip')
        await post(channel, 'Up.', None)
        self.assertEqual(channel.send.call_args.kwargs['files'], [])


if __name__ == '__main__':
    unittest.main()
