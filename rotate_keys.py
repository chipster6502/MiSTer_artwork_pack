#!/usr/bin/env python
"""Register rotations in rotations.tsv and delete the images already built
for those keys, so the next assemble re-encodes them turned.

    python rotate_keys.py SNES rotate_snes_right.txt 90
    python rotate_keys.py SNES rotate_snes_left.txt -90

Degrees are clockwise: 90 turns right, -90 left. One key per line; blank
lines and '#' are ignored (same file format as exclude_keys.py, for the
same reason). The rotation applies to the box-2D media unless --media says
otherwise. Keys with no metadata, or no source of that media in the pool,
are reported: the rotation would not reach them. Reassemble afterwards.
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

args = [a for a in sys.argv[1:] if not a.startswith('--')]
if len(args) < 3:
    sys.exit(__doc__.strip())
system, listing, degrees = args[0], args[1], args[2]
try:
    degrees = int(degrees)
except ValueError:
    sys.exit('degrees must be an integer: 90, -90, 180')
media = 'box-2D'
for i, a in enumerate(sys.argv):
    if a == '--media' and i + 1 < len(sys.argv):
        media = sys.argv[i + 1]

with open(listing, encoding='utf-8') as f:
    keys = [l.strip() for l in f if l.strip() and not l.startswith('#')]

meta = os.path.join('work', 'meta', system)
pool = os.path.join('work', 'pool', media, system)
if not os.path.isdir(meta):
    sys.exit('no such directory: %s' % meta)
in_pool = set()
if os.path.isdir(pool):
    in_pool = {os.path.splitext(n)[0] for n in os.listdir(pool)}

# one row per (media, system, key); a new one replaces the old
table = 'rotations.tsv'
rows = {}
if os.path.isfile(table):
    with open(table, encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')
            if not line or line.startswith('#'):
                continue
            p = line.split('\t')
            if len(p) >= 4:
                rows[(p[0], p[1], p[2])] = p[3]

outputs = [os.path.join('out', d, 'docs', system, 'Artwork')
           for d in os.listdir('out') if d.startswith('media-')]
warnings = 0
for key in keys:
    state = []
    if not os.path.isfile(os.path.join(meta, key + '.json')):
        state.append('NO METADATA')
        warnings += 1
    elif key not in in_pool:
        state.append('no %s source in the pool' % media)
        warnings += 1
    rows[(media, system, key)] = str(degrees)
    for art in outputs:
        p = os.path.join(art, key + '.jpg')
        if os.path.isfile(p):
            os.remove(p)
            state.append('deleted in ' + art.split(os.sep)[1])
    print('%-40s %s' % (', '.join(state) or 'registered', key))

with open(table, 'w', encoding='utf-8', newline='') as f:
    f.write('#media\tsystem\tkey\tdegrees (clockwise; -90 turns left)\n')
    for (m, s, k), d in sorted(rows.items()):
        f.write('%s\t%s\t%s\t%s\n' % (m, s, k, d))

print('\n%d rotation(s) of %d degrees registered in %s (%d rows in total)%s; '
      'reassemble %s.' % (len(keys), degrees, table, len(rows),
                          '; %d warning(s)' % warnings if warnings else '',
                          system))
