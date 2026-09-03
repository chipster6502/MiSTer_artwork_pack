#!/usr/bin/env python
"""
Build a machine-readable index of every published pack.

    python pack_index.py                 # all styles found in out/db
    python pack_index.py --style box2d

Writes out/db/index.json and prints a Markdown table. Everything comes from
the databases already built, so it never disagrees with what is published:
db_id and base_files_url are read back out of each db, not recomputed.

The file is for other people's tools. A consumer needs the contract, not just
the URLs, so index.json carries a 'layout' block describing what a pack holds
and how to resolve a dump to an image.
"""
import argparse
import json
import re
import time
import zipfile
from pathlib import Path

LAYOUT = {
    "artwork": "docs/<system>/Artwork/<key>.jpg",
    "one_image_per": "game, not per dump",
    "files": {
        "index.tsv": "name<TAB>crc<TAB>size<TAB>key - every known dump of "
                     "the game mapped to the image that represents it. "
                     "crc and size are empty where the catalogue has none "
                     "(arcade, Neo Geo).",
        "gameinfo.tsv": "key<TAB>name<TAB>year<TAB>genre<TAB>developer<TAB>players",
        "manifest.tsv": "key<TAB>style<TAB>ss_system_id - which style the "
                        "image came from and which ScreenScraper system, "
                        "which is what makes cross-catalogue fallback "
                        "deterministic.",
        "synopsis_<lang>.tsv": "key<TAB>synopsis, one file per language",
    },
    "resolution_order": [
        "the dump name as a filename: docs/<system>/Artwork/<name>.jpg",
        "index.tsv by name",
        "a trailing '(setname)' in the name, when it is itself a key",
        "index.tsv by crc+size",
        "index.tsv by title with the bracketed tags removed, skipped when "
        "that title is not unique",
    ],
    "notes": [
        "Disc systems index the crc and size of the .cue file, which is "
        "what the Redump DAT lists first. A consumer holding a .cue/.bin "
        "set matches on it exactly; one holding a .chd hashes a different "
        "file by construction and must resolve by name. This is about which "
        "file the hash describes, not about whether a .chd can be hashed.",
        "Catalogues that share a core keep separate folders (GAMEBOY/GBC, "
        "FDS/NES); a consumer should fall back to the sibling folder.",
    ],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--style", default=None, help="only this style label")
    ap.add_argument("--out", default="out/db/index.json")
    args = ap.parse_args()

    db_dir = Path("out/db")
    if not db_dir.is_dir():
        raise SystemExit("no out/db: run package first")

    systems = []
    for zip_path in sorted(db_dir.glob("*.json.zip")):
        m = re.match(r"(.+)_([^_]+)\.json\.zip$", zip_path.name)
        if not m:
            continue
        style = m.group(2)
        if args.style and style != args.style:
            continue
        with zipfile.ZipFile(zip_path) as zf:
            db = json.loads(zf.read(zf.namelist()[0]))
        files = db.get("files") or {}
        images = [f for f in files if f.lower().endswith(".jpg")]
        # The install path carries the real folder name, with its own case.
        folder = ""
        for rel in files:
            parts = rel.split("/")
            if len(parts) >= 2:
                folder = parts[1]
                break
        systems.append({
            "system": folder,
            "style": style,
            "db_id": db.get("db_id", ""),
            "db_zip": zip_path.name,
            "base_files_url": db.get("base_files_url", ""),
            "images": len(images),
            "files": len(files),
            "bytes": sum(f.get("size", 0) for f in files.values()),
        })

    index = {
        "generated": int(time.time()),
        "layout": LAYOUT,
        "systems": sorted(systems, key=lambda s: (s["style"], s["system"])),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(index, ensure_ascii=False, indent=1),
                   encoding="utf-8")

    print("| System | Images | db_id | media |")
    print("|---|---:|---|---|")
    for s in index["systems"]:
        print("| %s | %d | `%s` | %s |"
              % (s["system"], s["images"], s["db_id"], s["base_files_url"]))
    total = sum(s["images"] for s in index["systems"])
    size = sum(s["bytes"] for s in index["systems"]) / 1e6
    names = {s["system"] for s in index["systems"]}
    styles = sorted({s["style"] for s in index["systems"]})
    for style in styles:
        rows = [s for s in index["systems"] if s["style"] == style]
        print("  %-8s %2d systems, %5d images, %7.1f MB" % (
            style, len(rows), sum(s["images"] for s in rows),
            sum(s["bytes"] for s in rows) / 1e6))
    print("\n%d rows, %d systems, %d styles: %d images, %.1f MB  ->  %s"
          % (len(index["systems"]), len(names), len(styles), total, size, out))

if __name__ == "__main__":
    main()
