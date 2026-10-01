#!/usr/bin/env python
"""Print the "Published systems" tables of PACK_FORMAT.md from the databases
in out/db, so the document is pasted from what was built, never typed.

    python3 tools/pack_index.py

First one row per system with its group and the image count of each pack,
then one row per style with what it downloads and what it takes on a card.
The group is read back out of each db.json, not recomputed.
"""
import json
import re
import sys
import zipfile
from pathlib import Path

PACKS = (("Artwork", "Boxes"), ("Screenshots", "Screenshots"), ("Titles", "Titles"))
BLOCKS = (32 * 1024, 128 * 1024)

db_dir = Path("out/db")
if not db_dir.is_dir():
    sys.exit("no out/db: run package first")

rows = []
for zip_path in sorted(db_dir.glob("*.json.zip")):
    m = re.match(r"(.+)_([^_]+)\.json\.zip$", zip_path.name)
    if not m:
        continue
    with zipfile.ZipFile(zip_path) as zf:
        db = json.loads(zf.read(zf.namelist()[0]))
    files = db.get("files") or {}
    # the install path carries the real folder names, with their own case
    parts = next((rel.split("/") for rel in files if len(rel.split("/")) >= 3),
                 ["", "", ""])
    group = re.search(r"artworkdb-([^/]+)/", db.get("base_files_url", ""))
    sizes = [f.get("size", 0) for f in files.values()]
    rows.append({
        "system": parts[1], "folder": parts[2], "style": m.group(2),
        "group": group.group(1) if group else "",
        "images": sum(1 for f in files if f.lower().endswith((".jpg", ".png"))),
        "bytes": sum(sizes),
        # a FAT or exFAT card gives every file whole blocks, however small
        "card": [sum(-(-s // b) * b for s in sizes) for b in BLOCKS],
    })
if not rows:
    sys.exit("no databases in out/db")

packs = [(folder, label) for folder, label in PACKS
         if any(r["folder"] == folder for r in rows)]


def counts(of):
    # the box styles serve the same keys, so one number stands for all three
    # unless a build broke that
    values = [f"{r['images']:,}" for r in sorted(of, key=lambda r: r["style"])]
    return " / ".join(dict.fromkeys(values)) or "—"


print("| System | Group | " + " | ".join(label for _, label in packs) + " |")
print("|---|---|" + "---:|" * len(packs))
systems = sorted({r["system"] for r in rows}, key=str.lower)
for system in systems:
    of_system = [r for r in rows if r["system"] == system]
    group = ", ".join(sorted({r["group"] for r in of_system}))
    cells = [counts([r for r in of_system if r["folder"] == f]) for f, _ in packs]
    print(f"| {system} | {group} | " + " | ".join(cells) + " |")
totals = []
for folder, _ in packs:
    by_style = {}
    for r in rows:
        if r["folder"] == folder:
            by_style[r["style"]] = by_style.get(r["style"], 0) + r["images"]
    totals.append(" / ".join(dict.fromkeys(
        f"**{n:,}**" for _, n in sorted(by_style.items()))))
print(f"| **{len(systems)} systems** | | " + " | ".join(totals) + " |")

print()
print("| Style | Folder | Systems | Images | Download | "
      + " | ".join(f"Card, {b // 1024} KB blocks" for b in BLOCKS) + " |")
print("|---|---|---:|---:|---:|" + "---:|" * len(BLOCKS))
for folder, _ in packs:
    for style in sorted({r["style"] for r in rows if r["folder"] == folder}):
        of_style = [r for r in rows if r["style"] == style]
        card = [sum(r["card"][i] for r in of_style) for i in range(len(BLOCKS))]
        print(f"| `{style}` | `{folder}` | {len(of_style)} | "
              f"{sum(r['images'] for r in of_style):,} | "
              f"{sum(r['bytes'] for r in of_style) / 1e9:.2f} GB | "
              + " | ".join(f"{c / 1e9:.2f} GB" for c in card) + " |")
