#!/usr/bin/env python
"""Refuse one media for some keys: register them in media_rejects.tsv and
delete what was already built from that media, so the next assemble falls
to the next style of the recipe (or borrows the reference image).

    python reject_media.py mixrbv2 Arcade reject_arcade.txt "wrong mix"

One key per line; blank lines and '#' are ignored. The key stays in the
pack with every other style untouched -- to drop a key from the pack
altogether, use exclude_keys.py instead. Reassemble the system afterwards.
"""
import csv
import os
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

args = sys.argv[1:]
if len(args) < 3:
    sys.exit(__doc__.strip())
media, system, listing = args[0], args[1], args[2]
reason = args[3] if len(args) > 3 else ''

with open(listing, encoding='utf-8') as f:
    keys = [l.strip() for l in f if l.strip() and not l.startswith('#')]

meta = os.path.join('work', 'meta', system)
if not os.path.isdir(meta):
    sys.exit('no such directory: %s' % meta)
pool = os.path.join('work', 'pool', media, system)
in_pool = ({os.path.splitext(n)[0] for n in os.listdir(pool)}
           if os.path.isdir(pool) else set())

table = 'media_rejects.tsv'
rows = {}
if os.path.isfile(table):
    with open(table, encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')
            if line and not line.startswith('#'):
                p = line.split('\t')
                if len(p) >= 3:
                    rows[(p[0], p[1], p[2])] = p[3] if len(p) > 3 else ''

# Any style whose recipe reaches this media may have built from it, so every
# out/media-* is inspected -- but only images its own manifest attributes to
# this media are deleted. A pack that took the image from another media (or
# lends it to another style under --like) must not lose it.
built_from = {}
for name in (os.listdir('out') if os.path.isdir('out') else []):
    if not name.startswith('media-'):
        continue
    style = name[len('media-'):]
    csv_path = os.path.join('out', 'manifest-%s.csv' % style)
    if not os.path.isfile(csv_path):
        continue
    art = os.path.join('out', name, 'docs', system, 'Artwork')
    with open(csv_path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            if row['system'] == system and row['style'] == media:
                built_from.setdefault(row['key'], []).append((name, art))

for key in keys:
    state = []
    if not os.path.isfile(os.path.join(meta, key + '.json')):
        state.append('NO METADATA')
    elif key not in in_pool:
        state.append('no %s source in the pool' % media)
    if (media, system, key) in rows:
        state.append('already listed')
    rows[(media, system, key)] = reason or rows.get((media, system, key), '')
    for name, art in built_from.get(key, []):
        p = os.path.join(art, key + '.jpg')
        if os.path.isfile(p):
            os.remove(p)
            state.append('deleted in ' + name)
    print('%-46s %s' % (', '.join(state) or 'listed', key))

with open(table, 'w', encoding='utf-8', newline='') as f:
    f.write('#media\tsystem\tkey\treason\n')
    for (m, s, k), r in sorted(rows.items()):
        f.write('%s\t%s\t%s\t%s\n' % (m, s, k, r))

print('\n%d %s media refused for %s (%d rows in %s); reassemble it.'
      % (len(keys), media, system, len(rows), table))
