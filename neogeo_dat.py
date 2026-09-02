#!/usr/bin/env python
"""
Turn the Neo Geo core's romsets.xml into a Parent/Clone DAT the builder reads.

    python neogeo_dat.py romsets.xml dats/neogeo.dat

Rom packs name a game three ways -- 'mslug2', 'Metal Slug 2 (mslug2).neo', a
'Fatal Fury Special' folder -- so every spelling is emitted as an alias of one
game and the builder indexes them all to it.

  * The key is the setname: altnames carry ':' and '/', which no filesystem
    accepts in a filename.
  * The query uses the commercial name, not the key: 'mslug2' returns nothing
    from a console catalogue.
  * The query name is cleaned: inverted articles and bracketed explanations
    make real games 404.
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
    """Name to ask ScreenScraper for. Cut at the slash, never at the colon:
    a slash separates two titles, a colon does not (measured)."""
    name = EXPLANATORY.sub('', altname)
    name = re.split(r'\s+/\s+', name, maxsplit=1)[0]
    return ' '.join(uninvert(name.strip()).split()) or altname


SPLIT = re.compile(r'\s*:\s|\s+/\s+')

# Bare halves that name a DIFFERENT series: the subtitle of 'Fatal Fury: King
# of Fighters' is the KOF series. An exception, not a rule -- measured, 49 of
# 50 substring collisions are a title inside its own sequel. Lowercase.
BARE_FORM_DENY = {'king of fighters'}

# Queried by setname ('columnsn.zip') instead of commercial name: homebrews
# named after famous games on OTHER platforms, which the global name search
# returns instead ('Columns' -> Sega Classics). All came out of identify as
# out-of-family.
QUERY_BY_SETNAME = {
    'cabalng',       # Cabal (Neo Geo homebrew)
    'columnsn',      # Columns (Neo Geo homebrew)
    'kof98Ultimate', # KOF '98 Ultimate Match hack
    'nblktigr',      # Neo Black Tiger (homebrew)
    'neotris',       # NeoTris (homebrew)
    'sbp',           # Super Bubble Pop (homebrew)
    'tetrismn',      # Tetris (Neo Geo homebrew)
}


def name_forms(altname):
    """Every spelling a rom pack might use: either half of a colon split, the
    dash form, and run-together names spaced out. Measured on a real library;
    an alias only ever costs an index row."""
    forms = {altname}
    parts = [p.strip() for p in SPLIT.split(altname) if p.strip()]
    forms.update(parts)
    if len(parts) > 1:
        forms.add(' - '.join(parts))
    # 'OverTop' -> 'Over Top'
    for form in list(forms):
        spaced = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', form)
        if spaced != form:
            forms.add(spaced)
    return [f for f in forms if f and f.lower() not in BARE_FORM_DENY]


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
    romnom = (parent + '.zip' if parent in QUERY_BY_SETNAME
              else query_name(base[1]))
    games.append((parent, romnom, '', True))
    for sets, altname, altnamej in group:
        forms = [query_name(altname)]
        for source_name in (altname, altnamej):
            if source_name:
                forms += name_forms(source_name)
        # Every form also in the '<title> (<setname>)' shape the .neo files use
        aliases = list(sets[1:]) + forms
        aliases += ['%s (%s)' % (f, s) for f in forms for s in sets]
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
        # <release> keeps the setname parent from losing the election to an alias
        if has_release:
            out.write('\t\t<release name="%s"/>\n' % escape(name, QUOTE))
        # <rom> is required by load_entries(); no crc/size, like arcade
        out.write('\t\t<rom name="%s"/>\n' % escape(romnom, QUOTE))
        out.write('\t</game>\n')
    out.write('</datafile>\n')

parents = sum(1 for _, _, p, _ in games if not p)
print('%d romsets -> %d games, %d aliases  (%s)'
      % (len(romsets), parents, len(games) - parents, target))
