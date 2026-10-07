#!/usr/bin/env python3
"""MiSTer Boxart Pack builder.

Four resumable stages driven by scope.ini:

  identify  jeuInfos per game -> work/meta/<System>/<key>.json
  fetch     first source of the recipe that has media -> work/pool/<source>/
            (ScreenScraper media types, or libretro-thumbnails folders)
  assemble  box styles: baseline JPEG (<=768 px) in docs/<System>/Artwork/;
            screenshot styles: PNG at native size in their own folder;
            plus gameinfo.tsv, synopsis_<lang>.tsv, index.tsv, manifest.tsv
  package   one Downloader database per system -> out/db/

Every stage skips what already exists on disk; delete a file to rebuild it.
Credentials come from the environment only: SS_DEVID, SS_DEVPASSWORD,
SS_SSID, SS_SSPASSWORD.
"""

import argparse
import concurrent.futures
import configparser
import csv
import hashlib
import io
import json
import os
import posixpath
import re
import subprocess
import sys
import threading
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

API_BASE = "https://api.screenscraper.fr/api2"

# ----------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------

def log(msg):
    print(msg, flush=True)


def die(msg):
    log(f"ERROR: {msg}")
    sys.exit(1)


def md5_file(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def clean_text(value):
    """Collapse whitespace so a synopsis can live in one TSV cell."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


# ----------------------------------------------------------------------------
# scope / credentials
# ----------------------------------------------------------------------------

# children of Mame (75) that are not arcade games: LCD handhelds, "non Jeu"
DEFAULT_REJECT_SYSTEMS = "52,168"


class Scope:
    def __init__(self, path, style=None):
        cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        if not Path(path).is_file():
            die(f"scope file not found: {path}")
        cp.read(path, encoding="utf-8")

        ss = cp["screenscraper"] if cp.has_section("screenscraper") else {}
        self.softname = ss.get("softname", "").strip()
        self.regions = [r.strip() for r in ss.get("regions", "wor,us,eu,jp").split(",") if r.strip()]
        langs_raw = ss.get("langs", "all").strip()
        # None means "every language present in the metadata" (default)
        self.langs = None if langs_raw.lower() == "all" else \
            [l.strip() for l in langs_raw.split(",") if l.strip()]
        self.delay = float(ss.get("delay", "0.8"))
        # must not exceed the account's maxthreads (see any reply's ssuser)
        self.threads = max(1, int(ss.get("threads", "2")))

        pk = cp["pack"] if cp.has_section("pack") else {}

        # every style declared once, selected per run with --style
        self.styles = {}
        self.style_opts = {}
        for section in cp.sections():
            if not section.startswith("style:"):
                continue
            label = section.split(":", 1)[1].strip()
            raw = cp[section].get("recipe", "").strip()
            if not label or not raw:
                die(f"scope.ini: [{section}] needs a recipe")
            self.styles[label] = [s.strip() for s in raw.split(">")
                                  if s.strip()]
            self.style_opts[label] = dict(cp[section])

        if style:
            if style not in self.styles:
                known = ", ".join(sorted(self.styles)) or "none declared"
                die(f"scope.ini: unknown style '{style}' ({known})")
            self.style_label = style
            self.recipe = self.styles[style]
        else:
            self.recipe = [s.strip() for s in pk.get("style_recipe", "box-2D>mixrbv2").split(">") if s.strip()]
            self.style_label = pk.get("style_label", "").strip() or \
                self.recipe[0].lower().replace("-", "").replace("_", "")
        # per-style quality: styles compress very differently
        opts = self.style_opts.get(self.style_label, {})
        self.quality = int(opts.get("quality", pk.get("quality", "80")))
        # A screenshot style is a separate pack installed next to the boxes:
        # its own folder, and PNG so pixel art survives.
        self.folder = opts.get("folder", "Artwork").strip()
        self.image_ext = opts.get("format", "jpg").strip().lower()
        if self.image_ext not in ("jpg", "png"):
            die(f"scope.ini: [style:{self.style_label}] format must be jpg or png")
        if not re.fullmatch(r"[A-Za-z0-9]+", self.folder):
            die(f"scope.ini: [style:{self.style_label}] folder must be one word")
        # systems whose PNGs keep every colour even past one card block;
        # 'all' for every system
        self.lossless_systems = {s.strip() for s in
                                 opts.get("lossless", "").split(",") if s.strip()}
        self.max_px = int(pk.get("max_px", "768"))
        # 0 turns the check off: screenshots are uploads, not rendered
        # composites, and a series of discs can share one screen
        self.placeholder_min = int(opts.get("placeholder_min",
                                            pk.get("placeholder_min", "3")))
        # past this box a source was enlarged by an emulator or an uploader;
        # a later source within it shows the frame the console did, and wins
        self.native_max = parse_box(opts.get("native_max", ""),
                                    f"[style:{self.style_label}] native_max")
        self.url_base = pk.get("url_base", "").rstrip("/")
        self.db_id_prefix = pk.get("db_id_prefix", "").strip()
        # db branch root, same placeholders as url_base; only verify needs it
        self.db_url_base = pk.get("db_url_base", "").rstrip("/")

        self.systems = {}  # name -> dict(ss_id, kind, source)
        for section in cp.sections():
            if not section.startswith("system:"):
                continue
            name = section.split(":", 1)[1].strip()
            accept = cp[section].get("accept_systems", "").strip()
            reject = cp[section].get("reject_systems",
                                     DEFAULT_REJECT_SYSTEMS).strip()
            self.systems[name] = {
                "ss_id": cp[section].get("ss_id", "").strip(),
                "kind": cp[section].get("kind", "console").strip(),
                "source": cp[section].get("source", "").strip(),
                # media repo; db_id stays per system, so regrouping breaks
                # nobody's downloader.ini
                "group": cp[section].get("group", "").strip()
                         or name.lower(),
                # extra ids accepted besides the family of ss_id
                "accept": tuple(x.strip() for x in accept.split(",") if x.strip()),
                "reject": tuple(x.strip() for x in reject.split(",") if x.strip()),
                # libretro-thumbnails repository, for the lr-* recipe sources
                "libretro": cp[section].get("libretro", "").strip(),
                # recipe_<style> reorders sources where a measured
                # comparison found the other one better for this system
                "recipes": {k[len("recipe_"):]: [s.strip() for s in v.split(">")
                                                 if s.strip()]
                            for k, v in cp[section].items()
                            if k.startswith("recipe_") and v.strip()},
                # placeholder_min_<style>: a system whose source repeats one
                # generic screen across games, where the style has the check off
                "placeholder_mins": {k[len("placeholder_min_"):]: int(v)
                                     for k, v in cp[section].items()
                                     if k.startswith("placeholder_min_")
                                     and v.strip()},
                # native_max_<style>: a system with modes past the style's box,
                # or 'off' where no raster is native (vector displays)
                "native_maxes": {k[len("native_max_"):]:
                                 parse_box(v, f"[{section}] {k}")
                                 for k, v in cp[section].items()
                                 if k.startswith("native_max_") and v.strip()},
            }
        if not self.softname:
            die("scope.ini: [screenscraper] softname is required "
                "(must match the app name registered on ScreenScraper)")
        self.excludes = load_excludes()
        self.media_rejects = load_media_rejects()
        self.image_aliases = load_image_aliases()
        if not self.systems:
            die("scope.ini: no [system:<Name>] sections found")
        # a JPEG box style is resized to max_px anyway; the size of its
        # source says nothing about native pixels
        if self.image_ext != "png" and any(
                self.native_max_for(s) for s in self.systems):
            die(f"scope.ini: native_max needs a png style, not "
                f"[style:{self.style_label}]")

    def lossless_for(self, system):
        return "all" in self.lossless_systems or system in self.lossless_systems

    def recipe_for(self, system):
        """The style's recipe, or the system's own order for this style."""
        return self.systems[system]["recipes"].get(self.style_label, self.recipe)

    def placeholder_min_for(self, system):
        return self.systems[system]["placeholder_mins"].get(
            self.style_label, self.placeholder_min)

    def native_max_for(self, system):
        """(width, height) past which a source is not native, or None."""
        boxes = self.systems[system]["native_maxes"]
        if self.style_label in boxes:
            return boxes[self.style_label]
        return self.native_max


def parse_box(value, where):
    """'400x300' as (400, 300); empty or 'off' as None."""
    value = value.strip().lower()
    if value in ("", "off"):
        return None
    m = re.fullmatch(r"(\d+)\s*x\s*(\d+)", value)
    if not m:
        die(f"scope.ini: {where} must be WIDTHxHEIGHT or off, not '{value}'")
    return int(m.group(1)), int(m.group(2))


def credentials():
    creds = {k: os.environ.get(k, "").strip()
             for k in ("SS_DEVID", "SS_DEVPASSWORD", "SS_SSID", "SS_SSPASSWORD")}
    missing = [k for k, v in creds.items() if not v]
    if missing:
        die("missing environment variables: " + ", ".join(missing) +
            "\n  export them in your shell before running (never put them in files).")
    return creds


# ----------------------------------------------------------------------------
# HTTP with manual percent-encoding (urlencode breaks SS auth)
# ----------------------------------------------------------------------------

def build_url(endpoint, params):
    query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items() if v is not None and str(v) != "")
    return f"{API_BASE}/{endpoint}?{query}"


def http_get(url, softname, timeout=45, tries=3):
    last = None
    for attempt in range(1, tries + 1):
        try:
            req = Request(url, headers={"User-Agent": softname})
            with urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last = exc
            if isinstance(exc, HTTPError) and exc.code == 404:
                raise
            time.sleep(2 * attempt)
    raise last


AUTH_PARAMS = ("devid", "devpassword", "ssid", "sspassword")

# plain-text refusals SS serves instead of JSON when it turns the API off
QUOTA_MARKERS = (
    "maximum threads",
    "API totalement",
    "API fermé",
    # "closed" alone appears in ordinary PHP notices; qualified it still
    # catches SS's own refusal
    "api closed",
    "quota",
)

# A PHP notice leaking from the scraper is a glitch on one record, not a
# refusal to serve; treating it as a quota wall aborted whole runs.
PHP_NOTICE_MARKERS = (
    "<b>warning</b>",
    "<b>notice</b>",
    "<b>fatal error</b>",
    "<b>deprecated</b>",
)


def strip_auth(url):
    """Drop credential parameters: SS embeds them in media URLs and errors."""
    if "?" not in url:
        return url
    base, _, query = url.partition("?")
    keep = [part for part in query.split("&")
            if part.split("=", 1)[0].lower() not in AUTH_PARAMS]
    return base + ("?" + "&".join(keep) if keep else "")


def redact(text):
    """Blank out credential values inside arbitrary text."""
    for param in AUTH_PARAMS:
        text = re.sub(rf"({param}=)[^&\"'\s]*", r"\1***", text,
                      flags=re.IGNORECASE)
    return text


def redact_json(node):
    """Recursively strip auth params from every URL in a parsed reply."""
    if isinstance(node, dict):
        return {k: (strip_auth(v) if isinstance(v, str) and "devid=" in v
                    else redact_json(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [redact_json(v) for v in node]
    return node


def json_after_noise(text):
    """The jeu from a reply with junk prepended, or None. A body that starts
    with '{' and fails to parse is broken, not dirty."""
    start = text.find("{")
    if start <= 0:
        return None
    try:
        jeu = json.loads(text[start:])["response"]["jeu"]
    except (ValueError, KeyError, TypeError):
        return None
    return jeu if isinstance(jeu, dict) and jeu.get("id") else None


def check_quota_wall(body_text):
    """Abort only on a real refusal; valid JSON without a game is a miss."""
    stripped = body_text.strip()
    # Judge the JSON after a PHP notice, never the raw text: every reply
    # mentions quotas in its usage counters.
    if not stripped.startswith(("{", "[")):
        brace = stripped.find("{")
        if brace > 0 and any(m in stripped.lower() for m in PHP_NOTICE_MARKERS):
            stripped = stripped[brace:]
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            data = json.loads(stripped)
        except ValueError:
            pass
        else:
            error = ""
            if isinstance(data, dict):
                error = str(data.get("erreur") or data.get("error") or "")
            if not any(m.lower() in error.lower() for m in QUOTA_MARKERS):
                return
            body_text = error
    low = body_text.lower()
    if (any(m in low for m in PHP_NOTICE_MARKERS)
            and not any(m.lower() in low for m in QUOTA_MARKERS)):
        # server-side hiccup on one game: let the caller mark it a miss
        return
    for marker in QUOTA_MARKERS:
        if marker.lower() in body_text.lower():
            die("ScreenScraper refused the request (quota/threads/API closed):\n"
                f"  '{redact(body_text)[:160]}'\n"
                "  Stop for now and re-run later - every stage resumes where it left off.")


ABORT = threading.Event()
COUNTER_LOCK = threading.Lock()


def run_parallel(tasks, worker, threads, delay, label, total):
    """Bounded thread pool. die() in a worker only kills that thread, so
    refusals travel through ABORT and are re-raised here."""
    done = {"n": 0}
    failed = {"n": 0}
    results = []

    def wrapped(task):
        if ABORT.is_set():
            return None
        try:
            outcome = worker(task)
        except SystemExit:
            ABORT.set()
            return None
        except Exception as exc:
            # nothing cached, so the item stays in todo for the next run
            with COUNTER_LOCK:
                failed["n"] += 1
            log(f"  ! {label} failed ({type(exc).__name__}: {exc})")
            return None
        if delay:
            time.sleep(delay)
        with COUNTER_LOCK:
            done["n"] += 1
            if done["n"] % 100 == 0:
                log(f"  ... {label} {done['n']}/{total}")
        return outcome

    if threads <= 1:
        for task in tasks:
            results.append(wrapped(task))
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as pool:
            results = list(pool.map(wrapped, tasks))
    if ABORT.is_set():
        die("ScreenScraper refused a request - stopping. "
            "Re-run later; every stage resumes where it left off.")
    if failed["n"]:
        log(f"  ! {failed['n']} transient failure(s); re-run to retry them")
    return [r for r in results if r is not None]


# ----------------------------------------------------------------------------
# game sources (what to build): No-Intro DAT, MRA folder, plain list
# ----------------------------------------------------------------------------

# not catalogued by SS: querying them only burns quota
UNOFFICIAL_MARKERS = ("(unl)", "(aftermarket)", "(pirate)", "(homebrew)",
                      "(test program)", "(program)", "(kiosk)",
                      "(debug", "(bios)")


def load_entries(source, limit=0):
    """Return list of dicts: {key, romnom, crc, size}."""
    if ":" not in source:
        die(f"bad source spec '{source}' (expected dat:/mra:/list: prefix)")
    kind, _, path = source.partition(":")
    path = Path(path)
    entries = []

    if kind == "dat":
        if not path.is_file():
            die(f"DAT not found: {path}")
        root = ET.parse(path).getroot()
        for game in root.iter("game"):
            name = game.get("name", "").strip()
            rom = game.find("rom")
            if not name or rom is None or name.startswith("[BIOS]"):
                continue
            low = name.lower()
            if any(marker in low for marker in UNOFFICIAL_MARKERS):
                continue
            entries.append({
                "key": name,
                "romnom": rom.get("name", name),
                "crc": (rom.get("crc") or "").strip().lower(),
                "size": (rom.get("size") or "").strip(),
                # P/C XML only: official parent/clone relationship
                "cloneof": (game.get("cloneof") or "").strip(),
                # retail dumps carry <release>; compilations and protos do not
                "release": game.find("release") is not None,
            })

    elif kind == "mame":
        if not path.is_file():
            die(f"MAME listxml not found: {path}\n"
                "  Download mameXXXXlx.zip from "
                "https://github.com/mamedev/mame/releases and unzip it here.")
        # 300+ MB: stream it, and keep playable coin-op machines only
        for _, el in ET.iterparse(path, events=("end",)):
            if el.tag != "machine":
                continue
            name = el.get("name", "")
            playable = (el.get("isdevice") != "yes"
                        and el.get("isbios") != "yes"
                        and el.get("ismechanical") != "yes"
                        and el.get("runnable", "yes") == "yes")
            inp = el.find("input")
            if name and playable and inp is not None \
                    and inp.get("coins") is not None:
                entries.append({"key": name, "romnom": name + ".zip",
                                "crc": "", "size": "",
                                "cloneof": (el.get("cloneof") or "").strip()})
            el.clear()

    elif kind == "mra":
        if not path.is_dir():
            die(f"MRA folder not found: {path}")
        for mra in sorted(path.glob("*.mra")):
            try:
                setname = ET.parse(mra).getroot().findtext("setname", "").strip()
            except ET.ParseError:
                continue
            if setname:
                entries.append({"key": setname, "romnom": setname + ".zip",
                                "crc": "", "size": ""})

    elif kind == "list":
        if not path.is_file():
            die(f"list not found: {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            entries.append({"key": line, "romnom": line + ".zip",
                            "crc": "", "size": ""})
    else:
        die(f"unknown source kind '{kind}'")

    # dedupe by key, keep order
    seen, unique = set(), []
    for e in entries:
        if e["key"] not in seen:
            seen.add(e["key"])
            unique.append(e)
    if limit > 0:
        unique = unique[:limit]
    return unique


# ----------------------------------------------------------------------------
# variant clustering: one <game> per dump in a DAT; elect one representative
# per game and index every variant to it
# ----------------------------------------------------------------------------

REGION_WORDS = {"wor": ("world",), "us": ("usa",), "eu": ("europe",),
                "jp": ("japan",), "sp": ("spain",), "fr": ("france",),
                "de": ("germany",), "it": ("italy",)}
# summed demotion weights; a prototype sinks deeper than a re-release
VARIANT_WEIGHTS = (
    (3, ("beta", "proto", "sample", "(demo", "(alt", "(program", "(debug")),
    (1, ("virtual console", "classic mini", "switch online", "(arcade)",
         "(gamecube", "(wii", "collection", "anthology", "compilation",
         "archives", "anniversary", "competition cart", "mail-order",
         "aftermarket", "evercade", "(unl", "(pirate")),
    (1, ("(rev", "(v1.", "(v2.")),
)


def norm_title(name):
    """Title without any (...) [...] qualifiers, lowercase, single-spaced."""
    return re.sub(r"\s+", " ",
                  re.sub(r"\([^)]*\)|\[[^\]]*\]", "", name)).strip().lower()


def region_score(name, regions):
    low = name.lower()
    for rank, region in enumerate(regions):
        for word in REGION_WORDS.get(region, (region,)):
            if f"({word}" in low or f", {word}" in low or f" {word})" in low:
                return rank
    return len(regions)


def disc_number(name):
    """Disc N; 1.5 when the dump carries no disc tag.

    The election must always pick the SAME disc of a multi-disc game, or the
    key flips between discs. An untagged dump ranks between disc 1 and 2: a
    single-disc release represents the whole game, but so does an untagged
    sampler, and that must never outrank the real first disc.
    """
    m = re.search(r"\(disc\s*(\d+)", name.lower())
    if not m:
        return 1.5
    return int(m.group(1))


def variant_penalty(name):
    low = name.lower()
    return sum(weight
               for weight, markers in VARIANT_WEIGHTS
               for marker in markers if marker in low)


def elect_key(entry, regions):
    """Representative sort key: penalty, region chain, <release>, disc, name.
    Region outranks <release> because No-Intro tags it unevenly."""
    return (variant_penalty(entry["key"]),
            region_score(entry["key"], regions),
            0 if entry.get("release") else 1,
            disc_number(entry["key"]),
            len(entry["key"]), entry["key"])


def cluster_mame_entries(entries):
    """Group MAME sets under their parent: nothing to elect, MAME's parent is
    the reference set. An orphan clone becomes its own group."""
    by_name = {e["key"]: e for e in entries}
    parents, members = [], []
    for e in entries:
        parent = e["cloneof"] if e["cloneof"] in by_name else ""
        if not parent:
            parents.append(e)
        members.append((e["key"], "", "", e["cloneof"]
                        if e["cloneof"] in by_name else e["key"]))
    return parents, members


def cluster_dat_entries(entries, regions):
    """Group dumps and elect one representative each; members rows are
    (name, crc, size, key). Groups by cloneof when the DAT is P/C, by
    normalized title otherwise. The parent is not automatically elected."""
    by_name = {e["key"]: e for e in entries}
    # real parents: the parent entry has no cloneof, so it needs its own group
    parents = {e["cloneof"] for e in entries
               if e.get("cloneof") and e["cloneof"] in by_name}
    groups = {}
    for entry in entries:
        parent = entry.get("cloneof") or ""
        if parent in by_name:
            gid = parent
        elif entry["key"] in parents:
            gid = entry["key"]
        else:
            gid = norm_title(entry["key"])
        groups.setdefault(gid, []).append(entry)
    reps, members = [], []
    for group in groups.values():
        group = sorted(group, key=lambda e: elect_key(e, regions))
        rep = group[0]
        reps.append(rep)
        for entry in group:
            members.append((entry["key"], entry["crc"], entry["size"],
                            rep["key"]))
    reps.sort(key=lambda e: e["key"])
    members.sort()
    return reps, members


def write_members(meta_dir, members):
    with open(meta_dir / "_members.tsv", "w", encoding="utf-8", newline="") as f:
        f.write("#name\tcrc\tsize\tkey\n")
        for name, crc, size, key in members:
            f.write(f"{name}\t{crc}\t{size}\t{key}\n")


# ----------------------------------------------------------------------------
# stage 1: identify (jeuInfos)
# ----------------------------------------------------------------------------

def fetch_systems(scope, creds):
    """systemesListe.php, cached in work/systems.json: parent of each system."""
    cache = Path("work/systems.json")
    if cache.is_file():
        return json.loads(cache.read_text(encoding="utf-8"))
    params = {"devid": creds["SS_DEVID"], "devpassword": creds["SS_DEVPASSWORD"],
              "ssid": creds["SS_SSID"], "sspassword": creds["SS_SSPASSWORD"],
              "softname": scope.softname, "output": "json"}
    body = http_get(build_url("systemesListe.php", params), scope.softname)
    data = json.loads(body.decode("utf-8", errors="replace"))
    systems = {}
    for system in data["response"]["systemes"]:
        sid = str(system.get("id", ""))
        names = system.get("noms") or {}
        systems[sid] = {
            "name": names.get("nom_eu") or names.get("nom_us") or "",
            "parent": str(system.get("parentid", "") or ""),
        }
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(systems, ensure_ascii=False, indent=1),
                     encoding="utf-8")
    return systems


def in_family(systems, resolved_id, wanted_id, extra_ok=(), rejected=()):
    """Whether a reply may be accepted for the queried system. A rejected id
    always loses, even as a legitimate child (Game & Watch hangs off Mame)."""
    if resolved_id and resolved_id in rejected:
        return False
    if not resolved_id or resolved_id == wanted_id:
        return True
    if resolved_id in extra_ok:
        return True
    seen, cur = set(), resolved_id
    while cur and cur not in seen:
        seen.add(cur)
        cur = (systems.get(cur) or {}).get("parent", "")
        if cur == wanted_id:
            return True
    return False


def load_overrides(path=Path("overrides.tsv")):
    """key -> systemeid from overrides.tsv: jeuInfos falls back to a global
    name search, so generic names need the subsystem spelled out."""
    table = {}
    if not Path(path).is_file():
        return table
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            table[(parts[0], parts[1])] = parts[2].strip()
        elif len(parts) == 2:
            table[(None, parts[0])] = parts[1].strip()
    return table


def load_excludes(path=Path("excludes.tsv")):
    """{(system, key)} from excludes.tsv: keys reviewed out of the pack."""
    table = set()
    if not Path(path).is_file():
        return table
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            table.add((parts[0], parts[1]))
    return table


def query_game(scope, creds, system_id, entry):
    """One jeuInfos call. Returns (jeu_or_None, raw_text)."""
    params = {
        "devid": creds["SS_DEVID"], "devpassword": creds["SS_DEVPASSWORD"],
        "ssid": creds["SS_SSID"], "sspassword": creds["SS_SSPASSWORD"],
        "softname": scope.softname, "output": "json",
        "systemeid": system_id, "romtype": "rom",
        "romnom": entry["romnom"],
    }
    if entry.get("crc"):
        params["crc"] = entry["crc"]
    if entry.get("size"):
        params["romtaille"] = entry["size"]
    try:
        body = http_get(build_url("jeuInfos.php", params), scope.softname)
    except HTTPError as exc:
        if exc.code == 404:
            return None, "404"
        raise
    text = body.decode("utf-8", errors="replace")
    try:
        jeu = json.loads(text)["response"]["jeu"]
        assert jeu.get("id")
    except (ValueError, KeyError, AssertionError):
        # SS sometimes prepends a PHP notice to a good reply: salvage the JSON
        jeu = json_after_noise(text)
        if jeu is not None:
            return jeu, text
        return None, text
    return jeu, text


def stage_identify(scope, creds, only_system=None, limit=0, retry_miss=False):
    systems = fetch_systems(scope, creds)
    overrides = load_overrides()
    if overrides:
        log(f"[identify] {len(overrides)} subsystem override(s) loaded")
    for system, cfg in scope.systems.items():
        if only_system and system != only_system:
            continue
        meta_dir = Path("work/meta") / system
        meta_dir.mkdir(parents=True, exist_ok=True)
        entries = load_entries(cfg["source"])
        if cfg["source"].startswith("dat:"):
            entries, members = cluster_dat_entries(entries, scope.regions)
            log(f"[identify] {system}: {len(members)} dumps clustered into "
                f"{len(entries)} games")
        elif cfg["source"].startswith("mame:"):
            entries, members = cluster_mame_entries(entries)
            log(f"[identify] {system}: {len(members)} sets clustered into "
                f"{len(entries)} parents")
        else:
            members = [(e["key"], e["crc"], e["size"], e["key"])
                       for e in entries]
        write_members(meta_dir, members)
        if limit > 0:
            entries = entries[:limit]
        log(f"[identify] {system}: {len(entries)} entries "
            f"(ss_id={cfg['ss_id']}, kind={cfg['kind']})")

        done = excluded = 0
        todo = []
        for e in entries:
            if (system, e["key"]) in scope.excludes:
                excluded += 1
                continue
            marker = meta_dir / (e["key"] + ".miss")
            if marker.is_file() and retry_miss:
                # .miss is cached like a hit; clearing it lets overrides apply
                marker.unlink()
            if (meta_dir / (e["key"] + ".json")).is_file() or marker.is_file():
                done += 1
            else:
                todo.append(e)

        def identify_one(e):
            out = meta_dir / (e["key"] + ".json")
            marker = meta_dir / (e["key"] + ".miss")
            query_id = (overrides.get((system, e["key"]))
                        or overrides.get((None, e["key"]))
                        or cfg["ss_id"])
            jeu, text = query_game(scope, creds, query_id, e)
            if jeu is None:
                if text == "404":
                    marker.write_text("404", encoding="utf-8")
                    return "miss"
                check_quota_wall(text)
                marker.write_text(redact(text), encoding="utf-8")
                return "miss"

            # transversality guard: a missing image beats the wrong box
            res_id, res_name = ss_subsystem(jeu)
            if not in_family(systems, res_id, query_id, cfg["accept"],
                             cfg["reject"]):
                marker.write_text(
                    f"out-of-family: resolved to {res_id} ({res_name}) "
                    f"while querying {query_id}", encoding="utf-8")
                return "rejected"

            if is_nongame(jeu):
                marker.write_text("non-game: ScreenScraper resolved this to "
                                  "its catch-all non-game entry",
                                  encoding="utf-8")
                return "nongame"

            out.write_text(
                json.dumps(redact_json(jeu), ensure_ascii=False, indent=1),
                encoding="utf-8")
            return "hit"

        outcomes = run_parallel(todo, identify_one, scope.threads, scope.delay,
                                "identified", len(todo))
        hits = outcomes.count("hit")
        miss = outcomes.count("miss")
        rejected = outcomes.count("rejected")
        nongame = outcomes.count("nongame")

        log(f"[identify] {system}: {hits} new, {miss} miss, "
            f"{rejected} out-of-family, {nongame} non-game, "
            f"{done} already cached"
            + (f", {excluded} excluded" if excluded else ""))


# ----------------------------------------------------------------------------
# stage 2: fetch (recipe-driven media download)
# ----------------------------------------------------------------------------

def pool_index(style, system):
    """{stem: path} for a pool directory. Listed, never globbed: No-Intro
    names contain glob metacharacters ("[b]")."""
    pool = Path("work/pool") / style / system
    if not pool.is_dir():
        return {}
    return {path.stem: path for path in pool.iterdir() if path.is_file()}


def key_region(name):
    """The region the dump name states, '' when it states none."""
    low = name.lower()
    for code, words in REGION_WORDS.items():
        for word in words:
            if f"({word}" in low or f", {word}" in low or f" {word})" in low:
                return code
    return ""


def pick_media(medias, style, regions, prefer=""):
    """First media of the given type: the dump's own region first, then the
    region chain, then any. Without 'prefer' every key of a game got the same
    image while a real regional cover sat unused in the same SS entry."""
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


def with_auth(url, creds, softname):
    """Re-attach credentials at request time; stored replies have none."""
    url = strip_auth(url)
    extra = "&".join(f"{k}={quote(v, safe='')}" for k, v in (
        ("devid", creds["SS_DEVID"]), ("devpassword", creds["SS_DEVPASSWORD"]),
        ("ssid", creds["SS_SSID"]), ("sspassword", creds["SS_SSPASSWORD"]),
        ("softname", softname)))
    sep = "&" if "?" in url else "?"
    return url + sep + extra


# libretro-thumbnails: one repository per system, files named after the exact
# No-Intro/Redump dump, so a key resolves without any fuzzy matching
LIBRETRO_SOURCES = {"lr-snaps": "Named_Snaps", "lr-titles": "Named_Titles",
                    "lr-boxarts": "Named_Boxarts"}
LIBRETRO_GIT = "https://github.com/libretro-thumbnails/{repo}.git"
LIBRETRO_RAW = "https://raw.githubusercontent.com/libretro-thumbnails/{repo}/master/{path}"
LIBRETRO_BAD_CHARS = set('&*/:`<>?\\|"')
# raw.githubusercontent has no per-account limit like ScreenScraper's; kept
# low so thousands of requests in a row are not throttled
LIBRETRO_THREADS = 4
# a symlink to one of these is another product, not another dump
LIBRETRO_NOT_SAME = re.compile(r"\((demo|proto|beta|sample|kiosk)", re.IGNORECASE)


def libretro_name(name):
    return "".join("_" if c in LIBRETRO_BAD_CHARS else c for c in name)


def libretro_listing(repo, folder):
    """Stems of the PNGs in one folder. A blob-less clone lists every file for
    a few MB and no API quota; delete work/libretro/<repo> to refresh it."""
    clone = Path("work/libretro") / repo
    try:
        if not (clone / ".git").is_dir():
            clone.parent.mkdir(parents=True, exist_ok=True)
            log(f"[fetch] cloning the {repo} listing (no images)")
            subprocess.run(["git", "clone", "-q", "--filter=blob:none",
                            "--no-checkout", "--depth", "1",
                            LIBRETRO_GIT.format(repo=repo), str(clone)],
                           check=True)
        # -z: no path quoting, so non-ASCII names come through intact
        raw = subprocess.run(["git", "-C", str(clone), "ls-tree", "-z",
                              "--name-only", "HEAD", folder + "/"],
                             capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        die(f"libretro listing of {repo} failed ({exc}); the lr-* sources "
            "need git on the PATH")
    return {path.rsplit("/", 1)[-1][:-4]
            for path in raw.decode("utf-8").split("\0")
            if path.lower().endswith(".png")}


def libretro_candidates(meta_dir):
    """{key: [names to try]}: the key, then every dump it stands for."""
    names = {}
    members = meta_dir / "_members.tsv"
    if members.is_file():
        for line in members.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#"):
                parts = line.split("\t")
                names.setdefault(parts[3], []).append(parts[0])
    return {key: [key] + sorted(n for n in dumps if n != key)
            for key, dumps in names.items()}


def libretro_get(repo, folder, stem, softname):
    """PNG bytes of one libretro file, following a symlink only to another
    dump of the same product: they sometimes point at a different game."""
    path = f"{folder}/{stem}.png"
    body = http_get(LIBRETRO_RAW.format(repo=repo, path=quote(path)), softname,
                    timeout=90)
    if body.startswith(b"\x89PNG"):
        return body
    # raw.githubusercontent serves a symlink as the path it points to
    target = posixpath.normpath(posixpath.join(
        folder, body.decode("utf-8", errors="replace").strip()))
    target_stem = posixpath.basename(target)[:-4]
    if (norm_title(target_stem) != norm_title(stem)
            or LIBRETRO_NOT_SAME.search(target_stem)):
        return None
    body = http_get(LIBRETRO_RAW.format(repo=repo, path=quote(target)),
                    softname, timeout=90)
    return body if body.startswith(b"\x89PNG") else None


def stage_fetch(scope, creds, only_system=None, like=None):
    for system, cfg in scope.systems.items():
        if only_system and system != only_system:
            continue
        meta_dir = Path("work/meta") / system
        if not meta_dir.is_dir():
            log(f"[fetch] {system}: no meta yet, run identify first")
            continue
        recipe = scope.recipe_for(system)
        metas = [m for m in sorted(meta_dir.glob("*.json"))
                 if (system, m.stem) not in scope.excludes]
        log(f"[fetch] {system}: {len(metas)} identified games, "
            f"recipe {' > '.join(recipe)}")

        listings, lr_names = {}, {}
        for source in recipe:
            if source not in LIBRETRO_SOURCES:
                continue
            if not cfg["libretro"]:
                log(f"[fetch] {system}: no libretro repo in scope.ini, "
                    f"{source} skipped")
                continue
            listings[source] = libretro_listing(cfg["libretro"],
                                                LIBRETRO_SOURCES[source])
            lr_names = lr_names or libretro_candidates(meta_dir)

        def libretro_stem(source, key):
            for name in lr_names.get(key, [key]):
                if libretro_name(name) in listings.get(source, {}):
                    return libretro_name(name)
            return None

        box = scope.native_max_for(system)

        def can_try(source, meta_path):
            """Whether a source may hold a PNG for this key, known without a
            request; a JPEG never replaces a PNG already pooled."""
            if source in LIBRETRO_SOURCES:
                return bool(libretro_stem(source, meta_path.stem))
            jeu = json.loads(meta_path.read_text(encoding="utf-8"))
            media = pick_media(jeu.get("medias") or [], source, scope.regions,
                               key_region(meta_path.stem))
            return bool(media) and (media.get("format") or "jpg").lower() == "png"

        # the reference's unified siblings and dropped placeholders would
        # cost a request each and never reach a pack
        wanted = reference_pack(like, system)[0] if like else None
        cached = unwanted = 0
        pools = {style: pool_index(style, system) for style in recipe}
        todo = []
        # keys that can only be completed from libretro: no ScreenScraper
        # request, so no account pacing
        lr_todo = []
        for meta_path in metas:
            key = meta_path.stem
            if wanted is not None and key not in wanted:
                unwanted += 1
                continue
            # a refused file stays in the pool; the next source is due
            held = [i for i, style in enumerate(recipe)
                    if pools[style].get(key)
                    and (style, system, key) not in scope.media_rejects]
            if not held:
                todo.append((meta_path, 0))
                continue
            # an earlier source missing from the pool was tried and failed
            start = held[0] + 1
            later = [style for style in recipe[start:]
                     if not pools[style].get(key)
                     and (style, system, key) not in scope.media_rejects
                     and box and can_try(style, meta_path)]
            if later and not any(fits_native(pools[recipe[i]][key], box)
                                 for i in held):
                (lr_todo if all(s in LIBRETRO_SOURCES for s in later)
                 else todo).append((meta_path, start))
            else:
                cached += 1

        saved = {}

        def save(source, key, ext, body):
            pool = Path("work/pool") / source / system
            pool.mkdir(parents=True, exist_ok=True)
            path = pool / f"{key}.{ext}"
            path.write_bytes(body)
            saved[(source, key)] = path
            return "got"

        def download(source, key, media):
            try:
                body = http_get(with_auth(media["url"], creds, scope.softname),
                                scope.softname, timeout=90)
            except HTTPError:
                return "none"
            if body.strip() == b"NOMEDIA":
                return "stale"
            if len(body) < 1024 and b"JFIF" not in body and b"PNG" not in body:
                check_quota_wall(body.decode("utf-8", errors="replace"))
                return "none"
            return save(source, key, (media.get("format") or "jpg").lower(), body)

        def refresh_medias(meta_path, jeu):
            """The fiche's media list as SS holds it now, saved over the
            stored one. Names and texts stay as identified: packs already
            published must not change under a fetch."""
            params = {"devid": creds["SS_DEVID"],
                      "devpassword": creds["SS_DEVPASSWORD"],
                      "ssid": creds["SS_SSID"], "sspassword": creds["SS_SSPASSWORD"],
                      "softname": scope.softname, "output": "json",
                      "gameid": jeu.get("id")}
            try:
                text = http_get(build_url("jeuInfos.php", params),
                                scope.softname).decode("utf-8", errors="replace")
            except HTTPError:
                return None
            try:
                fresh = json.loads(text)["response"]["jeu"]
            except (ValueError, KeyError, TypeError):
                fresh = json_after_noise(text)
            if not isinstance(fresh, dict) or str(fresh.get("id")) != str(jeu.get("id")):
                check_quota_wall(text)
                return None
            jeu["medias"] = redact_json(fresh.get("medias") or [])
            meta_path.write_text(json.dumps(jeu, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
            return jeu["medias"]

        def get_ss(source, meta_path, jeu, media):
            key = meta_path.stem
            outcome = download(source, key, media)
            if outcome != "stale":
                return outcome
            # SS moves media between regions (wor -> jp, same file); the old
            # URL then answers NOMEDIA while the fiche lists it elsewhere
            medias = refresh_medias(meta_path, jeu)
            again = medias and pick_media(medias, source, scope.regions,
                                          key_region(key))
            if again and strip_auth(again["url"]) != strip_auth(media["url"]):
                outcome = download(source, key, again)
            return "got" if outcome == "got" else "none"

        def fetch_one(task):
            """'got' or 'none' for a key with nothing in the pool; for one
            whose pooled images are all past the native box, 'native' when a
            later source has the frame at native size, 'kept' when not."""
            meta_path, start = task
            held = start > 0
            key = meta_path.stem
            jeu = json.loads(meta_path.read_text(encoding="utf-8"))
            medias = jeu.get("medias") or []
            lossy = None
            got = False
            for source in recipe[start:]:
                if ((source, system, key) in scope.media_rejects
                        or pools[source].get(key)):
                    continue
                if source in LIBRETRO_SOURCES:
                    stem = libretro_stem(source, key)
                    if not stem:
                        continue
                    try:
                        body = libretro_get(cfg["libretro"],
                                            LIBRETRO_SOURCES[source], stem,
                                            scope.softname)
                    except HTTPError:
                        body = None
                    if body:
                        save(source, key, "png", body)
                        if fits_native(saved[(source, key)], box):
                            return "native" if held else "got"
                        got = True
                    continue
                media = pick_media(medias, source, scope.regions,
                                   key_region(key))
                if not media:
                    continue
                # a PNG pack gains nothing from a JPEG it can never make
                # lossless, so a later lossless source goes first
                if (scope.image_ext == "png" and lossy is None
                        and (media.get("format") or "jpg").lower() != "png"):
                    lossy = (source, media)
                    continue
                outcome = get_ss(source, meta_path, jeu, media)
                # SS lists some media it no longer serves; a screenshot pack
                # tries its next source, the closed box styles keep their
                # recipe as built
                if scope.image_ext != "png":
                    return outcome
                if outcome == "got":
                    if fits_native(saved[(source, key)], box):
                        return "native" if held else "got"
                    got = True
            if held:
                return "kept"
            if got:
                return "got"
            if lossy:
                return get_ss(lossy[0], meta_path, jeu, lossy[1])
            return "none"

        outcomes = run_parallel(todo, fetch_one, scope.threads, scope.delay,
                                "downloaded", len(todo))
        outcomes += run_parallel(lr_todo, fetch_one, LIBRETRO_THREADS, 0,
                                 "checked in libretro", len(lr_todo))
        got = outcomes.count("got")
        none = outcomes.count("none")
        native = outcomes.count("native")
        checked = native + outcomes.count("kept")

        log(f"[fetch] {system}: {got} downloaded, {cached} cached, {none} without media"
            + (f", {native} of {checked} past {box[0]}x{box[1]} found at "
               "native size in a later source" if checked else "")
            + (f", {unwanted} not in {like}" if like else ""))


# ----------------------------------------------------------------------------
# stage 3: assemble (normalize + gameinfo.tsv + manifest.csv)
# ----------------------------------------------------------------------------

def preferred(items, field, order, textkey="text"):
    """Pick items[i][textkey] whose items[i][field] follows the order list."""
    if not items:
        return ""
    for want in order:
        for it in items:
            if it.get(field) == want and it.get(textkey):
                return it[textkey]
    return items[0].get(textkey, "")


def game_info_row(jeu, scope):
    name = preferred(jeu.get("noms") or [], "region", scope.regions) or jeu.get("nom", "")
    date = preferred(jeu.get("dates") or [], "region", scope.regions)
    year = date[:4] if date else ""
    genres = []
    for g in jeu.get("genres") or []:
        # genre lives in the language-neutral core file: keep it in English
        genres.append(preferred(g.get("noms") or [], "langue", ["en"]))
    developer = (jeu.get("developpeur") or {}).get("text", "")
    players = (jeu.get("joueurs") or {}).get("text", "")
    # every language SS provides, unless scope.langs restricts the set
    synopsis = {}
    for s in jeu.get("synopsis") or []:
        lang, text = s.get("langue", ""), clean_text(s.get("text", ""))
        if lang and text and (scope.langs is None or lang in scope.langs):
            synopsis[lang] = text
    return name, year, "/".join(x for x in genres if x), developer, players, synopsis


MANIFEST_FIELDS = ["system", "key", "ss_id", "ss_system_id", "ss_system",
                   "style", "region", "md5", "size", "width", "height",
                   "encoding"]


def load_names(path=Path("names.tsv")):
    """(system, key) -> display name from names.tsv. SS sometimes holds junk
    in the 'wor' name (a sequel's title, a ROM hack); the game and its box
    are right, only the printed name is wrong."""
    table = {}
    if not Path(path).is_file():
        return table
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            table[(parts[0], parts[1])] = parts[2].strip()
        elif len(parts) == 2:
            table[(None, parts[0])] = parts[1].strip()
    return table


def load_media_rejects(path=Path("media_rejects.tsv")):
    """{(media, system, key)} from media_rejects.tsv: medias reviewed as
    unusable for one game. Not the same as an excluded key, which leaves
    the pack entirely: here only this media is refused."""
    table = set()
    if not Path(path).is_file():
        return table
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            table.add((parts[0], parts[1], parts[2]))
    return table


def load_image_aliases(path=Path("image_aliases.tsv")):
    """(style, system, key) -> winner from image_aliases.tsv: keys reviewed
    as showing the same image as another key, which they then share through
    index.tsv. unify_siblings only merges keys of one fiche with the same
    bytes; a visual duplicate across fiches needs a person to see it."""
    table = {}
    if not Path(path).is_file():
        return table
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 4 and parts[3].strip():
            table[(parts[0], parts[1], parts[2])] = parts[3].strip()
    return table


def resolve_alias(alias_of, key):
    """The key whose image a key ends up showing, through any chain."""
    seen = {key}
    while key in alias_of:
        key = alias_of[key]
        if key in seen:
            break
        seen.add(key)
    return key


def load_rotations(path=Path("rotations.tsv")):
    """(media, system, key) -> degrees clockwise from rotations.tsv. Some SS
    scans of landscape boxes are stored sideways; turning them here keeps
    the fix through re-fetches, which the pool would lose."""
    table = {}
    if not Path(path).is_file():
        return table
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 4:
            try:
                table[(parts[0], parts[1], parts[2])] = int(parts[3])
            except ValueError:
                die(f"rotations.tsv: bad degrees in '{line}'")
    return table


# SS files everything that is not a game under one catch-all entry with this
# name prefix. Rejecting by marker is exact; the placeholder threshold is luck.
NONGAME_MARKER = "zzz(notgame)"


def is_nongame(jeu):
    """True when SS answered with its catch-all non-game entry."""
    names = [n.get("text", "") for n in (jeu.get("noms") or [])
             if isinstance(n, dict)]
    names.append(jeu.get("nom", "") or "")
    return any(n.lower().startswith(NONGAME_MARKER) for n in names if n)


def ss_subsystem(jeu):
    """(id, name) of the system SS resolved the game to; under Arcade this
    is a manufacturer subsystem, not the queried id."""
    sysinfo = jeu.get("systeme") or {}
    if not isinstance(sysinfo, dict):
        return "", ""
    name = sysinfo.get("text") or ""
    if not name:
        noms = sysinfo.get("noms") or {}
        if isinstance(noms, dict):
            name = noms.get("nom_eu") or noms.get("nom_us") or ""
    return str(sysinfo.get("id", "") or ""), clean_text(name)


def load_manifest(path, fields):
    """Read an existing manifest, padding rows from older builds so a new
    column never breaks a resumed run."""
    rows = {}
    if path.is_file():
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rows[(row["system"], row["key"])] = {
                    name: row.get(name, "") or "" for name in fields}
    return rows


def ss_game_id(meta_dir, key):
    """The ScreenScraper game id behind a key, '' when unreadable."""
    path = meta_dir / (key + ".json")
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("id", ""))
    except Exception:
        return ""


def find_placeholders(scope, system, threshold):
    """MD5s of empty templates: SS renders the mix composite even with no
    art, so a byte-identical image repeated across games is a placeholder.

    Counted per distinct SS entry, not per key: a game retitled per region
    forms several keys that legitimately share one box, and counting keys
    discarded real covers.

    Screenshot styles compare pixels after the native reduction: one generic
    screen reaches the pool as several files (doubled, palette or RGB) that
    the pack would write as the same image. Keyed by file md5 all the same,
    which is how callers look a source up."""
    if threshold <= 0:
        return {}
    meta_dir = Path("work/meta") / system
    by_hash = {}
    for style in scope.recipe_for(system):
        pool = Path("work/pool") / style / system
        if not pool.is_dir():
            continue
        for path in pool.iterdir():
            if path.is_file():
                h = (picture_hash(path) if scope.image_ext == "png"
                     else md5_file(path))
                by_hash.setdefault(h, []).append(path)

    out = {}
    for paths in by_hash.values():
        keys = [p.stem for p in paths]
        if len(keys) < threshold:
            continue
        ids = {ss_game_id(meta_dir, k) for k in keys}
        ids.discard("")
        # unreadable metadata: fall back to the key count
        if ids and len(ids) < threshold:
            continue
        for p in paths:
            out[md5_file(p)] = keys
    return out


def picture_hash(path):
    """md5 of the pixels a PNG pack would write for a source; the file md5
    when it does not decode, so a broken file still groups with itself."""
    from PIL import Image
    try:
        with Image.open(path) as img:
            img = exact_reduce(flatten(img))
            return hashlib.md5(b"%dx%d:" % img.size + img.tobytes()).hexdigest()
    except Exception:
        return md5_file(path)


def flatten(img):
    """RGB over black, as a PNG pack writes it."""
    from PIL import Image
    if img.mode in ("RGBA", "LA", "P") or "transparency" in img.info:
        img = img.convert("RGBA")
        flat = Image.new("RGB", img.size, (0, 0, 0))
        flat.paste(img, mask=img.split()[-1])
        return flat
    return img.convert("RGB")


def fits_native(path, box):
    """Whether a source is within the system's native box once an exact
    enlargement is undone. A file that does not decode counts as native, so
    assemble still picks it and drops it from the pool for the next fetch."""
    from PIL import Image
    if box is None:
        return True
    try:
        with Image.open(path) as img:
            w, h = img.size
            if w > box[0] or h > box[1]:
                w, h = exact_reduce(flatten(img)).size
    except Exception:
        return True
    return w <= box[0] and h <= box[1]


def read_members(meta_dir):
    """Keys currently in scope, as written by identify."""
    path = meta_dir / "_members.tsv"
    if not path.is_file():
        return None
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            keys.add(line.split("\t")[3])
    return keys


# The card allocates in 128 KB blocks, so a file costs its size rounded up.
# Per-style quality is the highest that fits one block; an image that spills
# into a second block is compressed a little harder until it fits. Measured:
# 70% fit after one 5-point step, and a floor of 75 recovers 444 of 480 MB.
ALLOC_BLOCK = 128 * 1024
QUALITY_FLOOR = 75


def save_within_block(img, target, quality, block=ALLOC_BLOCK,
                      floor=QUALITY_FLOOR):
    """Save a JPEG, stepping quality down until it fits one block. Stops at
    the floor: a few detailed covers legitimately need two."""
    img.save(target, format="JPEG", quality=quality, optimize=True,
             progressive=False)
    while target.stat().st_size > block and quality > floor:
        quality -= 5
        img.save(target, format="JPEG", quality=quality, optimize=True,
                 progressive=False)
    return quality


def exact_reduce(img):
    """Undo a nearest-neighbour upscale by one factor on both axes, only when
    upscaling back reproduces every pixel: the result is the native frame,
    and nothing is resampled. Both axes together, or the aspect would change
    for any consumer that shows pixels 1:1."""
    from PIL import Image, ImageChops
    w, h = img.size
    for k in (4, 3, 2):
        if w % k or h % k:
            continue
        small = img.resize((w // k, h // k), Image.NEAREST)
        if ImageChops.difference(small.resize((w, h), Image.NEAREST),
                                 img).getbbox() is None:
            return small
    return img


def save_png(img, target, lossless, block=ALLOC_BLOCK):
    """Save a screenshot as PNG and say how: 'lossless', or '256colors' when
    the lossless file would spill past one card block. A palette is used
    only when it reproduces every pixel."""
    from PIL import Image, ImageChops
    img = img.copy()
    img.info.clear()
    out = img
    colours = img.getcolors(256)
    if colours:
        pal = img.convert("P", palette=Image.ADAPTIVE, colors=len(colours))
        if ImageChops.difference(pal.convert("RGB"), img).getbbox() is None:
            out = pal
    out.save(target, format="PNG", optimize=True)
    if lossless or target.stat().st_size <= block:
        return "lossless"
    img.quantize(256).save(target, format="PNG", optimize=True)
    return "256colors"


def reference_pack(like, system):
    """(keys, alias_of, styles, art_dir) of the pack this build mirrors: the
    keys carrying an image in its manifest, the index remaps it published,
    the style each image came from, and where its images are.

    Every style of a system serves the same keys through the same index, so
    switching styles never loses a game; a key this style cannot serve
    borrows the reference image, and manifest.tsv says so.
    """
    manifest = load_manifest(Path(f"out/manifest-{like}.csv"), MANIFEST_FIELDS)
    styles = {k: row["style"] for (s, k), row in manifest.items()
              if s == system}
    if not styles:
        die(f"--like {like}: no manifest rows for {system}; "
            f"assemble {like} first")
    art = Path(f"out/media-{like}/docs") / system / "Artwork"

    def key_by_name(path):
        table = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#"):
                name, _, _, key = line.split("\t")
                table[name] = key
        return table

    alias_of = {}
    members_path = Path("work/meta") / system / "_members.tsv"
    if members_path.is_file():
        if not (art / "index.tsv").is_file():
            die(f"--like {like}: {art / 'index.tsv'} missing; "
                f"assemble {like} first")
        published = key_by_name(art / "index.tsv")
        for name, key in key_by_name(members_path).items():
            if name in published and published[name] != key:
                alias_of[key] = published[name]
    return set(styles), alias_of, styles, art


def unify_siblings(meta_dir, pools, placeholders, in_scope, scope):
    """{loser key: winner key} for keys of one system that share a fiche AND
    a byte-identical source image.

    The region preference in pick_media() already splits siblings when
    ScreenScraper holds a cover per region; where it holds one, every dump of
    the game got its own copy of it. Same fiche and same bytes mean the same
    box for the same game, so one file suffices and the rest become index
    rows. Grouped by the SOURCE md5, already computed for the placeholder
    check, so losers are never encoded at all.
    """
    groups = {}
    for meta_path in sorted(meta_dir.glob("*.json")):
        key = meta_path.stem
        if in_scope is not None and key not in in_scope:
            continue
        if (meta_dir.name, key) in scope.excludes:
            continue
        src = None
        for style in scope.recipe_for(meta_dir.name):
            hit = pools[style].get(key)
            if hit and md5_file(hit) not in placeholders:
                src = hit
                break
        fiche = ss_game_id(meta_dir, key)
        if src and fiche:
            groups.setdefault((fiche, md5_file(src)), []).append(key)
    alias_of = {}
    for keys in groups.values():
        if len(keys) < 2:
            continue
        winner = min(keys, key=lambda k: elect_key({"key": k}, scope.regions))
        for k in keys:
            if k != winner:
                alias_of[k] = winner
    return alias_of


def stage_assemble(scope, only_system=None, prune=False, like=None):
    from PIL import Image

    forced_names = load_names()
    if forced_names:
        log(f"[assemble] {len(forced_names)} forced name(s) loaded")
    rotations = load_rotations()
    if rotations:
        log(f"[assemble] {len(rotations)} rotation(s) loaded")

    # per style: rows are keyed by (system, key)
    manifest_path = Path(f"out/manifest-{scope.style_label}.csv")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(manifest_path, MANIFEST_FIELDS)

    for system, cfg in scope.systems.items():
        if only_system and system != only_system:
            continue
        meta_dir = Path("work/meta") / system
        if not meta_dir.is_dir():
            continue
        out_dir = (Path(f"out/media-{scope.style_label}/docs")
                   / system / scope.folder)
        out_dir.mkdir(parents=True, exist_ok=True)
        ext = "." + scope.image_ext
        png = scope.image_ext == "png"

        # keys no longer in _members.tsv left the scope
        in_scope = read_members(meta_dir)
        recipe = scope.recipe_for(system)
        box = scope.native_max_for(system)
        pools = {style: pool_index(style, system) for style in recipe}
        placeholders = find_placeholders(scope, system,
                                         scope.placeholder_min_for(system))
        if placeholders:
            total = len({k for keys in placeholders.values() for k in keys})
            log(f"[assemble] {system}: {len(placeholders)} placeholder "
                f"image(s) detected, affecting {total} games")
        rows = []
        syn_rows = {}  # lang -> [(key, text)]
        pack_rows = []  # (key, style, ss_system_id, encoding) for manifest.tsv
        made = skipped = stale = blanks = broken = excluded = refused = 0
        to_native = 0
        ref_keys = ref_art = None
        borrowed = set()
        if like:
            ref_keys, alias_of, ref_styles, ref_art = reference_pack(like, system)
        else:
            alias_of = unify_siblings(meta_dir, pools, placeholders, in_scope, scope)
            for (style, sys_name, key), winner in scope.image_aliases.items():
                if (style == scope.style_label and sys_name == system
                        and key != winner):
                    alias_of[key] = winner
            # a reviewed winner may itself have been unified into a sibling
            alias_of = {k: resolve_alias(alias_of, k) for k in alias_of}
        for meta_path in sorted(meta_dir.glob("*.json")):
            key = meta_path.stem
            if in_scope is not None and key not in in_scope:
                stale += 1
                continue
            if (system, key) in scope.excludes:
                # metadata may linger from before the exclusion
                (out_dir / (key + ext)).unlink(missing_ok=True)
                manifest.pop((system, key), None)
                excluded += 1
                continue
            jeu = json.loads(meta_path.read_text(encoding="utf-8"))
            name, year, genre, dev, players, synopsis = game_info_row(jeu, scope)
            name = (forced_names.get((system, key))
                    or forced_names.get((None, key)) or name)
            rows.append([key, name, year, genre, dev, players])
            for lang, text in synopsis.items():
                syn_rows.setdefault(lang, []).append((key, text))

            src = style_used = None
            dropped = False
            enlarged = None
            for style in recipe:
                hit = pools[style].get(key)
                if hit:
                    if (style, system, key) in scope.media_rejects:
                        refused += 1
                        dropped = True
                        continue
                    # an empty template is worse than no image at all
                    if md5_file(hit) in placeholders:
                        blanks += 1
                        dropped = True
                        continue
                    if not fits_native(hit, box):
                        # kept unless a later source is native
                        enlarged = enlarged or (hit, style)
                        continue
                    src, style_used = hit, style
                    break
            native_won = bool(enlarged and src)
            if enlarged and not src:
                src, style_used = enlarged
            if key in alias_of:
                # a stale file from a build before unification must not stay
                (out_dir / (key + ext)).unlink(missing_ok=True)
                manifest.pop((system, key), None)
                continue
            if ref_keys is not None:
                if key not in ref_keys:
                    continue
                if not src:
                    src, style_used = ref_art / (key + ".jpg"), ref_styles[key]
                    borrowed.add(key)
            if not src:
                if dropped:
                    # an image built before the refusal must not stay
                    (out_dir / (key + ext)).unlink(missing_ok=True)
                    manifest.pop((system, key), None)
                continue

            to_native += native_won
            target = out_dir / (key + ext)
            built = manifest.get((system, key)) or {}
            if target.is_file() and built.get("style", style_used) != style_used:
                # built from another source before a refusal or a native one
                # took over
                target.unlink()
            encoding = built.get("encoding", "")
            if target.is_file():
                skipped += 1
            elif key in borrowed:
                # already normalised; a second JPEG pass would only lose quality
                target.write_bytes(src.read_bytes())
                made += 1
            else:
                try:
                    with Image.open(src) as img:
                        # SS serves box-3D as RGBA over black: composite,
                        # or Pillow fills the alpha white
                        # a screenshot may carry its transparent colour in
                        # info instead of an alpha band; left there, it
                        # trips the palette conversion
                        if img.mode in ("RGBA", "LA", "P") or (
                                png and "transparency" in img.info):
                            img = img.convert("RGBA")
                            flat = Image.new("RGB", img.size, (0, 0, 0))
                            flat.paste(img, mask=img.split()[-1])
                            img = flat
                        else:
                            img = img.convert("RGB")
                        turn = rotations.get((style_used, system, key))
                        if turn:
                            # Pillow turns counter-clockwise; the table is clockwise
                            img = img.rotate(-turn, expand=True)
                        if png:
                            # never resampled: native pixels are the point
                            encoding = save_png(
                                exact_reduce(img), target,
                                scope.lossless_for(system))
                        else:
                            w, h = img.size
                            if max(w, h) > scope.max_px:
                                ratio = scope.max_px / max(w, h)
                                img = img.resize((round(w * ratio), round(h * ratio)),
                                                 Image.LANCZOS)
                            save_within_block(img, target, scope.quality)
                except Exception as exc:
                    # drop it from the pool so the next fetch retries, and any
                    # half-written output, which the next run would keep
                    log(f"[assemble] {system}: unreadable {src.name} ({exc}); "
                        "removed from pool, will be re-fetched")
                    src.unlink(missing_ok=True)
                    target.unlink(missing_ok=True)
                    broken += 1
                    continue
                made += 1

            with Image.open(target) as done_img:
                width, height = done_img.size
            sys_id, sys_name = ss_subsystem(jeu)
            manifest[(system, key)] = {
                "system": system, "key": key, "ss_id": str(jeu.get("id", "")),
                "ss_system_id": sys_id, "ss_system": sys_name,
                "style": style_used, "region": "",
                "md5": md5_file(target), "size": str(target.stat().st_size),
                "width": str(width), "height": str(height),
                "encoding": encoding if png else "",
            }
            pack_rows.append((key, style_used, sys_id, encoding))

        # lets a consumer filter by style and see which SS system each
        # image came from
        with open(out_dir / "manifest.tsv", "w", encoding="utf-8",
                  newline="") as f:
            # PNG packs also say which images lost colours to fit a block
            f.write("#key\tstyle\tss_system_id" + ("\tencoding" if png else "")
                    + "\n")
            for key, style_used, sys_id, encoding in sorted(pack_rows):
                f.write(f"{key}\t{style_used}\t{sys_id}"
                        + (f"\t{encoding}" if png else "") + "\n")

        info_path = out_dir / "gameinfo.tsv"
        with open(info_path, "w", encoding="utf-8", newline="") as f:
            f.write("#key\tname\tyear\tgenre\tdeveloper\tplayers\n")
            for row in rows:
                f.write("\t".join(clean_text(c) for c in row) + "\n")

        # regenerate wholesale so a narrowed language set leaves no strays
        for old in out_dir.glob("synopsis_*.tsv"):
            old.unlink()
        for lang in sorted(syn_rows):
            with open(out_dir / f"synopsis_{lang}.tsv", "w",
                      encoding="utf-8", newline="") as f:
                f.write("#key\tsynopsis\n")
                for key, text in syn_rows[lang]:
                    f.write(f"{key}\t{text}\n")

        # images left over from a previous, wider scope; before the index,
        # which must not list a file this run deletes
        if in_scope is not None:
            # a manifest row exists iff its image does: an excluded key or a
            # dropped placeholder stays in scope but has no file
            for row in [r for r in manifest
                        if r[0] == system
                        and (r[1] not in in_scope
                             or (ref_keys is not None and r[1] not in ref_keys)
                             or not (out_dir / (r[1] + ext)).is_file())]:
                manifest.pop(row, None)
            orphans = [p for p in out_dir.glob("*" + ext)
                       if p.stem not in in_scope
                       or (ref_keys is not None and p.stem not in ref_keys)]
            if prune:
                for orphan in orphans:
                    orphan.unlink()
            if orphans:
                names = ", ".join(sorted(p.stem for p in orphans)[:5])
                log(f"[assemble] {system}: {len(orphans)} image(s) out of "
                    f"scope ({names}{'...' if len(orphans) > 5 else ''}) "
                    + ("deleted" if prune else
                       "- run with --prune to delete them"))

        # a reviewed alias whose winner has no image leaves its key with none
        if not like:
            lost = sorted(k for (st, sy, k) in scope.image_aliases
                          if st == scope.style_label and sy == system
                          and k in alias_of
                          and not (out_dir / (alias_of[k] + ext)).is_file())
            if lost:
                log(f"[assemble] {system}: {len(lost)} image_aliases.tsv "
                    f"key(s) point to a key with no image: {', '.join(lost[:5])}"
                    + ("..." if len(lost) > 5 else ""))

        # every dump (name + crc/size) -> the image actually in the pack
        members_path = meta_dir / "_members.tsv"
        indexed = 0
        if members_path.is_file():
            with open(out_dir / "index.tsv", "w", encoding="utf-8",
                      newline="") as f:
                f.write("#name\tcrc\tsize\tkey\n")
                for line in members_path.read_text(encoding="utf-8").splitlines():
                    if not line or line.startswith("#"):
                        continue
                    name, crc, size, key = line.split("\t")
                    key = alias_of.get(key, key)
                    if (out_dir / (key + ext)).is_file():
                        f.write(f"{name}\t{crc}\t{size}\t{key}\n")
                        indexed += 1

        lang_list = ", ".join(sorted(syn_rows)) or "none"
        log(f"[assemble] {system}: {made} images written, {skipped} kept, "
            f"gameinfo.tsv with {len(rows)} rows, "
            f"synopsis in {len(syn_rows)} languages ({lang_list}), "
            f"index.tsv with {indexed} variants, "
            f"manifest.tsv with {len(pack_rows)} rows"
            + (f", {stale} meta out of scope" if stale else "")
            + (f", {refused} media refused" if refused else "")
            + (f", {to_native} taken at native size from a later source"
               if to_native else "")
            + (f", {excluded} excluded" if excluded else "")
            + (f", {len(alias_of)} key(s) unified into a sibling's image"
               if alias_of else "")
            + (f", {len(borrowed)} borrowed from {like}" if borrowed else "")
            + (f", {blanks} placeholder(s) discarded" if blanks else "")
            + (f", {broken} unreadable" if broken else ""))

    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        for row_key in sorted(manifest):
            writer.writerow(manifest[row_key])
    log(f"[assemble] manifest.csv: {len(manifest)} rows total")


# ----------------------------------------------------------------------------
# stage 4: package (Downloader db per system)
# ----------------------------------------------------------------------------

def stage_package(scope, only_system=None):
    """One Downloader database per system. "pext" allows USB installs,
    "base_files_url" replaces per-file urls, "tags" drive downloader.ini
    filters."""
    if not scope.url_base or not scope.db_id_prefix:
        die("scope.ini: [pack] url_base and db_id_prefix are required for package")
    # a single-repo url_base would point at the wrong repo and fail only
    # at install time
    for token in ("{group}", "{style}"):
        if token not in scope.url_base:
            die(f"scope.ini: [pack] url_base must contain {token}")
    db_dir = Path("out/db")
    db_dir.mkdir(parents=True, exist_ok=True)
    snippets = []

    for system in scope.systems:
        if only_system and system != only_system:
            continue
        media_dir = (Path(f"out/media-{scope.style_label}/docs")
                     / system / scope.folder)
        if not media_dir.is_dir():
            continue
        install_root = Path("docs") / system / scope.folder
        folder_lc = scope.folder.lower()

        tag_dictionary, tag_ids = {}, []
        for name in ("docs", folder_lc, system.lower(),
                     f"{system.lower()}{folder_lc}"):
            if name not in tag_dictionary:
                tag_dictionary[name] = len(tag_dictionary)
            tag_ids.append(tag_dictionary[name])
        docs_tag = [tag_dictionary["docs"]]
        system_tags = sorted(set(tag_ids))

        files = {}
        for path in sorted(media_dir.iterdir()):
            if not path.is_file():
                continue
            rel = (install_root / path.name).as_posix()
            files[rel] = {
                "hash": md5_file(path),
                "size": path.stat().st_size,
                "path": "pext",
                "tags": system_tags,
            }
        folders = {
            "docs": {"path": "pext", "tags": docs_tag},
            f"docs/{system}": {"path": "pext", "tags": system_tags},
            f"docs/{system}/{scope.folder}": {"path": "pext", "tags": system_tags},
        }

        # box styles replace each other under one db_id; any other folder is
        # a pack of its own, installed next to them
        db_id = scope.db_id_prefix + system.lower() + (
            "" if scope.folder == "Artwork" else "-" + folder_lc)
        db = {
            "db_id": db_id,
            "timestamp": int(time.time()),
            "base_files_url": scope.url_base.format(
                group=scope.systems[system]["group"],
                style=scope.style_label) + "/",
            "files": files,
            "folders": folders,
            "tag_dictionary": tag_dictionary,
        }

        base = f"{system.lower()}_{scope.style_label}"
        json_path = db_dir / f"{base}.json"
        json_path.write_text(json.dumps(db, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        with zipfile.ZipFile(db_dir / f"{base}.json.zip", "w",
                             zipfile.ZIP_DEFLATED) as zf:
            zf.write(json_path, "db.json")

        total_mb = sum(f["size"] for f in files.values()) / 1e6
        zip_kb = (db_dir / f"{base}.json.zip").stat().st_size / 1024
        log(f"[package] {system}: {len(files)} files, {total_mb:.1f} MB "
            f"-> db/{base}.json.zip ({zip_kb:.0f} KB, db_id={db_id}, "
            f"tags={','.join(tag_dictionary)})")
        snippets.append((db_id, base, scope.systems[system]["group"]))

    if snippets:
        log("\n--- downloader.ini sections (append on the MiSTer) ---")
        for db_id, base, group in snippets:
            if scope.db_url_base:
                root = scope.db_url_base.format(
                    group=group, style=scope.style_label)
            else:
                root = "<RAW-URL-OF-YOUR-REPO-DB-BRANCH>"
            log(f"[{db_id}]")
            log(f"db_url = {root}/{base}.json.zip\n")


def cmd_resolve(scope, creds, only_system=None, limit=0):
    """Sweep candidate subsystems for rejected games; proposals go to
    overrides.suggested.tsv and nothing is applied."""
    systems = fetch_systems(scope, creds)
    out_lines, resolved, unresolved = [], 0, 0

    for system, cfg in scope.systems.items():
        if only_system and system != only_system:
            continue
        meta_dir = Path("work/meta") / system
        if not meta_dir.is_dir():
            continue
        stuck = [m for m in sorted(meta_dir.glob("*.miss"))
                 if m.read_text(encoding="utf-8").startswith("out-of-family")]
        if limit > 0:
            stuck = stuck[:limit]
        if not stuck:
            log(f"[resolve] {system}: nothing rejected")
            continue

        candidates = [sid for sid, info in systems.items()
                      if info["parent"] == cfg["ss_id"]
                      and sid not in cfg["reject"]]
        log(f"[resolve] {system}: {len(stuck)} rejected, "
            f"sweeping {len(candidates)} candidate subsystems")

        for marker in stuck:
            key = marker.stem
            entry = {"romnom": key + ".zip", "crc": "", "size": ""}
            found = None
            for sid in candidates:
                jeu, _ = query_game(scope, creds, sid, entry)
                time.sleep(scope.delay)
                if not jeu:
                    continue
                res_id, res_name = ss_subsystem(jeu)
                if in_family(systems, res_id, sid, cfg["accept"], cfg["reject"]):
                    found = (res_id, res_name, jeu)
                    break
            if found:
                res_id, res_name, jeu = found
                nom = preferred(jeu.get("noms") or [], "region", scope.regions)
                styles = sorted({m.get("type") for m in (jeu.get("medias") or [])
                                 if m.get("type", "").startswith("box")})
                out_lines.append(f"{system}\t{key}\t{res_id}\t"
                                 f"# {res_name} - {nom} - {','.join(styles) or 'no box'}")
                log(f"  {key} -> {res_id} ({res_name}) {nom}")
                resolved += 1
            else:
                log(f"  {key} -> no candidate subsystem resolved it")
                unresolved += 1

    if out_lines:
        path = Path("overrides.suggested.tsv")
        path.write_text("# system\tkey\tsystemeid\t# notes\n" +
                        "\n".join(out_lines) + "\n", encoding="utf-8")
        log(f"\n[resolve] {resolved} resolved, {unresolved} not. "
            f"Review {path} and merge the good lines into overrides.tsv, "
            f"then delete the matching .miss files and re-run identify.")
    else:
        log(f"[resolve] {resolved} resolved, {unresolved} not.")


# ----------------------------------------------------------------------------
# helper command: dump ScreenScraper system list
# ----------------------------------------------------------------------------

def cmd_systems(scope, creds):
    systems = fetch_systems(scope, creds)
    for sid in sorted(systems, key=lambda x: int(x) if x.isdigit() else 0):
        info = systems[sid]
        parent = info["parent"]
        suffix = ""
        if parent:
            suffix = f"   (child of {parent} {systems.get(parent, {}).get('name', '?')})"
        print(f"{sid:>5}  {info['name']}{suffix}")


def stage_verify(scope, only_system=None, sample=12):
    """Compare what is published against what was built locally."""
    if not scope.db_url_base:
        die("scope.ini: [pack] db_url_base is required for verify")

    db_dir = Path("out/db")
    media_root = Path(f"out/media-{scope.style_label}")
    problems = 0

    for system in scope.systems:
        if only_system and system != only_system:
            continue
        base = f"{system.lower()}_{scope.style_label}"
        local_zip = db_dir / f"{base}.json.zip"
        if not local_zip.is_file():
            log(f"[verify] {system}: no local {base}.json.zip, skipped")
            continue

        group = scope.systems[system]["group"]
        root = scope.db_url_base.format(group=group,
                                        style=scope.style_label)
        url = f"{root}/{base}.json.zip"
        try:
            published = http_get(url, scope.softname)
        except Exception as exc:
            log(f"[verify] {system}: FAIL cannot fetch {url} ({exc})")
            problems += 1
            continue

        local_bytes = local_zip.read_bytes()
        if hashlib.md5(published).hexdigest() != \
                hashlib.md5(local_bytes).hexdigest():
            log(f"[verify] {system}: FAIL published db.json.zip differs "
                "from local; republish it")
            problems += 1
            continue

        # the db matches, so sample the media it points at
        with zipfile.ZipFile(io.BytesIO(local_bytes)) as zf:
            db = json.loads(zf.read(zf.namelist()[0]))
        entries = sorted(db["files"].items())
        if not entries:
            log(f"[verify] {system}: db has no files")
            problems += 1
            continue
        step = max(1, len(entries) // sample)
        picks = entries[::step][:sample]

        bad = 0
        for rel, meta in picks:
            local_file = media_root / rel
            if not local_file.is_file():
                log(f"[verify] {system}: missing locally {rel}")
                bad += 1
                continue
            if hashlib.md5(local_file.read_bytes()).hexdigest() \
                    != meta["hash"]:
                log(f"[verify] {system}: local file does not match db "
                    f"{rel}")
                bad += 1
                continue
            media_url = db["base_files_url"] + quote(rel)
            try:
                blob = http_get(media_url, scope.softname)
            except Exception as exc:
                log(f"[verify] {system}: cannot fetch {rel} ({exc})")
                bad += 1
                continue
            if hashlib.md5(blob).hexdigest() != meta["hash"]:
                log(f"[verify] {system}: published media differs {rel}")
                bad += 1

        if bad:
            log(f"[verify] {system}: FAIL {bad}/{len(picks)} sampled "
                "files wrong")
            problems += 1
        else:
            log(f"[verify] {system}: OK db.json.zip and {len(picks)} "
                f"sampled files match ({len(entries)} in db)")

    if problems:
        die(f"verify: {problems} system(s) inconsistent")
    log("[verify] all checked systems consistent")


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="MiSTer Boxart Pack builder")
    parser.add_argument("stage", choices=["identify", "fetch", "assemble",
                                          "package", "all", "systems",
                                          "resolve", "verify"])
    parser.add_argument("--scope", default="scope.ini")
    parser.add_argument("--system", default=None,
                        help="restrict to one [system:<Name>] section")
    parser.add_argument("--style", default=None,
                        help="use a [style:<label>] recipe instead of "
                             "the [pack] defaults")
    parser.add_argument("--limit", type=int, default=0,
                        help="identify only the first N entries (smoke tests)")
    parser.add_argument("--prune", action="store_true",
                        help="delete assembled images that are no longer in "
                             "scope (assemble)")
    parser.add_argument("--retry-miss", action="store_true",
                        help="re-query entries previously marked .miss "
                             "(use after editing overrides.tsv)")
    parser.add_argument("--like", default=None, metavar="STYLE",
                        help="mirror another style's pack: same keys, same "
                             "index, its image where this style has none "
                             "(fetch, assemble)")
    args = parser.parse_args()

    scope = Scope(args.scope, args.style)
    if args.like:
        if args.like not in scope.styles:
            die(f"--like: unknown style '{args.like}'")
        if args.like == scope.style_label:
            die("--like must name a different style")
        if scope.image_ext != "jpg" or scope.folder != "Artwork":
            die("--like only mirrors box styles: a screenshot pack must "
                "never borrow a box image")
    needs_net = args.stage in ("identify", "fetch", "all", "systems",
                               "resolve")
    # verify only reads public raw URLs, so it needs no credentials
    creds = credentials() if needs_net else None

    if args.stage == "systems":
        cmd_systems(scope, creds)
        return
    if args.stage == "verify":
        stage_verify(scope, args.system)
        return
    if args.stage == "resolve":
        cmd_resolve(scope, creds, args.system, args.limit)
        return
    if args.stage in ("identify", "all"):
        stage_identify(scope, creds, args.system, args.limit, args.retry_miss)
    if args.stage in ("fetch", "all"):
        stage_fetch(scope, creds, args.system, args.like)
    if args.stage in ("assemble", "all"):
        stage_assemble(scope, args.system, args.prune, args.like)
    if args.stage in ("package", "all"):
        stage_package(scope, args.system)


if __name__ == "__main__":
    main()
