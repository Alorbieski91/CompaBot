"""SQLite storage for quotes and server favorites."""
import sqlite3
from pathlib import Path

class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS quotes (
                id INTEGER PRIMARY KEY, guild INTEGER NOT NULL,
                channel INTEGER NOT NULL, message INTEGER NOT NULL UNIQUE,
                author INTEGER NOT NULL, name TEXT NOT NULL, body TEXT NOT NULL,
                url TEXT NOT NULL, saved_by INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS favorites (
                guild INTEGER NOT NULL, category TEXT NOT NULL,
                item TEXT NOT NULL COLLATE NOCASE,
                PRIMARY KEY (guild, category, item));
        ''')

    def save_quote(self, guild, channel, message, author, name, body, url, saved_by):
        with self.db:
            return bool(self.db.execute(
                'INSERT OR IGNORE INTO quotes (guild,channel,message,author,name,body,url,saved_by) VALUES (?,?,?,?,?,?,?,?)',
                (guild, channel, message, author, name, body, url, saved_by)).rowcount)

    def quote(self, guild, channel):
        return self.db.execute('SELECT * FROM quotes WHERE guild=? AND channel=? ORDER BY RANDOM() LIMIT 1', (guild, channel)).fetchone()

    def remove_quote(self, guild, quote_id, user, moderator=False):
        with self.db:
            return bool(self.db.execute('DELETE FROM quotes WHERE guild=? AND id=? AND (author=? OR saved_by=? OR ?)', (guild, quote_id, user, user, moderator)).rowcount)

    def add(self, guild, category, item):
        with self.db:
            return bool(self.db.execute('INSERT OR IGNORE INTO favorites VALUES (?,?,?)', (guild, category, item)).rowcount)

    def remove(self, guild, category, item):
        with self.db:
            return bool(self.db.execute('DELETE FROM favorites WHERE guild=? AND category=? AND item=?', (guild, category, item)).rowcount)

    def items(self, guild, category):
        return [r[0] for r in self.db.execute('SELECT item FROM favorites WHERE guild=? AND category=? ORDER BY item', (guild, category))]

    def close(self):
        self.db.close()
