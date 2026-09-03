#!/usr/bin/env python
"""Print the "Published systems" table of PACK_FORMAT.md from the databases
in out/db, so the document is pasted from what was built, never typed.

    python3 tools/pack_index.py            # box2d table, every style summarised
    python3 tools/pack_index.py box3d

db_id and base URL are read back out of each db.json, not recomputed.
"""
import json
import re
import sys
import zipfile
from pathlib import Path

style_wanted = sys.argv[1] if len(sys.argv) > 1 else "box2d"
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
    # the install path carries the real folder name, with its own case
    folder = next((rel.split("/")[1] for rel in files
                   if len(rel.split("/")) >= 2), "")
    rows.append({
        "system": folder, "style": m.group(2), "db_id": db.get("db_id", ""),
        "url": db.get("base_files_url", ""),
        "images": sum(1 for f in files if f.lower().endswith(".jpg")),
        "bytes": sum(f.get("size", 0) for f in files.values()),
    })

table = sorted((r for r in rows if r["style"] == style_wanted),
               key=lambda r: r["system"].lower())
if not table:
    sys.exit(f"no {style_wanted} databases in out/db")
print("| System | Images | db_id | Media base URL |")
print("|---|---:|---|---|")
for r in table:
    print(f"| {r['system']} | {r['images']} | `{r['db_id']}` | {r['url']} |")
print(f"\n**{len(table)} systems, {sum(r['images'] for r in table):,} images, "
      f"{sum(r['bytes'] for r in table) / 1e9:.2f} GB.**\n")

for style in sorted({r["style"] for r in rows}):
    of_style = [r for r in rows if r["style"] == style]
    print("  %-8s %2d systems, %6d images, %7.1f MB" % (
        style, len(of_style), sum(r["images"] for r in of_style),
        sum(r["bytes"] for r in of_style) / 1e6))
