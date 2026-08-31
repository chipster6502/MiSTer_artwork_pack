#!/usr/bin/env python
"""
Turn the Neo Geo core's romsets.xml into a Parent/Clone DAT the builder reads.

    python neogeo_dat.py romsets.xml dats/neogeo.dat

A Neo Geo game's pack key is the name of its romset file or folder, and every
rom pack names them differently. One real library holds three forms of the
same game:

    mslug2                       Darksoft folder and .zip: setname
    Metal Slug 2 (mslug2).neo    @MiSTer Pack Add-on
    Fatal Fury Special           @MiSTer Pack Add-on romset folder

romsets.xml carries the setname, the commercial name and the Japanese one, so
emitting them as aliases of one game is enough: the builder groups by cloneof,
elects a representative, and writes every alias as an index.tsv row.

Three decisions that are not obvious:

  * THE KEY IS THE SETNAME. Altnames carry ':' and '/' — 'Metal Slug 2: Super
    Vehicle-001/II' — and the key becomes a filename that neither Windows nor
    exFAT accepts. Arcade already keys on setnames for the same reason.

  * THE QUERY DOES NOT USE THE KEY. query_game() sends 'romnom', taken from
    <rom name>, so ScreenScraper is asked for the commercial name. Asking it
    for 'mslug2' would return nothing from a console catalogue.

  * THE QUERY NAME IS CLEANED. romsets.xml inverts articles and appends
    explanations in brackets; both make real commercial games 404.
"""
import re
import sys
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

if len(sys.argv) < 3:
    sys.exit(__doc__.strip())
source, target = sys.argv[1], sys.argv[2]

ARTICLES = ('The', 'A', 'An', 'La', 'Le', 'Les', 'El', 'Los', 'Die', 'Der')

# Brackets that explain rather than distinguish. Stripped from the query only:
# the alias keeps them, because a folder may be named that way.
EXPLANATORY = re.compile(
    r'\s*\((?:[^()]*\b(?:release|version|bootleg|hack|prototype|set \d|'
    r'earlier|alt board|localized|NGH-|NGM-)[^()]*)\)', re.I)

QUOTE = {'"': '&quot;'}


def uninvert(name):
    """'Irritating Maze, The' -> 'The Irritating Maze'."""
    for art in ARTICLES:
        if name.endswith(', ' + art):
            return art + ' ' + name[:-len(art) - 2]
        # Mid-string too: "King of Fighters '98, The: The Slugfest"
        mark = ', %s: ' % art
        if mark in name:
            head, tail = name.split(mark, 1)
            return '%s %s: %s' % (art, head, tail)
    return name


def query_name(altname):
    """Name to ask ScreenScraper for.

    Cut at the slash, never at the colon. Measured: 'Street Hoop / Street
    Slam' 404s while 'Street Hoop' returns a full box — a slash separates two
    titles. Colons resolve fine, and cutting them would turn 'Fatal Fury:
    King of Fighters' into the whole series.
    """
    name = EXPLANATORY.sub('', altname)
    name = re.split(r'\s+/\s+', name, maxsplit=1)[0]
    return ' '.join(uninvert(name.strip()).split()) or altname


def short_name(altname):
    """The form the .neo files use: up to the colon or the slash."""
    return re.split(r'\s*:\s|\s+/\s+', altname, maxsplit=1)[0].strip() or altname


def norm(name):
    name = re.sub(r'\([^)]*\)|\[[^\]]*\]', ' ', name)
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', name.lower()).split())


romsets = []
for node in ET.parse(source).getroot().iter('romset'):
    raw = (node.get('name') or '').strip()
    if not raw:
        continue
    # One attribute may hold several setnames: name="tophuntrh,tphuntrh"
    sets = [s.strip() for s in raw.split(',') if s.strip()]
    altname = (node.get('altname') or '').strip() or sets[0]
    altnamej = (node.get('altnamej') or '').strip()
    romsets.append((sets, altname, altnamej))

if not romsets:
    sys.exit('no <romset> found in %s' % source)

# Group by normalised altname, so '(set 2)' revisions join their base game.
groups = {}
for entry in romsets:
    groups.setdefault(norm(entry[1]), []).append(entry)

taken = set()
games = []          # (name, romnom, parent_or_empty, has_release)
for gid in sorted(groups):
    group = groups[gid]
    base = min(group, key=lambda e: (len(e[1]), e[1]))
    parent = base[0][0]
    if parent.lower() in taken:
        continue
    taken.add(parent.lower())
    games.append((parent, query_name(base[1]), '', True))
    for sets, altname, altnamej in group:
        aliases = list(sets[1:])
        aliases += [n for n in (altname, query_name(altname),
                                short_name(altname), altnamej) if n]
        aliases += ['%s (%s)' % (short_name(altname), s) for s in sets]
        for name in aliases:
            if name.lower() in taken:
                continue
            taken.add(name.lower())
            games.append((name, name, parent, False))

with open(target, 'w', encoding='utf-8') as out:
    out.write('<?xml version="1.0"?>\n<datafile>\n')
    out.write('\t<header>\n\t\t<name>Neo Geo (from romsets.xml)</name>\n'
              '\t\t<description>Generated by neogeo_dat.py</description>\n'
              '\t</header>\n')
    for name, romnom, parent, has_release in games:
        attr = ' name="%s"' % escape(name, QUOTE)
        if parent:
            attr += ' cloneof="%s"' % escape(parent, QUOTE)
        out.write('\t<game%s>\n' % attr)
        # <release> outranks name length in elect_key(), which is what keeps
        # the setname parent from losing to one of its own aliases.
        if has_release:
            out.write('\t\t<release name="%s"/>\n' % escape(name, QUOTE))
        # <rom> is required: load_entries() drops a <game> without one. No crc
        # or size, same as the arcade rows.
        out.write('\t\t<rom name="%s"/>\n' % escape(romnom, QUOTE))
        out.write('\t</game>\n')
    out.write('</datafile>\n')

parents = sum(1 for _, _, p, _ in games if not p)
print('%d romsets -> %d games, %d aliases  (%s)'
      % (len(romsets), parents, len(games) - parents, target))
