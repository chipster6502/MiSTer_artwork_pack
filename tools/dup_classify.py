"""Classify the duplicate images left in the box2d pack.

    python3 dup_classify.py [System ...]               (default: every system)

Groups manifest rows by md5. For each group of keys sharing one image:

  STALE POOL   pick_media(), run for each key with its own region, would
               choose two or more media: the pool predates region-aware
               fetching. Deleting those pool files and fetching again
               splits the group. The rule executes pick_media() rather than
               approximating it -- two approximations both miscounted.
  ONE COVER    same fiche and ScreenScraper holds one box for it. Removed
               by unification in assemble; should stay at 0.
  MIXED FICHE  different fiches, byte-identical image. Each one is a
               wrong-box suspect and is always listed.
"""
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import configparser

# the region chain the builder uses; same default as build_pack.Scope
_cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
_cp.read("scope.ini", encoding="utf-8")
REGIONS = [r.strip() for r in _cp.get("screenscraper", "regions",
                                      fallback="wor,us,eu,jp").split(",") if r.strip()]
RECIPE = ("box-2D", "mixrbv2")


def pick_media(medias, style, regions, prefer=""):
    """Verbatim copy of build_pack.pick_media(): what fetch would choose."""
    of_style = [m for m in medias if m.get("type") == style and m.get("url")]
    if prefer:
        for m in of_style:
            if m.get("region") == prefer:
                return m
    for region in regions:
        for m in of_style:
            if m.get("region") == region:
                return m
    return of_style[0] if of_style else None

# same words as build_pack.key_region(), inlined so the script also runs
# against a checkout that predates it
REGION_WORDS = {"wor": ("world",), "us": ("usa",), "eu": ("europe",),
                "jp": ("japan",), "sp": ("spain",), "fr": ("france",),
                "de": ("germany",), "it": ("italy",)}


def key_region(name):
    low = name.lower()
    for code, words in REGION_WORDS.items():
        for word in words:
            if f"({word}" in low or f", {word}" in low or f" {word})" in low:
                return code
    return ""


only = set(sys.argv[1:])

groups = defaultdict(list)          # (system, md5) -> [row]
for r in csv.DictReader(open("out/manifest-box2d.csv", encoding="utf-8")):
    if only and r["system"] not in only:
        continue
    groups[(r["system"], r["md5"])].append(r)

stats = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))  # sys -> cls -> [groups, extra files, extra bytes]
examples = defaultdict(list)

for (system, md5), rows in groups.items():
    if len(rows) < 2:
        continue
    fiches = {r["ss_id"] for r in rows}
    if len(fiches) > 1:
        cls = "MIXED FICHE"
    else:
        cls = "ONE COVER"
        # what fetch would pick for each key today: first style of the
        # recipe with media, the key's own region preferred
        picks = set()
        for r in rows:
            try:
                jeu = json.load(open(Path("work/meta") / system / (r["key"] + ".json"),
                                     encoding="utf-8"))
            except OSError:
                continue
            medias = jeu.get("medias") or []
            for style in RECIPE:
                m = pick_media(medias, style, REGIONS, key_region(r["key"]))
                if m:
                    picks.add(m.get("url"))
                    break
        if len(picks) >= 2:
            cls = "STALE POOL"
    extra = len(rows) - 1
    size = int(rows[0]["size"])
    s = stats[system][cls]
    s[0] += 1
    s[1] += extra
    s[2] += extra * size
    # every MIXED FICHE group is a wrong-box suspect: list them all, always
    if cls == "MIXED FICHE" or len(examples[(system, cls)]) < 2:
        examples[(system, cls)].append([(r["key"], r["ss_id"]) for r in rows])

print("%-20s %-12s %7s %8s %9s" % ("system", "class", "groups", "extra", "MB"))
tot = defaultdict(lambda: [0, 0, 0])
for system in sorted(stats):
    for cls in ("STALE POOL", "ONE COVER", "MIXED FICHE"):
        g, e, b = stats[system][cls]
        if g:
            print("%-20s %-12s %7d %8d %9.1f" % (system, cls, g, e, b / 1e6))
            tot[cls][0] += g; tot[cls][1] += e; tot[cls][2] += b
print()
for cls in ("STALE POOL", "ONE COVER", "MIXED FICHE"):
    g, e, b = tot[cls]
    print("%-12s %7d groups %8d extra files %9.1f MB" % (cls, g, e, b / 1e6))

print()
for (system, cls), exs in sorted(examples.items()):
    if cls != "MIXED FICHE" and not only:
        continue
    for keys in exs:
        print("%s [%s]" % (system, cls))
        for k, fiche in keys:
            print("    %-70s fiche %s" % (k, fiche))
