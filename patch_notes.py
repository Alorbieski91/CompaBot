"""Turn Icarus patch notes from Steam into a short "what changed for us" message.

RocketWerkz posts each "Week N Update" and "Hotfix" on the game's Steam news feed. Compabot
picks out the list items that change how we play: creatures (Kiwis laying eggs is the kind of
thing we missed once), taming, farming, new items and recipes, balance, missions, and anything
that touches saves. Bug-fix and polish sections are skipped.
"""
import json
import re
import urllib.request

GAME_APP_ID = '1149460'  # the Icarus game; the dedicated server (2089300) has no news feed
NEWS_URL = ('https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/?appid=' + GAME_APP_ID +
            '&count=15&maxlength=0&feeds=steam_community_announcements&format=json')
UPDATE_TITLE = re.compile(r'\b(update|hotfix|patch)\b', re.I)
BBCODE = re.compile(r'\[/?[a-z0-9*]+(?:=[^\]]*)?\]', re.I)
# Sections of the notes that never change how we play.
SKIP_SECTION = re.compile(r'fix|bug|known issue|performance|optimi[sz]|\bui\b|interface|audio|sound|'
                          r'visual|art\b|localization|translation|store|dlc|workshop item', re.I)
SKIP_LINE = re.compile(r'^(fixed|fixes|fix|resolved|corrected)\b', re.I)
CHANGE = re.compile(r'^(added|new|changed|reworked|increased|reduced|decreased|lowered|raised|removed|'
                    r'replaced|improved|adjusted|rebalanced|updated|buffed|nerfed|can now|now)\b', re.I)
# Words that mark a change to what we do in the game; these lines are listed first.
GAMEPLAY = re.compile(r'egg|tam(e|ed|ing)|mount|pet\b|creature|animal|kiwi|wolf|bear|moa|buffalo|boar|'
                      r'deer|horse|breed|farm|crop|seed|plant|harvest|husbandry|feed|food|cook|recipe|'
                      r'craft|blueprint|tech ?tree|talent|workshop|item|tool|weapon|armou?r|deployable|'
                      r'bench|ore|resource|yield|stamina|health|oxygen|water|hunger|temperature|weather|'
                      r'storm|mission|prospect|outpost|biome|map|cave|loot|drop|spawn|damage|durability|save',
                      re.I)
LIMIT = 12


def fetch_json(url, timeout=30):
    request = urllib.request.Request(url, headers={'User-Agent': 'Compabot'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def update_posts(news, since):
    """The update and hotfix posts in a GetNewsForApp response published after `since`, oldest first."""
    items = news.get('appnews', {}).get('newsitems', [])
    posts = [i for i in items if i.get('date', 0) > since and UPDATE_TITLE.search(i.get('title', ''))]
    return sorted(posts, key=lambda i: i['date'])


def plain_text(contents):
    text = BBCODE.sub('\n', contents.replace('[*]', '\n- '))
    return '\n'.join(line.strip() for line in text.splitlines() if line.strip())


def gameplay_changes(posts, limit=LIMIT):
    """The list items in the notes that look like gameplay changes, most relevant first."""
    found = []
    for post in posts:
        section = ''
        for line in plain_text(post.get('contents', '')).splitlines():
            if not line.startswith(('-', '•', '*')):
                section = line  # a heading such as "New Content" or "Bug Fixes"
                continue
            line = line.lstrip('-•* ').strip()
            if SKIP_SECTION.search(section) or SKIP_LINE.match(line) or line in (f for _, f in found):
                continue
            gameplay = bool(GAMEPLAY.search(line))
            if gameplay or CHANGE.match(line):
                found.append((not gameplay, line))
    found.sort(key=lambda item: item[0])  # stable: gameplay lines first, each group in note order
    lines = [f'- {line if len(line) <= 180 else line[:177] + "..."}' for _, line in found[:limit]]
    if len(found) > limit:
        lines.append(f'- ...and {len(found) - limit} more in the notes.')
    return lines


def describe(posts, build):
    """The Discord message about the gameplay changes in `posts`, now running as `build`."""
    titles = ', '.join(f"[{p['title']}](<{p['url']}>)" for p in posts)
    lines = gameplay_changes(posts)
    summary = '\n'.join(lines) if lines else 'No gameplay changes stood out; see the notes for details.'
    return f'**What changed in Icarus** (server now on build {build}): {titles}\n{summary}'
