# MiSTer Artwork Pack — format specification

This document describes the artwork packs as installed on a MiSTer, for
developers who want to read them from their own tools — front-ends, monitors,
overlays, scripts. It covers the on-disk layout, the TSV files, how to resolve
a loaded game to its image, and how the packs are distributed.

For what the pack *is* and how to install or build it, see the
[README](README.md).

## Design in one paragraph

A pack ships **one image per game, not per dump**. `Super Mario World
(Europe)` has no file of its own; an index maps it — and every other known
dump of the game — to the single image that represents it. Everything is
plain files on the card: no database engine, no network, no credentials.
A consumer needs a directory join, a TSV parser and, optionally, a CRC32.

## On-disk layout

```
docs/<System>/Artwork/
    <key>.jpg             one image per game
    index.tsv             every known dump -> the key that represents it
    gameinfo.tsv          name, year, genre, developer, players
    synopsis_<lang>.tsv   one file per language
    manifest.tsv          provenance of each image
```

The path is part of the format, not a configuration option. `<System>` is the
exact name of the corresponding `games/` folder on the MiSTer, with the same
spelling and case (`GAMEBOY`, `NeoGeo-CD`, `PSX`).

Packs are installed with the Downloader's `path: "pext"`, so `docs/` may live
on the SD card **or on any USB drive**. Probe the mount points rather than
assuming `/media/fat`: the reference implementation checks `/media/fat`, then
`/media/usb0` through `/media/usb7`, and uses the first mount where
`docs/<System>/Artwork/` exists.

All TSV files are UTF-8, tab-separated, LF-terminated.

## Keys

The **key** is the identifier a game is filed under, and it is always a name
that already exists in the wild:

- **Consoles (cartridge):** the No-Intro ROM name, without extension —
  `Sonic The Hedgehog (USA, Europe)`.
- **Consoles (CD):** the Redump name. Multi-disc games are represented by one
  disc (normally `(Disc 1)`); the other discs are index rows pointing to it.
- **Arcade:** the MAME **parent** setname — `sfa3`, not a title. Clone
  setnames are index rows pointing to the parent.

Keys contain spaces, commas, apostrophes and parentheses; the image filename
is the key verbatim plus `.jpg`. URL-encode when fetching over HTTP.

Keys are **stable within a release but not across releases**: No-Intro
renames dumps, and the pack occasionally changes which dump represents a
game. Resolve at read time; do not persist keys in your own storage.

## The TSV files

### `index.tsv` — the resolver

```
#name	crc	size	key
Blaster Master Boy (USA)	3b2c7118	131072	Blaster Master Boy (USA)
Blaster Master Boy (USA) (Beta)	4ea70173	131072	Blaster Master Boy (USA)
Blaster Master Boy (World) (Evercade)	42284c26	131072	Blaster Master Boy (USA)
Blaster Master Jr. (Europe)	e9f9016f	131072	Blaster Master Boy (USA)
Bomber King - Scenario 2 (Japan) (En)	b8fe9077	131072	Blaster Master Boy (USA)
```

(Real rows from the Game Boy pack: one game, five catalogued dumps — a beta,
an Evercade re-release and two regional titles among them — all resolving to
the single image that represents it.)

One row per known dump. `name` is the dump's catalogued name (No-Intro,
Redump, or MAME setname). `crc` is the CRC32 in lowercase hex and `size` the
file size in bytes — compare case-insensitively and don't assume either is
present:

- **Cartridge rows** carry the CRC and size of the ROM itself, exactly as
  No-Intro catalogues it. If the file on disk is an untouched dump, hashing
  it gives a guaranteed match even when the filename doesn't.
- **CD rows** carry the CRC and size of the **`.cue` file** of the Redump
  set — a match only for `.cue/.bin` libraries. A `.chd` never matches
  (it is a different file from the one Redump catalogued), so `.chd`
  libraries resolve by name.
- **Arcade rows** have empty `crc` and `size`: a MAME set is a zip of many
  files with no single hash. They resolve by name, which is exact anyway —
  the `.mra` setname *is* the key space.

Only dumps whose image actually exists in the pack are indexed.

### `gameinfo.tsv` — metadata

```
#key	name	year	genre	developer	players
```

One row per game in scope, **including games that have metadata but no
image** — a consumer can still show details when artwork is missing. `name`
is the display title (regional preference: World, US, EU, JP). Empty fields
are empty strings, never omitted columns.

### `synopsis_<lang>.tsv` — descriptions

```
#key	synopsis
```

One file per language, currently up to six: `de`, `en`, `es`, `fr`, `it`,
`pt`. The synopsis is collapsed to a single line.

A language file exists only where ScreenScraper has text in that language for
that system, so **do not assume a fixed set** — a small catalogue can ship
five files where a large one ships six. Glob `synopsis_*.tsv` rather than
probing for a hardcoded list, and fall back to another language when the
user's choice is absent.

### `manifest.tsv` — provenance

```
#key	style	ss_system_id
```

One row per shipped image. `style` is the media type the image was actually
built from (a `box2d` pack may fall back to `mixrbv2` for a game with no box
scan), and `ss_system_id` is the ScreenScraper system the game was catalogued
under — which matters for shared catalogues (below).

## Resolving a game to an image

The reference implementation is `_pack_lookup()` in
[MiSTer_monitor](https://github.com/chipster6502/MiSTer_monitor)'s
`mister_status_server.py`. Five steps, cheapest first, stopping at the first
hit. `key` here is the loaded file's name without extension.

1. **Exact key as filename.** `docs/<System>/Artwork/<key>.jpg` exists —
   the common case, one `stat()`.
2. **Index by name.** Look `key` up in `index.tsv` (lowercased). Catches the
   user holding a dump the pack did not pick as representative.
3. **Trailing `(setname)` as key.** If the name ends in a parenthesised tag
   and that tag is itself a key in this folder, use it. Catches ROM packs
   that prefix the identifier with a title of their own invention —
   `Shock Troopers (set 1) (shocktro)`. Safe because it only fires when the
   tail is an existing key.
4. **Index by CRC32 + size.** Catches a renamed file. This is the step that
   carries cartridge libraries whose No-Intro revision differs from the
   pack's — No-Intro renames dumps as its romanisation policy evolves
   (*Dondoko-tou* → *Dondoko Shima*), and by name those never match. Costs
   nothing extra if you already hash for other reasons; skip it if you
   don't.
5. **Index by bare title.** Strip every parenthesised tag from both sides
   and compare what remains, so `F-18 Hornet (NTSC) (Absolute) (1988)`
   matches `F-18 Hornet (USA)`. **Skip this step when the stripped title is
   not unique in the index** — serving a coin-flip image is worse than
   serving none.

Steps 2–5 need `index.tsv`; when it is missing, degrade to step 1 rather
than failing. If a step maps to a key whose `.jpg` is absent, fall through.

Field measurement, PSX on real hardware: of 40 catalogued games, 9 resolved
at step 1, 29 through the index, 2 by title. **The index is what carries the
pack** — with exact-name resolution alone, three quarters of the library
would show no artwork.

## Shared catalogues

Some catalogues are played through a core that reports a different system.
Each keeps its own `docs/` folder, and the consumer is expected to fall back
between **siblings** in a fixed order:

| Core reports | Try folders, in order | Why |
|---|---|---|
| Game Boy | `GAMEBOY`, then `GBC` | ScreenScraper splits dual-mode (`GB Compatible` / GBC) cartridges between the two catalogues on its own criteria |
| Game Boy Color | `GBC`, then `GAMEBOY` | same, symmetric |
| NES/Famicom | `NES` only — but a `.fds` file resolves in `FDS`, then `NES` | asymmetric on purpose: a cartridge must never receive the disk release's box art |
| Super Game Boy | `GAMEBOY`, then `GBC` | SGB has no pack of its own — its ScreenScraper catalogue carries poorer media than Game Boy's for the same titles |

The general rule: try the system's own folder first, then its siblings, and
report which folder actually resolved. `manifest.tsv`'s `ss_system_id` is
what makes the split deterministic if you need to know where an image came
from.

## Images

Baseline JPEG, RGB, longest side at most **768 px**. Quality is tuned per
style so that a typical image fits one 128 KB card block. No placeholder
images: a game with no usable art gets no file (an absent image is treated
as better than a wrong or empty one).

## Styles

Three, mutually exclusive: `box2d` (flat scan), `box3d` (3D render),
`mixrbv2` (screenshot in a TV frame). A user installs one. All three write
the **same paths** and share the same `db_id`, so consumers read whatever is
there and never need to know which style is installed — `manifest.tsv` says,
if it matters.

## Distribution

Media lives in `artworkdb-<group>` repositories, one per hardware family,
with one branch per style plus a `db` branch:

```
https://github.com/chipster6502/artworkdb-<group>
    media-box2d      docs/<System>/Artwork/... for every system in the group
    media-box3d
    media-mixrbv2
    db               <system>_<style>.json.zip (Downloader databases)
```

The Downloader database id is per **system**, regardless of group —
`chipster6502/artworkdb-<system-lowercase>` — so a future regrouping changes
URLs inside `db.json` and breaks nobody's `downloader.ini`. Every file is
tagged `docs`, `artwork`, `<system>` and `<system>artwork` for filtering.

Any file can be fetched directly, without the Downloader. Two derivation
rules are all you need:

```
media    https://raw.githubusercontent.com/chipster6502/artworkdb-<group>/media-<style>/docs/<System>/Artwork/<key>.jpg
db       https://raw.githubusercontent.com/chipster6502/artworkdb-<group>/db/<system-lowercase>_<style>.json.zip
db_id    chipster6502/artworkdb-<system-lowercase>
```

URL-encode the key, and note that `raw.githubusercontent.com` caches for a
few minutes after a publish.

### Published systems

`box2d`, as of 1 September 2026. Counts change with every publication; treat
this as a snapshot, not an interface.

| System | Images | db_id | Media base URL |
|---|---:|---|---|
| 3DO | 335 | `chipster6502/artworkdb-3do` | https://raw.githubusercontent.com/chipster6502/artworkdb-misc/media-box2d/ |
| ATARI5200 | 95 | `chipster6502/artworkdb-atari5200` | https://raw.githubusercontent.com/chipster6502/artworkdb-atari/media-box2d/ |
| ATARI7800 | 66 | `chipster6502/artworkdb-atari7800` | https://raw.githubusercontent.com/chipster6502/artworkdb-atari/media-box2d/ |
| AmigaCD32 | 150 | `chipster6502/artworkdb-amigacd32` | https://raw.githubusercontent.com/chipster6502/artworkdb-misc/media-box2d/ |
| Arcade | 4957 | `chipster6502/artworkdb-arcade` | https://raw.githubusercontent.com/chipster6502/artworkdb-arcade/media-box2d/ |
| Atari2600 | 624 | `chipster6502/artworkdb-atari2600` | https://raw.githubusercontent.com/chipster6502/artworkdb-atari/media-box2d/ |
| AtariLynx | 88 | `chipster6502/artworkdb-atarilynx` | https://raw.githubusercontent.com/chipster6502/artworkdb-atari/media-box2d/ |
| CD-i | 199 | `chipster6502/artworkdb-cd-i` | https://raw.githubusercontent.com/chipster6502/artworkdb-misc/media-box2d/ |
| Coleco | 165 | `chipster6502/artworkdb-coleco` | https://raw.githubusercontent.com/chipster6502/artworkdb-misc/media-box2d/ |
| FDS | 206 | `chipster6502/artworkdb-fds` | https://raw.githubusercontent.com/chipster6502/artworkdb-nintendo-consoles/media-box2d/ |
| GAMEBOY | 1035 | `chipster6502/artworkdb-gameboy` | https://raw.githubusercontent.com/chipster6502/artworkdb-nintendo-handhelds/media-box2d/ |
| GBA | 1637 | `chipster6502/artworkdb-gba` | https://raw.githubusercontent.com/chipster6502/artworkdb-nintendo-handhelds/media-box2d/ |
| GBC | 961 | `chipster6502/artworkdb-gbc` | https://raw.githubusercontent.com/chipster6502/artworkdb-nintendo-handhelds/media-box2d/ |
| GameGear | 382 | `chipster6502/artworkdb-gamegear` | https://raw.githubusercontent.com/chipster6502/artworkdb-sega/media-box2d/ |
| Genesis | 1020 | `chipster6502/artworkdb-genesis` | https://raw.githubusercontent.com/chipster6502/artworkdb-sega/media-box2d/ |
| Intellivision | 156 | `chipster6502/artworkdb-intellivision` | https://raw.githubusercontent.com/chipster6502/artworkdb-misc/media-box2d/ |
| Jaguar | 57 | `chipster6502/artworkdb-jaguar` | https://raw.githubusercontent.com/chipster6502/artworkdb-atari/media-box2d/ |
| MegaCD | 249 | `chipster6502/artworkdb-megacd` | https://raw.githubusercontent.com/chipster6502/artworkdb-sega/media-box2d/ |
| N64 | 409 | `chipster6502/artworkdb-n64` | https://raw.githubusercontent.com/chipster6502/artworkdb-nintendo-consoles/media-box2d/ |
| NEOGEO | 169 | `chipster6502/artworkdb-neogeo` | https://raw.githubusercontent.com/chipster6502/artworkdb-snk/media-box2d/ |
| NES | 1438 | `chipster6502/artworkdb-nes` | https://raw.githubusercontent.com/chipster6502/artworkdb-nintendo-consoles/media-box2d/ |
| NeoGeo-CD | 97 | `chipster6502/artworkdb-neogeo-cd` | https://raw.githubusercontent.com/chipster6502/artworkdb-snk/media-box2d/ |
| NeoGeoPocket | 10 | `chipster6502/artworkdb-neogeopocket` | https://raw.githubusercontent.com/chipster6502/artworkdb-snk/media-box2d/ |
| NeoGeoPocket-Color | 75 | `chipster6502/artworkdb-neogeopocket-color` | https://raw.githubusercontent.com/chipster6502/artworkdb-snk/media-box2d/ |
| ODYSSEY2 | 83 | `chipster6502/artworkdb-odyssey2` | https://raw.githubusercontent.com/chipster6502/artworkdb-misc/media-box2d/ |
| PSX | 5352 | `chipster6502/artworkdb-psx` | https://raw.githubusercontent.com/chipster6502/artworkdb-sony/media-box2d/ |
| S32X | 40 | `chipster6502/artworkdb-s32x` | https://raw.githubusercontent.com/chipster6502/artworkdb-sega/media-box2d/ |
| SG-1000 | 74 | `chipster6502/artworkdb-sg-1000` | https://raw.githubusercontent.com/chipster6502/artworkdb-sega/media-box2d/ |
| SMS | 343 | `chipster6502/artworkdb-sms` | https://raw.githubusercontent.com/chipster6502/artworkdb-sega/media-box2d/ |
| SNES | 1803 | `chipster6502/artworkdb-snes` | https://raw.githubusercontent.com/chipster6502/artworkdb-nintendo-consoles/media-box2d/ |
| Satellaview | 260 | `chipster6502/artworkdb-satellaview` | https://raw.githubusercontent.com/chipster6502/artworkdb-nintendo-consoles/media-box2d/ |
| Saturn | 1247 | `chipster6502/artworkdb-saturn` | https://raw.githubusercontent.com/chipster6502/artworkdb-sega/media-box2d/ |
| SuperGrafx | 5 | `chipster6502/artworkdb-supergrafx` | https://raw.githubusercontent.com/chipster6502/artworkdb-nec/media-box2d/ |
| TGFX16 | 302 | `chipster6502/artworkdb-tgfx16` | https://raw.githubusercontent.com/chipster6502/artworkdb-nec/media-box2d/ |
| TGFX16-CD | 400 | `chipster6502/artworkdb-tgfx16-cd` | https://raw.githubusercontent.com/chipster6502/artworkdb-nec/media-box2d/ |
| VECTREX | 34 | `chipster6502/artworkdb-vectrex` | https://raw.githubusercontent.com/chipster6502/artworkdb-misc/media-box2d/ |
| VirtualBoy | 27 | `chipster6502/artworkdb-virtualboy` | https://raw.githubusercontent.com/chipster6502/artworkdb-nintendo-consoles/media-box2d/ |
| WonderSwan | 111 | `chipster6502/artworkdb-wonderswan` | https://raw.githubusercontent.com/chipster6502/artworkdb-misc/media-box2d/ |
| WonderSwanColor | 91 | `chipster6502/artworkdb-wonderswancolor` | https://raw.githubusercontent.com/chipster6502/artworkdb-misc/media-box2d/ |

**39 systems, 24,752 images, 2.49 GB.** Append
`docs/<System>/Artwork/<key>.jpg` to a media base URL to fetch one image;
swap `media-box2d` for another style branch to get that style.

A consumer that walks the systems it finds under `docs/` needs none of this
table — the folder names on the card are the list. It matters only when
fetching over HTTP, where you have to know which repository holds a system.

## What you can rely on

Stable, treated as a contract:

- the path scheme `docs/<System>/Artwork/`;
- the five file names and their column layouts as specified above;
- keys being No-Intro / Redump names and MAME parent setnames;
- one image per game, no placeholders;
- absent things being absent (no empty files, no dummy rows).

Not guaranteed across releases:

- **which dump represents a game** — keys move when No-Intro renames or the
  election changes, so resolve at read time and don't cache keys;
- the exact set of synopsis languages per system;
- image dimensions below the 768 px cap (they follow the source scan);
- image counts, and which systems exist — the catalogue grows.

## Credits

Artwork and metadata come from [ScreenScraper](https://www.screenscraper.fr),
contributed by its community. Respect their terms when redistributing.
