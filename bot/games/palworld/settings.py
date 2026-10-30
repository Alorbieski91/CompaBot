"""Palworld's gameplay settings: which ones /server settings offers, and reading and writing them.

The dedicated server reads them from server\\Pal\\Saved\\Config\\WindowsServer\\PalWorldSettings.ini,
all on one line: OptionSettings=(ExpRate=1.000000,DeathPenalty=All,...). It only reads them when it
starts, and it can write its own values back over the file when it shuts down, so the file is
only edited while the server is off. A change made while it runs waits in settings\\pending.json
and is written in just before the next start (see ServerControl.run_action), or by the
settings watcher once the server has stopped.

Left out on purpose: passwords, ports, the REST API and RCON (a wrong value locks out the bot or
the players), what config.psd1 rewrites on every start (name, description, max players),
settings a dedicated server ignores, engine tuning, and free-text lists.
"""
import json
import os
import re
import shutil
import time
from pathlib import Path

from ...servers.profile import ServerError
from ...servers.settings import Setting, decode, same

F, I, B, C = 'float', 'int', 'bool', 'choice'


def s(key, label, kind, default, low=None, high=None, options=(), unit=''):
    return Setting(key, label, kind, default, low, high, options, unit)


SETTINGS = (
    s('DayTimeSpeedRate', 'How fast daytime passes', F, 1.0, 0.1, 5),
    s('NightTimeSpeedRate', 'How fast night passes', F, 1.0, 0.1, 5),
    s('ExpRate', 'XP gain for players and Pals', F, 1.0, 0.1, 20),
    s('PalCaptureRate', 'Catch chance', F, 1.0, 0.5, 2),
    s('PalSpawnNumRate', 'How many wild Pals spawn', F, 1.0, 0.5, 3),
    s('PalEggDefaultHatchingTime', 'Hours for a huge egg to hatch', F, 72.0, 0, 240, unit='hours'),
    s('EnablePredatorBossPal', 'Predator boss Pals spawn', B, True),
    s('RandomizerType', 'Shuffle which Pals spawn where', C, 'None',
      options=(('None', 'normal spawns'), ('Region', 'shuffled per region'), ('All', 'shuffled everywhere'))),
    s('bIsRandomizerPalLevelRandom', 'Wild Pal levels fully random', B, False),
    s('PalDamageRateAttack', 'Damage Pals deal', F, 1.0, 0.1, 5),
    s('PalDamageRateDefense', 'Damage Pals take', F, 1.0, 0.1, 5),
    s('PlayerDamageRateAttack', 'Damage players deal', F, 1.0, 0.1, 5),
    s('PlayerDamageRateDefense', 'Damage players take', F, 1.0, 0.1, 5),
    s('PlayerStomachDecreaceRate', 'How fast players get hungry', F, 1.0, 0.1, 5),
    s('PlayerStaminaDecreaceRate', 'How fast player stamina drains', F, 1.0, 0.1, 5),
    s('PlayerAutoHPRegeneRate', 'Player health regen', F, 1.0, 0.1, 5),
    s('PlayerAutoHpRegeneRateInSleep', 'Player health regen while sleeping', F, 1.0, 0.1, 5),
    s('PalStomachDecreaceRate', 'How fast Pals get hungry', F, 1.0, 0.1, 5),
    s('PalStaminaDecreaceRate', 'How fast Pal stamina drains', F, 1.0, 0.1, 5),
    s('PalAutoHPRegeneRate', 'Pal health regen', F, 1.0, 0.1, 5),
    s('PalAutoHpRegeneRateInSleep', 'Pal health regen in the Palbox', F, 1.0, 0.1, 5),
    s('bEnableInvaderEnemy', 'Raids on bases', B, True),
    s('bEnableAimAssistPad', 'Controller aim assist', B, True),
    s('bEnableAimAssistKeyboard', 'Keyboard aim assist', B, False),
    s('CollectionDropRate', 'Wood, stone and ore gathered', F, 1.0, 0.5, 3),
    s('CollectionObjectHpRate', 'Health of trees and rocks', F, 1.0, 0.5, 3),
    s('CollectionObjectRespawnSpeedRate', 'How fast trees and rocks come back', F, 1.0, 0.5, 3),
    s('EnemyDropItemRate', 'Loot from defeated enemies', F, 1.0, 0.5, 3),
    s('ItemWeightRate', 'How heavy items are', F, 1.0, 0, 5),
    s('EquipmentDurabilityDamageRate', 'Gear wear (0 = never breaks)', F, 1.0, 0, 5),
    s('ItemCorruptionMultiplier', 'How fast food spoils', F, 1.0, 0, 10),
    s('DropItemMaxNum', 'Most items lying on the ground', I, 3000, 0, 5000),
    s('PhysicsActiveDropItemMaxNum', 'Most dropped items using physics (-1 = no limit)', I, -1, -1, 5000),
    s('DropItemAliveMaxHours', 'Hours dropped items stay', F, 1.0, 0, 24, unit='hours'),
    s('SupplyDropSpan', 'Minutes between supply drops and meteorites', I, 180, 1, 1440, unit='minutes'),
    s('MonsterFarmActionSpeedRate', 'Ranch production speed', F, 1.0, 0.1, 5),
    s('FishingDifficultyRate', 'Fishing difficulty', F, 1.0, 0.1, 1),
    s('DeathPenalty', 'What you drop when you die', C, 'All',
      options=(('None', 'keep everything'), ('Item', 'drop items, keep gear'),
               ('ItemAndEquipment', 'drop items and gear'), ('All', 'drop items, gear and team Pals'))),
    s('bHardcore', 'Hardcore: no respawning', B, False),
    s('bPalLost', 'Pals that die are gone for good', B, False),
    s('bCharacterRecreateInHardcore', 'New character allowed after a hardcore death', B, False),
    s('BlockRespawnTime', 'Seconds before you can respawn', F, 5.0, 0, 600, unit='seconds'),
    s('RespawnPenaltyDurationThreshold', 'Seconds alive before the respawn penalty starts (0 = off)', F, 0.0, 0, 3600,
      unit='seconds'),
    s('RespawnPenaltyTimeScale', 'Respawn penalty multiplier', F, 2.0, 1, 10),
    s('BaseCampMaxNum', 'Bases on the whole server', I, 128, 1, 128),
    s('BaseCampMaxNumInGuild', 'Bases per guild', I, 4, 1, 10),
    s('BaseCampWorkerMaxNum', 'Pals working per base', I, 15, 1, 50),
    s('MaxBuildingLimitNum', 'Structures per player (0 = no limit)', I, 0, 0, 100000),
    s('bBuildAreaLimit', 'No building near fast-travel points', B, False),
    s('BuildObjectHpRate', 'Structure health', F, 1.0, 0.5, 5),
    s('BuildObjectDamageRate', 'Damage to structures', F, 1.0, 0.5, 3),
    s('BuildObjectDeteriorationDamageRate', 'Structure decay outside bases (0 = none)', F, 1.0, 0, 10),
    s('WorkSpeedRate', 'Pal work speed', F, 1.0, 0.1, 5),
    s('bAllowEnemyCampSpawnNearBaseCamp', 'Enemy camps can spawn near bases', B, True),
    s('bInvisibleOtherGuildBaseCampAreaFX', "Hide other guilds' base borders", B, False),
    s('bEnableBuildingPlayerUIdDisplay', 'Show who built each structure', B, False),
    s('GuildPlayerMaxNum', 'Players per guild', I, 20, 1, 100),
    s('bAutoResetGuildNoOnlinePlayers', 'Reset guilds nobody has played in', B, False),
    s('AutoResetGuildTimeNoOnlinePlayers', 'Hours before that reset', F, 72.0, 1, 8760, unit='hours'),
    s('GuildRejoinCooldownMinutes', 'Minutes before rejoining a guild', I, 0, 0, 10080, unit='minutes'),
    s('bIsPvP', 'PvP', B, False),
    s('bAdditionalDropItemWhenPlayerKillingInPvPMode', 'Extra drop on a PvP kill', B, False),
    s('AdditionalDropItemNumWhenPlayerKillingInPvPMode', 'How many of that extra drop', I, 1, 0, 100),
    s('bDisplayPvPItemNumOnWorldMap_BaseCamp', 'Show PvP item counts per base on the map', B, False),
    s('bDisplayPvPItemNumOnWorldMap_Player', 'Show players and their PvP items on the map', B, False),
    s('bEnableFastTravel', 'Fast travel', B, True),
    s('bEnableFastTravelOnlyBaseCamp', 'Fast travel only between bases', B, False),
    s('bIsStartLocationSelectByMap', 'New players pick their start on the map', B, False),
    s('bExistPlayerAfterLogout', 'Your body stays in the world after you log out', B, False),
    s('bAllowEnhanceStat_Health', 'Stat points into Health', B, True),
    s('bAllowEnhanceStat_Attack', 'Stat points into Attack', B, True),
    s('bAllowEnhanceStat_Stamina', 'Stat points into Stamina', B, True),
    s('bAllowEnhanceStat_Weight', 'Stat points into Carry Weight', B, True),
    s('bAllowEnhanceStat_WorkSpeed', 'Stat points into Work Speed', B, True),
    s('bAllowGlobalPalboxExport', 'Save Pals to the Global Palbox', B, True),
    s('bAllowGlobalPalboxImport', 'Bring Pals in from the Global Palbox', B, False),
    s('bAllowClientMod', 'Players with client mods can join', B, True),
    s('bShowPlayerList', 'Player list in the Esc menu', B, False),
    s('bIsShowJoinLeftMessage', 'Join and leave messages in chat', B, True),
    s('ChatPostLimitPerMinute', 'Chat messages allowed per minute', I, 30, 1, 120),
    s('bEnableVoiceChat', 'In-game voice chat', B, False),
    s('VoiceChatMaxVolumeDistance', 'Full-volume voice distance', F, 3000.0, 100, 50000, unit='cm'),
    s('VoiceChatZeroVolumeDistance', 'Distance where voice fades out', F, 15000.0, 100, 50000, unit='cm'),
    s('AutoSaveSpan', 'Seconds between autosaves', F, 30.0, 30, 3600, unit='seconds'),
    s('bIsUseBackupSaveData', "Palworld's own world backups", B, True),
    s('ServerReplicatePawnCullDistance', 'How far away Pals stay in sync', F, 15000.0, 5000, 15000, unit='cm'),
)
CATALOGUE = {setting.key: setting for setting in SETTINGS}

OPTION_LINE = re.compile(r'^OptionSettings=\((.*)\)[ \t\r]*$', re.M)
# The settings file copies /server settings undo can go back to.
KEEP_BACKUPS = 30


def split_options(inner):
    """The Key=Value pairs inside OptionSettings=(...), in order, values exactly as written.

    Values can be "quoted text" with commas in it, or (bracketed,lists), so this does not just split on commas.
    """
    pairs, start, depth, quoted = [], 0, 0, False
    for at, char in enumerate(inner + ','):
        if char == '"':
            quoted = not quoted
        elif quoted:
            continue
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
        elif char == ',' and depth == 0:
            part = inner[start:at]
            start = at + 1
            if part.strip():
                key, _, value = part.partition('=')
                pairs.append((key.strip(), value))
    return pairs


def encode(setting, value):
    """A value the way Palworld writes it in the file: True, Item, 3000, 1.500000."""
    if setting.kind == 'bool':
        return 'True' if value else 'False'
    if setting.kind == 'float':
        return f'{float(value):.6f}'
    return str(value)


def read_options(path):
    """{key: raw value} from a settings file's OptionSettings line. Raises OSError or ServerError."""
    match = OPTION_LINE.search(Path(path).read_text(encoding='utf-8-sig'))
    if not match:
        raise ServerError(f'{path} has no OptionSettings line. Start the server once so it is set up.')
    return dict(split_options(match[1]))


def write_options(path, changes):
    """Set {key: raw value} in a settings file's OptionSettings line, keeping everything else as it was."""
    path = Path(path)
    data = path.read_bytes()
    bom = data.startswith(b'\xef\xbb\xbf')
    text = data.decode('utf-8-sig')
    match = OPTION_LINE.search(text)
    if not match:
        raise ServerError(f'{path} has no OptionSettings line. Start the server once so it is set up.')
    pairs = split_options(match[1])
    keys = [key for key, _ in pairs]
    for key, value in changes.items():
        if key in keys:
            pairs[keys.index(key)] = (key, value)
        else:
            pairs.append((key, value))
    line = ','.join(f'{key}={value}' for key, value in pairs)
    text = text[:match.start(1)] + line + text[match.end(1):]
    temp = path.with_name(path.name + '.tmp')
    temp.write_bytes((b'\xef\xbb\xbf' if bom else b'') + text.encode('utf-8'))
    os.replace(temp, path)


class PalworldSettings:
    """Palworld's settings file, the changes waiting for the next start, and the backups and log around them."""

    catalogue = CATALOGUE

    def __init__(self, control):
        root = control.install_root
        self.control = control
        self.file = root / 'server' / 'Pal' / 'Saved' / 'Config' / 'WindowsServer' / 'PalWorldSettings.ini'
        self.default_file = root / 'server' / 'DefaultPalWorldSettings.ini'
        self.saves = root / 'server' / 'Pal' / 'Saved' / 'SaveGames'
        self.folder = root / 'settings'  # pending.json, applied.json, history.jsonl
        self.backups = root / 'backups' / 'settings'

    # The settings file

    def read(self):
        """{key: decoded value} for every catalogue setting in the settings file. Raises ServerError."""
        try:
            raw = read_options(self.file)
        except OSError:
            raise ServerError(f"Couldn't read `{self.file}`. Start the Palworld server once so it is created.") from None
        return {key: decode(setting, raw.get(key)) for key, setting in self.catalogue.items()}

    def defaults(self):
        """{key: default value}, from the server's DefaultPalWorldSettings.ini where it has the key."""
        try:
            raw = read_options(self.default_file)
        except (OSError, ServerError):
            raw = {}
        found = {}
        for key, setting in self.catalogue.items():
            value = decode(setting, raw.get(key))
            found[key] = setting.default if value is None else value
        return found

    def world_option(self):
        """A WorldOption.sav that would override the settings file, or None."""
        return next(iter(sorted(self.saves.glob('*/*/WorldOption.sav'))), None)

    def write(self, values, who):
        """Back up the settings file, then write {key: value} into it. Only while the server is off."""
        current = self.read()
        self.backup()
        write_options(self.file, {key: encode(self.catalogue[key], value) for key, value in values.items()})
        changes = {key: {'value': value, 'old': current.get(key), 'by': who, 'at': int(time.time())}
                   for key, value in values.items()}
        self.record('written', changes)
        applied = self.applied()
        applied.update(changes)
        self.save('applied.json', applied)

    def backup(self):
        self.backups.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        n = 0
        # The number keeps two changes in the same second in order; names sort oldest first.
        while (target := self.backups / f'PalWorldSettings-{stamp}-{n:02d}.ini').exists():
            n += 1
        shutil.copyfile(self.file, target)
        for old in self.saved_copies()[:-KEEP_BACKUPS]:
            old.unlink(missing_ok=True)

    def saved_copies(self):
        """Backups of the settings file, oldest first."""
        return sorted(self.backups.glob('PalWorldSettings-*.ini'), key=lambda p: p.name)

    def undo(self, who):
        """Put back the newest backup of the settings file. Returns its name. Only while the server is off."""
        copies = self.saved_copies()
        if not copies:
            raise ServerError('There is no earlier copy of the settings file to go back to.')
        newest = copies[-1]
        shutil.copyfile(newest, self.file)
        newest.unlink()
        self.record('undone', {'file': newest.name, 'by': who})
        self.save('applied.json', {})
        return newest.name

    # Changes waiting for the next start

    def pending(self):
        """{'changes': {key: {value, old, by, at}}, 'restart': bool}."""
        state = self.load('pending.json') or {}
        return {'changes': state.get('changes', {}), 'restart': bool(state.get('restart'))}

    def add_pending(self, key, value, old, who):
        """Wait to write key=value at the next start. A value the file already has drops the wait instead."""
        state = self.pending()
        if same(self.catalogue[key], value, old):
            state['changes'].pop(key, None)
        else:
            state['changes'][key] = {'value': value, 'old': old, 'by': who, 'at': int(time.time())}
            self.record('pending', {key: state['changes'][key]})
        self.save_pending(state)
        return state

    def cancel(self, key=None):
        """Drop one waiting change, or all of them. Returns the keys dropped."""
        state = self.pending()
        dropped = [key] if key in state['changes'] else [] if key else list(state['changes'])
        for name in dropped:
            del state['changes'][name]
        self.save_pending(state)
        return dropped

    def want_restart(self, wanted=True):
        state = self.pending()
        state['restart'] = wanted
        self.save_pending(state)

    def save_pending(self, state):
        if not state['changes']:
            state['restart'] = False
        self.save('pending.json', state)

    def apply_pending(self):
        """Write the waiting changes into the settings file. Returns {key: value} written. Only while the server is off."""
        state = self.pending()
        if not state['changes']:
            return {}
        current = self.read()
        values = {key: change['value'] for key, change in state['changes'].items() if key in self.catalogue}
        self.backup()
        write_options(self.file, {key: encode(self.catalogue[key], value) for key, value in values.items()})
        applied = self.applied()
        for key, change in state['changes'].items():
            applied[key] = {**change, 'old': current.get(key, change.get('old'))}
        self.save('applied.json', applied)
        self.save('pending.json', {'changes': {}, 'restart': False})
        self.record('written', state['changes'])
        return values

    # Written to the file, not yet seen in the running server

    def applied(self):
        return self.load('applied.json') or {}

    def clear_applied(self):
        self.save('applied.json', {})

    def live(self):
        """{key: decoded value} the running server is using, from its REST API. Raises OSError or ValueError."""
        from .api import request
        raw = request(self.control, 'settings', timeout=5.0)
        return {key: decode(setting, raw.get(key)) for key, setting in self.catalogue.items() if key in raw}

    # Files

    def load(self, name):
        try:
            return json.loads((self.folder / name).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None

    def save(self, name, data):
        self.folder.mkdir(parents=True, exist_ok=True)
        temp = self.folder / (name + '.tmp')
        temp.write_text(json.dumps(data, indent=1), encoding='utf-8')
        os.replace(temp, self.folder / name)

    def record(self, what, details):
        """Add a line to settings\\history.jsonl: who changed what, and when."""
        self.folder.mkdir(parents=True, exist_ok=True)
        with open(self.folder / 'history.jsonl', 'a', encoding='utf-8') as history:
            history.write(json.dumps({'at': int(time.time()), 'what': what, 'details': details}) + '\n')
