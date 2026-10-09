"""Keep laanp's Icarus mods (such as Pete's Beacon Teleport) in step with the game week.

laanp publishes every mod in https://github.com/laanp/Icarus_Mods_Separated with a
modinfo.json index. Mod files are named <mod>_v<fix>_w<week>_P.pak, and each mod's readme
says which game build it was made for: "Compatible with Icarus Version: Rev. 3.0.30.158174
(Week: 252)". The installed game build is in server/Icarus/Config/version.json.
"""
import json
import logging
import re
import shutil
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger('compabot.mods')

MODINFO_URL = 'https://raw.githubusercontent.com/laanp/Icarus_Mods_Separated/main/modinfo.json'
PAK_NAME = re.compile(r'^(?P<name>.+)_v(?P<fix>\d+)_w(?P<week>\d+)_P\.pak$', re.I)
README_REV = re.compile(r'Rev\.\s*(?P<rev>\d+(?:\.\d+){3})\s*\(Week:\s*(?P<week>\d+)\)', re.I)
# UE4 .pak files end with a footer holding this magic number.
PAK_MAGIC = bytes.fromhex('e1126f5a')
OLD_COPIES = 5


class ModError(Exception):
    pass


def fetch(url, timeout=60):
    request = urllib.request.Request(url, headers={'User-Agent': 'Compabot'})
    return urllib.request.urlopen(request, timeout=timeout)


def version_key(rev):
    return tuple(int(part) for part in rev.split('.'))


def game_version(install_root):
    """The installed game build as 'major.minor.patch.changelist', or None if unknown."""
    try:
        data = json.loads((Path(install_root) / 'server' / 'Icarus' / 'Config' / 'version.json').read_text(encoding='utf-8-sig'))
        v = data['Version']
        return f"{v['Major']}.{v['Minor']}.{v['Patch']}.{v['Changelist']}"
    except (OSError, ValueError, KeyError, TypeError):
        return None


def is_pak(path):
    with open(path, 'rb') as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 1024))
        return size > 1024 and PAK_MAGIC in f.read()


class ModUpdater:
    def __init__(self, install_root, opener=fetch):
        self.root = Path(install_root)
        self.mods_dir = self.root / 'server' / 'Icarus' / 'Content' / 'Paks' / 'mods'
        self.backup_dir = self.root / 'backups' / 'mods'
        self.open = opener

    def read(self, url):
        with self.open(url) as response:
            return response.read().decode('utf-8-sig')

    def installed(self):
        found = []
        for path in sorted(self.mods_dir.glob('*.pak')):
            match = PAK_NAME.match(path.name)
            if match:
                found.append((match['name'], int(match['week']), int(match['fix']), path))
        return found

    def built_for(self, entry):
        """The game build and week a mod's readme says it supports, or (None, None)."""
        # Read github.com/<owner>/<repo>/raw/<path> links straight from raw.githubusercontent.com.
        url = re.sub(r'^https://github\.com/([^/]+/[^/]+)/(?:raw|blob)/', r'https://raw.githubusercontent.com/\1/',
                     entry.get('readmeURL', ''))
        if not url:
            return None, None
        try:
            match = README_REV.search(self.read(url))
        except Exception as error:
            log.warning('Could not read %s: %s', url, error)
            return None, None
        return (match['rev'], int(match['week'])) if match else (None, None)

    def download(self, url, target):
        part = target.with_name(target.name + '.download')
        try:
            with self.open(url, timeout=300) as response, open(part, 'wb') as out:
                shutil.copyfileobj(response, out)
            if not is_pak(part):
                raise ModError(f'{target.name} did not download as a valid .pak file.')
            part.replace(target)
        finally:
            part.unlink(missing_ok=True)

    def retire(self, path):
        """Move an old mod file into backups/mods, keeping the newest few copies per mod."""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(self.backup_dir / path.name))
        name = PAK_NAME.match(path.name)['name']
        copies = sorted((p for p in self.backup_dir.glob('*.pak')
                         if (m := PAK_NAME.match(p.name)) and m['name'] == name),
                        key=lambda p: p.stat().st_mtime, reverse=True)
        for old in copies[OLD_COPIES:]:
            old.unlink()

    def index(self):
        """laanp's mod list, by lower-case mod name."""
        try:
            return {m['name'].lower(): m for m in json.loads(self.read(MODINFO_URL))['mods']}
        except Exception as error:
            raise ModError(f'Could not read the mod list from GitHub: {error}') from None

    @staticmethod
    def latest_release(index, name):
        """A mod's list entry, its latest pak URL, and that pak's name match (None if there is no release)."""
        entry = index.get(name.lower())
        pak_url = (entry or {}).get('files', {}).get('pak', '')
        return entry, pak_url, PAK_NAME.match(Path(urlparse(pak_url).path).name)

    def available(self):
        """Installed mods with a newer release the installed game can run, as 'name wX vY to wA vB'.

        Only reads from GitHub; nothing is downloaded or moved.
        """
        installed = self.installed()
        if not installed:
            return []
        index = self.index()
        game = game_version(self.root)
        found = []
        for name, week, fix, _ in installed:
            entry, _, latest = self.latest_release(index, name)
            if not latest or (int(latest['week']), int(latest['fix'])) <= (week, fix):
                continue
            rev, _ = self.built_for(entry)
            if rev and game and version_key(rev) > version_key(game):
                continue  # made for a newer game; the game update will bring it in
            found.append(f"{name} w{week} v{fix} to w{latest['week']} v{latest['fix']}")
        return found

    def update(self):
        """Update installed laanp mods to their latest release. Returns lines to report."""
        installed = self.installed()
        if not installed:
            return ['No mods installed.']
        index = self.index()
        game = game_version(self.root)
        lines = []
        for name, week, fix, path in installed:
            entry, pak_url, latest = self.latest_release(index, name)
            if not latest:
                lines.append(f'{name}: not in laanp\'s mod list, left as is.')
                continue
            new_week, new_fix = int(latest['week']), int(latest['fix'])
            rev, rev_week = self.built_for(entry)
            if rev and game and version_key(rev) > version_key(game):
                lines.append(f'{name}: the latest release (w{new_week}) needs game {rev}, but the server has {game}. '
                             f'Kept w{week}; update the game first.')
                continue
            if (new_week, new_fix) > (week, fix):
                target = self.mods_dir / latest.group(0)
                self.download(pak_url, target)
                if target != path:
                    self.retire(path)
                lines.append(f'{name}: updated w{week} v{fix} to w{new_week} v{new_fix} (old file kept in backups\\mods).')
            else:
                lines.append(f'{name}: w{week} v{fix} is the latest release.')
            if rev and game and version_key(rev) < version_key(game):
                lines.append(f'Warning: {name} was made for game {rev} (week {rev_week}) but the server has {game}. '
                             'laanp has not released it for this game update yet; it may not load until they do.')
        return lines
