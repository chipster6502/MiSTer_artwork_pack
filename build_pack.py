#!/usr/bin/env python3
"""MiSTer Boxart Pack builder.

Four resumable stages driven by scope.ini:

  identify  jeuInfos per game -> work/meta/<System>/<key>.json
  fetch     first style of the recipe that has media -> work/pool/<style>/
  assemble  normalize to baseline JPEG (<=768 px) -> out/media/docs/, plus
            gameinfo.tsv, synopsis_<lang>.tsv, index.tsv and manifest.csv
  package   one Downloader database per system -> out/db/

State lives on disk: each stage skips whatever already exists, so any stage
can be interrupted and re-run. Delete a file to force it to be rebuilt.

Credentials come from the environment, never from files:
  SS_DEVID, SS_DEVPASSWORD, SS_SSID, SS_SSPASSWORD
"""

import argparse
import concurrent.futures
import configparser
import csv
import hashlib
import io
import json
import os
import re
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
    def __init__(self, path):
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
        self.recipe = [s.strip() for s in pk.get("style_recipe", "box-2D>mixrbv2").split(">") if s.strip()]
        self.style_label = pk.get("style_label", "").strip() or \
            self.recipe[0].lower().replace("-", "").replace("_", "")
        self.quality = int(pk.get("quality", "80"))
        self.max_px = int(pk.get("max_px", "768"))
        self.placeholder_min = int(pk.get("placeholder_min", "3"))
        self.url_base = pk.get("url_base", "").rstrip("/")
        self.db_id_prefix = pk.get("db_id_prefix", "").strip()

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
                # extra ids accepted besides the family of ss_id
                "accept": tuple(x.strip() for x in accept.split(",") if x.strip()),
                "reject": tuple(x.strip() for x in reject.split(",") if x.strip()),
            }
        if not self.softname:
            die("scope.ini: [screenscraper] softname is required "
                "(must match the app name registered on ScreenScraper)")
        if not self.systems:
            die("scope.ini: no [system:<Name>] sections found")


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
    "closed",
    "quota",
)


def strip_auth(url):
    """Drop credential parameters. SS embeds them in media URLs and echoes
    them back in errors, so replies must be cleaned before hitting disk."""
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


def check_quota_wall(body_text):
    """Abort only on a real refusal. Valid JSON without a game is an ordinary
    miss - the reply carries usage counters that mention quotas."""
    stripped = body_text.strip()
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
    for marker in QUOTA_MARKERS:
        if marker.lower() in body_text.lower():
            die("ScreenScraper refused the request (quota/threads/API closed):\n"
                f"  '{redact(body_text)[:160]}'\n"
                "  Stop for now and re-run later - every stage resumes where it left off.")


ABORT = threading.Event()
COUNTER_LOCK = threading.Lock()


def run_parallel(tasks, worker, threads, delay, label, total):
    """Bounded thread pool. threads must stay at or below the account's
    maxthreads. die() in a thread only kills that thread, so refusals are
    signalled through ABORT and re-raised by the caller."""
    done = {"n": 0}
    results = []

    def wrapped(task):
        if ABORT.is_set():
            return None
        try:
            outcome = worker(task)
        except SystemExit:
            ABORT.set()
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
    return [r for r in results if r is not None]


# ----------------------------------------------------------------------------
# game sources (what to build): No-Intro DAT, MRA folder, plain list
# ----------------------------------------------------------------------------

# Unofficial / Non-Redump sets: SS does not catalog them, so they would only
# burn quota and produce misses.
UNOFFICIAL_MARKERS = ("(unl)", "(aftermarket)", "(pirate)", "(homebrew)",
                      "(test program)", "(debug", "(bios)")


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
        # 300+ MB: stream it. Keep playable coin-op machines only; devices,
        # BIOS, mechanical cabinets and the MESS consoles/computers (no coin
        # slot) are not arcade games.
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
# variant clustering: a DAT has one <game> per dump (regions, revisions,
# betas). Cluster them, fetch art only for one elected representative, and
# index every variant to it: full matching scope at one image per game.
# ----------------------------------------------------------------------------

REGION_WORDS = {"wor": ("world",), "us": ("usa",), "eu": ("europe",),
                "jp": ("japan",), "sp": ("spain",), "fr": ("france",),
                "de": ("germany",), "it": ("italy",)}
# Summed demotion weights. An unreleased prototype is a far worse artwork
# source than a re-release, so it sinks deeper.
VARIANT_WEIGHTS = (
    (3, ("beta", "proto", "sample", "(demo", "(alt", "(program", "(debug")),
    (1, ("virtual console", "classic mini", "switch online", "(arcade)",
         "(gamecube", "(wii", "collection", "anthology", "compilation",
         "archives", "anniversary", "competition cart", "mail-order",
         "aftermarket", "(unl", "(pirate")),
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


def variant_penalty(name):
    low = name.lower()
    return sum(weight
               for weight, markers in VARIANT_WEIGHTS
               for marker in markers if marker in low)


def elect_key(entry, regions):
    """Representative sort key: variant penalty, then region chain, then
    <release> as tie-break, then name. Region outranks <release> because
    No-Intro tags it unevenly (Starwing (Germany) would beat Star Fox (USA))."""
    return (variant_penalty(entry["key"]),
            region_score(entry["key"], regions),
            0 if entry.get("release") else 1,
            len(entry["key"]), entry["key"])


def cluster_mame_entries(entries):
    """Group MAME sets under their parent. Returns (parents, members).

    Unlike No-Intro there is nothing to elect: MAME defines the parent as
    the reference set, so it is queried and every clone maps to its key in
    the index. An orphan clone (parent filtered out) becomes its own group.
    """
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
    """Group dumps and elect one representative each. Returns
    (representatives, members) with members rows (name, crc, size, key).

    Groups by the official cloneof when the DAT is P/C XML, by normalized
    title otherwise. The parent is not automatically the representative:
    No-Intro often picks a European Rev 1."""
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
    """systemesListe.php, cached in work/systems.json. Needed to know which
    systems are children of which."""
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
    always loses, even when it is a legitimate child (Game & Watch hangs off
    Mame, which is how 'pacman' returned a Tomytronic LCD box)."""
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
    """key -> systemeid, from a reviewed TSV. jeuInfos falls back to a global
    name search when a romset is missing from the queried system, so generic
    names need the exact subsystem spelled out."""
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

        done = 0
        todo = []
        for e in entries:
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

            out.write_text(
                json.dumps(redact_json(jeu), ensure_ascii=False, indent=1),
                encoding="utf-8")
            return "hit"

        outcomes = run_parallel(todo, identify_one, scope.threads, scope.delay,
                                "identified", len(todo))
        hits = outcomes.count("hit")
        miss = outcomes.count("miss")
        rejected = outcomes.count("rejected")

        log(f"[identify] {system}: {hits} new, {miss} miss, "
            f"{rejected} out-of-family, {done} already cached")


# ----------------------------------------------------------------------------
# stage 2: fetch (recipe-driven media download)
# ----------------------------------------------------------------------------

def pool_index(style, system):
    """{stem: path} for a pool directory. Never glob by key: No-Intro names
    contain metacharacters ("[b]" is a character class) and the lookup would
    silently miss. Listing once is also cheaper."""
    pool = Path("work/pool") / style / system
    if not pool.is_dir():
        return {}
    return {path.stem: path for path in pool.iterdir() if path.is_file()}


def pick_media(medias, style, regions):
    """First media of given type following the region chain, else any region."""
    of_style = [m for m in medias if m.get("type") == style and m.get("url")]
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


def stage_fetch(scope, creds, only_system=None):
    for system, cfg in scope.systems.items():
        if only_system and system != only_system:
            continue
        meta_dir = Path("work/meta") / system
        if not meta_dir.is_dir():
            log(f"[fetch] {system}: no meta yet, run identify first")
            continue
        metas = sorted(meta_dir.glob("*.json"))
        log(f"[fetch] {system}: {len(metas)} identified games, "
            f"recipe {' > '.join(scope.recipe)}")

        cached = 0
        pools = {style: pool_index(style, system) for style in scope.recipe}
        todo = []
        for meta_path in metas:
            key = meta_path.stem
            if any(pools[style].get(key) for style in scope.recipe):
                cached += 1
            else:
                todo.append(meta_path)

        def fetch_one(meta_path):
            key = meta_path.stem
            jeu = json.loads(meta_path.read_text(encoding="utf-8"))
            medias = jeu.get("medias") or []
            media = style_used = None
            for style in scope.recipe:
                media = pick_media(medias, style, scope.regions)
                if media:
                    style_used = style
                    break
            if not media:
                return "none"

            ext = (media.get("format") or "jpg").lower()
            pool = Path("work/pool") / style_used / system
            pool.mkdir(parents=True, exist_ok=True)
            target = pool / f"{key}.{ext}"
            try:
                body = http_get(with_auth(media["url"], creds, scope.softname),
                                scope.softname, timeout=90)
            except HTTPError:
                return "none"
            if len(body) < 1024 and b"JFIF" not in body and b"PNG" not in body:
                check_quota_wall(body.decode("utf-8", errors="replace"))
                return "none"
            target.write_bytes(body)
            return "got"

        outcomes = run_parallel(todo, fetch_one, scope.threads, scope.delay,
                                "downloaded", len(todo))
        got = outcomes.count("got")
        none = outcomes.count("none")

        log(f"[fetch] {system}: {got} downloaded, {cached} cached, {none} without media")


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
                   "style", "region", "md5", "size", "width", "height"]


def ss_subsystem(jeu):
    """(id, name) of the system SS actually resolved the game to. Arcade is a
    parent plus dozens of manufacturer subsystems, so this differs from the
    queried id and makes coverage gaps diagnosable per manufacturer."""
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
    """Read an existing manifest, padding rows written by older builds so a
    newly added column never breaks a resumed run."""
    rows = {}
    if path.is_file():
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rows[(row["system"], row["key"])] = {
                    name: row.get(name, "") or "" for name in fields}
    return rows


def find_placeholders(scope, system, threshold):
    """MD5s of empty templates. SS renders the mix composite even with no
    art, giving an identical blank frame to every such game; real artwork is
    never byte-identical, so a repeated hash is a placeholder."""
    by_hash = {}
    for style in scope.recipe:
        pool = Path("work/pool") / style / system
        if not pool.is_dir():
            continue
        for path in pool.iterdir():
            if path.is_file():
                by_hash.setdefault(md5_file(path), []).append(path.stem)
    return {h: keys for h, keys in by_hash.items() if len(keys) >= threshold}


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


def stage_assemble(scope, only_system=None, prune=False):
    from PIL import Image

    manifest_path = Path("out/manifest.csv")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(manifest_path, MANIFEST_FIELDS)

    for system, cfg in scope.systems.items():
        if only_system and system != only_system:
            continue
        meta_dir = Path("work/meta") / system
        if not meta_dir.is_dir():
            continue
        out_dir = Path("out/media/docs") / system / "Artwork"
        out_dir.mkdir(parents=True, exist_ok=True)

        # work/meta accumulates across runs: keys no longer in _members.tsv
        # left the scope and must not reach the pack
        in_scope = read_members(meta_dir)
        pools = {style: pool_index(style, system) for style in scope.recipe}
        placeholders = find_placeholders(scope, system, scope.placeholder_min)
        if placeholders:
            total = sum(len(v) for v in placeholders.values())
            log(f"[assemble] {system}: {len(placeholders)} placeholder "
                f"image(s) detected, affecting {total} games")
        rows = []
        syn_rows = {}  # lang -> [(key, text)]
        made = skipped = stale = blanks = broken = 0
        for meta_path in sorted(meta_dir.glob("*.json")):
            key = meta_path.stem
            if in_scope is not None and key not in in_scope:
                stale += 1
                continue
            jeu = json.loads(meta_path.read_text(encoding="utf-8"))
            name, year, genre, dev, players, synopsis = game_info_row(jeu, scope)
            rows.append([key, name, year, genre, dev, players])
            for lang, text in synopsis.items():
                syn_rows.setdefault(lang, []).append((key, text))

            src = style_used = None
            for style in scope.recipe:
                hit = pools[style].get(key)
                if hit:
                    # an empty template is worse than no image at all
                    if md5_file(hit) in placeholders:
                        blanks += 1
                        continue
                    src, style_used = hit, style
                    break
            if not src:
                continue

            target = out_dir / (key + ".jpg")
            if target.is_file():
                skipped += 1
            else:
                try:
                    with Image.open(src) as img:
                        img = img.convert("RGB")
                        w, h = img.size
                        if max(w, h) > scope.max_px:
                            ratio = scope.max_px / max(w, h)
                            img = img.resize((round(w * ratio), round(h * ratio)),
                                             Image.LANCZOS)
                        img.save(target, format="JPEG", quality=scope.quality,
                                 optimize=True, progressive=False)
                except Exception as exc:
                    # drop it from the pool so the next fetch retries
                    log(f"[assemble] {system}: unreadable {src.name} ({exc}); "
                        "removed from pool, will be re-fetched")
                    src.unlink(missing_ok=True)
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
            }

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
                    if (out_dir / (key + ".jpg")).is_file():
                        f.write(line + "\n")
                        indexed += 1

        # images left over from a previous, wider scope
        if in_scope is not None:
            orphans = [p for p in out_dir.glob("*.jpg")
                       if p.stem not in in_scope]
            for orphan in orphans:
                manifest.pop((system, orphan.stem), None)
                if prune:
                    orphan.unlink()
            if orphans:
                names = ", ".join(sorted(p.stem for p in orphans)[:5])
                log(f"[assemble] {system}: {len(orphans)} image(s) out of "
                    f"scope ({names}{'...' if len(orphans) > 5 else ''}) "
                    + ("deleted" if prune else
                       "- run with --prune to delete them"))

        lang_list = ", ".join(sorted(syn_rows)) or "none"
        log(f"[assemble] {system}: {made} images written, {skipped} kept, "
            f"gameinfo.tsv with {len(rows)} rows, "
            f"synopsis in {len(syn_rows)} languages ({lang_list}), "
            f"index.tsv with {indexed} variants"
            + (f", {stale} meta out of scope" if stale else "")
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
    """One Downloader database per system. Format checked against
    Downloader_MiSTer: "path": "pext" allows installing to USB instead of the
    SD card, "base_files_url" replaces a per-file url, and "tags" are what
    let a user filter by system from downloader.ini."""
    if not scope.url_base or not scope.db_id_prefix:
        die("scope.ini: [pack] url_base and db_id_prefix are required for package")
    db_dir = Path("out/db")
    db_dir.mkdir(parents=True, exist_ok=True)
    snippets = []

    for system in scope.systems:
        if only_system and system != only_system:
            continue
        media_dir = Path("out/media/docs") / system / "Artwork"
        if not media_dir.is_dir():
            continue
        install_root = Path("docs") / system / "Artwork"

        tag_dictionary, tag_ids = {}, []
        for name in ("docs", "artwork", system.lower(),
                     f"{system.lower()}artwork"):
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
            f"docs/{system}/Artwork": {"path": "pext", "tags": system_tags},
        }

        db_id = scope.db_id_prefix + system.lower()
        db = {
            "db_id": db_id,
            "timestamp": int(time.time()),
            "base_files_url": scope.url_base + "/",
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
        snippets.append((db_id, base))

    if snippets:
        log("\n--- downloader.ini sections (append on the MiSTer) ---")
        for db_id, base in snippets:
            log(f"[{db_id}]")
            log(f"db_url = <RAW-URL-OF-YOUR-REPO-DB-BRANCH>/{base}.json.zip\n")


def cmd_resolve(scope, creds, only_system=None, limit=0):
    """Sweep candidate subsystems for rejected games and write proposed
    override lines to overrides.suggested.tsv. Nothing is applied
    automatically; only rejected entries are swept, bounding the cost."""
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


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="MiSTer Boxart Pack builder")
    parser.add_argument("stage", choices=["identify", "fetch", "assemble",
                                          "package", "all", "systems",
                                          "resolve"])
    parser.add_argument("--scope", default="scope.ini")
    parser.add_argument("--system", default=None,
                        help="restrict to one [system:<Name>] section")
    parser.add_argument("--limit", type=int, default=0,
                        help="identify only the first N entries (smoke tests)")
    parser.add_argument("--prune", action="store_true",
                        help="delete assembled images that are no longer in "
                             "scope (assemble)")
    parser.add_argument("--retry-miss", action="store_true",
                        help="re-query entries previously marked .miss "
                             "(use after editing overrides.tsv)")
    args = parser.parse_args()

    scope = Scope(args.scope)
    needs_net = args.stage in ("identify", "fetch", "all", "systems",
                               "resolve")
    creds = credentials() if needs_net else None

    if args.stage == "systems":
        cmd_systems(scope, creds)
        return
    if args.stage == "resolve":
        cmd_resolve(scope, creds, args.system, args.limit)
        return
    if args.stage in ("identify", "all"):
        stage_identify(scope, creds, args.system, args.limit, args.retry_miss)
    if args.stage in ("fetch", "all"):
        stage_fetch(scope, creds, args.system)
    if args.stage in ("assemble", "all"):
        stage_assemble(scope, args.system, args.prune)
    if args.stage in ("package", "all"):
        stage_package(scope, args.system)


if __name__ == "__main__":
    main()
