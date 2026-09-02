#!/usr/bin/env python3
"""Validate a generated db.json against Downloader_MiSTer's own parser.

    git clone --depth 1 https://github.com/MiSTer-devel/Downloader_MiSTer.git ../Downloader_MiSTer
    python validate_db.py out/db/snes_box2d.json.zip

Looks for the checkout in DOWNLOADER_SRC, ../Downloader_MiSTer/src, then
./Downloader_MiSTer/src. Only parses; downloads nothing.
"""

import json
import os
import sys
import zipfile
from pathlib import Path


def find_downloader():
    candidates = []
    env = os.environ.get("DOWNLOADER_SRC", "").strip()
    if env:
        candidates.append(Path(env))
    here = Path(__file__).resolve().parent
    candidates += [here.parent / "Downloader_MiSTer" / "src",
                   here / "Downloader_MiSTer" / "src"]
    for path in candidates:
        if (path / "downloader" / "db_entity.py").is_file():
            return path
    print("ERROR: Downloader_MiSTer not found. Looked in:")
    for path in candidates:
        print(f"  {path}")
    print("\nClone it next to this folder:")
    print("  git clone --depth 1 "
          "https://github.com/MiSTer-devel/Downloader_MiSTer.git "
          "../Downloader_MiSTer")
    print("...or point DOWNLOADER_SRC at an existing checkout.")
    sys.exit(1)


def load(path):
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            name = next((n for n in zf.namelist() if n.endswith(".json")), None)
            if not name:
                print(f"ERROR: no .json inside {path}")
                sys.exit(1)
            return json.loads(zf.read(name))
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    sys.path.insert(0, str(find_downloader()))
    from downloader.db_entity import DbEntity, DbEntityValidationException

    failures = 0
    for arg in sys.argv[1:]:
        path = Path(arg)
        if not path.is_file():
            print(f"MISSING  {path}")
            failures += 1
            continue
        raw = load(path)
        try:
            db = DbEntity(raw, raw.get("db_id", "?"))
        except DbEntityValidationException as exc:
            print(f"INVALID  {path.name}: {exc}")
            failures += 1
            continue

        total = sum(f.get("size", 0) for f in db.files.values())
        pext = sum(1 for f in db.files.values() if f.get("path") == "pext")
        sample = next(iter(db.files), None)
        print(f"VALID    {path.name}")
        print(f"         db_id          {db.db_id}")
        print(f"         files          {len(db.files)} "
              f"({pext} pext) | folders {len(db.folders)}")
        print(f"         total size     {total / 1e6:.1f} MB")
        print(f"         base_files_url {db.base_files_url}")
        print(f"         tags           {db.tag_dictionary}")
        if sample:
            print(f"         sample         {sample}")
            print(f"                        {db.base_files_url}{sample}")

    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
