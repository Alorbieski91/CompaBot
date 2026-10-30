"""Game settings that /server settings can change: what each one is, and checking a new value.

A game with settings (Palworld so far) lists them as Setting entries in its own folder, and
gives its ServerControl a `settings` object that reads and writes its settings file. This
module only knows about the four kinds of value: True/False, one of a few options, a whole
number and a decimal.
"""
import math
from dataclasses import dataclass

from .profile import ServerError

# What people type for True or False.
TRUE = {'true', 'yes', 'on', '1', 'y', 't'}
FALSE = {'false', 'no', 'off', '0', 'n', 'f'}
# Round numbers offered for a decimal or whole-number setting, inside its range.
COMMON = (0, 0.5, 1, 1.5, 2, 3, 5, 10)


@dataclass(frozen=True)
class Setting:
    key: str  # the name in the settings file
    label: str  # what it does, in plain words
    kind: str  # 'bool', 'choice', 'int' or 'float'
    default: object  # used when the game's own default file can't be read
    low: float = None  # range for 'int' and 'float'
    high: float = None
    options: tuple = ()  # ((value, meaning), ...) for 'choice'
    unit: str = ''  # shown after numbers, e.g. 'hours'

    def allowed(self):
        """The allowed values, in words."""
        if self.kind == 'bool':
            return 'True or False'
        if self.kind == 'choice':
            return ', '.join(value for value, _ in self.options)
        unit = f' {self.unit}' if self.unit else ''
        return f'{show(self, self.low)} to {show(self, self.high)}{unit}'


def parse(setting, text):
    """The value someone typed, as a bool, str, int or float. Raises ServerError if it isn't allowed."""
    text = str(text).strip()
    if setting.kind == 'bool':
        if text.lower() in TRUE:
            return True
        if text.lower() in FALSE:
            return False
        raise ServerError(f'`{setting.key}` is True or False, not `{text}`.')
    if setting.kind == 'choice':
        for value, _ in setting.options:
            if text.lower() == value.lower():
                return value
        raise ServerError(f'`{setting.key}` must be one of {setting.allowed()}, not `{text}`.')
    try:
        number = float(text)
    except ValueError:
        raise ServerError(f'`{setting.key}` is a number from {setting.allowed()}, not `{text}`.') from None
    if not math.isfinite(number):
        raise ServerError(f'`{setting.key}` is a number from {setting.allowed()}, not `{text}`.')
    if setting.kind == 'int':
        if number != int(number):
            raise ServerError(f'`{setting.key}` is a whole number from {setting.allowed()}, not `{text}`.')
        number = int(number)
    if not setting.low <= number <= setting.high:
        raise ServerError(f'`{setting.key}` must be from {setting.allowed()}; `{text}` is outside that.')
    return number


def decode(setting, raw):
    """A value as the settings file (or the game's API) holds it, or None if it can't be read."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw if setting.kind == 'bool' else None
    text = str(raw).strip().strip('"')
    try:
        if setting.kind == 'bool':
            return {'true': True, 'false': False}.get(text.lower())
        if setting.kind == 'choice':
            return next((value for value, _ in setting.options if value.lower() == text.lower()), text)
        number = float(text)
        return int(number) if setting.kind == 'int' else number
    except ValueError:
        return None


def show(setting, value):
    """A value the way people read it: True, Item, 3000, 1.5."""
    if value is None:
        return '?'
    if isinstance(value, bool):
        return 'True' if value else 'False'
    if isinstance(value, float):
        return f'{value:g}'
    return str(value)


def same(setting, a, b):
    """Whether two decoded values are the same setting value (decimals compared loosely)."""
    if a is None or b is None:
        return False
    if setting.kind in ('int', 'float'):
        return math.isclose(float(a), float(b), rel_tol=1e-4, abs_tol=1e-6)
    return a == b


def suggestions(setting, current=None, default=None, typed=''):
    """(name, value) pairs for the value: autocomplete. Typed text comes first, marked if it isn't allowed."""
    found = []
    typed = typed.strip()
    if typed:
        try:
            value = parse(setting, typed)
            found.append((f'{show(setting, value)}', show(setting, value)))
        except ServerError:
            if setting.kind in ('int', 'float'):
                found.append((f'{typed} (not allowed: {setting.allowed()})', typed))
    if setting.kind == 'bool':
        values = [(True, ''), (False, '')]
    elif setting.kind == 'choice':
        values = list(setting.options)
    else:
        numbers = {setting.low, setting.high, *(n for n in COMMON if setting.low <= n <= setting.high)}
        if default is not None:
            numbers.add(default)
        if setting.kind == 'int':
            numbers = {int(n) for n in numbers if n == int(n)}
        values = [(n, '') for n in sorted(numbers)]
    for value, meaning in values:
        notes = [meaning] if meaning else []
        if current is not None and same(setting, value, current):
            notes.append('now')
        if default is not None and same(setting, value, default):
            notes.append('default')
        if setting.kind in ('int', 'float') and value in (setting.low, setting.high):
            notes.append('lowest' if value == setting.low else 'highest')
        name = show(setting, value) + (f' ({", ".join(notes)})' if notes else '')
        if all(v != show(setting, value) for _, v in found):
            found.append((name, show(setting, value)))
    return [(name[:100], value[:100]) for name, value in found]
