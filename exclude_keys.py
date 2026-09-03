#!/usr/bin/env python
"""Exclude keys from the pack on purpose: add them to excludes.tsv, which
identify, fetch and assemble all honour, and drop what was already built.

    python exclude_keys.py NES exclude_nes.txt "hardware test, not a game"

One key per line; blank lines and '#' are ignored. A file, not arguments:
keys carry parentheses, quotes and '!!', which the shell mangles even when
quoted. The reason is optional and lands in the table for the next reader.
Reassemble the system afterwards.
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

args = sys.argv[1:]
if len(args) < 2:
    sys.exit(__doc__.strip())
system, listing = args[0], args[1]
reason = args[2] if len(args) > 2 else ''

with open(listing, encoding='utf-8') as f:
    keys = [l.strip() for l in f if l.strip() and not l.startswith('#')]

meta = os.path.join('work', 'meta', system)
if not os.path.isdir(meta):
    sys.exit('no such directory: %s' % meta)
outputs = [os.path.join('out', d, 'docs', system, 'Artwork')
           for d in os.listdir('out') if d.startswith('media-')] \
    if os.path.isdir('out') else []

table = 'excludes.tsv'
rows = {}
if os.path.isfile(table):
    with open(table, encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')
            if line and not line.startswith('#'):
                p = line.split('\t')
                if len(p) >= 2:
                    rows[(p[0], p[1])] = p[2] if len(p) > 2 else ''

for key in keys:
    state = []
    if not os.path.isfile(os.path.join(meta, key + '.json')):
        state.append('NO METADATA')
    if (system, key) in rows:
        state.append('already listed')
    rows[(system, key)] = reason or rows.get((system, key), '')
    for p in [os.path.join(meta, key + '.json')] + \
             [os.path.join(art, key + '.jpg') for art in outputs]:
        if os.path.isfile(p):
            os.remove(p)
            state.append('deleted ' + ('metadata' if p.endswith('.json')
                                       else p.split(os.sep)[1]))
    print('%-40s %s' % (', '.join(state) or 'listed', key))

with open(table, 'w', encoding='utf-8', newline='') as f:
    f.write('#system\tkey\treason\n')
    for (s, k), r in sorted(rows.items()):
        f.write('%s\t%s\t%s\n' % (s, k, r))

print('\n%d key(s) excluded from %s (%d rows in %s); reassemble it.'
      % (len(keys), system, len(rows), table))
