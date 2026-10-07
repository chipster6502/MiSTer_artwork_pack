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

Two more packs, in-game screenshots and title screens, use the same layout
in folders of their own, `docs/<System>/Screenshots/` and
`docs/<System>/Titles/`, with PNG images. They are installed next to the
boxes, not instead of them; see [Screenshot and title packs](#screenshot-and-title-packs).

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

One image per game and per box: when several dumps of one game would
carry a byte-identical box, the pack keeps one file and the other dumps
are index rows pointing to it. Dumps whose box ScreenScraper holds per
region keep separate images.

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

A screenshot or title pack resolves the same way against its own folder,
its own `index.tsv` and `.png`. Resolve each pack independently: the set of
keys that carry a file differs between packs (below), while every dump
resolves in all of them.

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

Box styles: baseline JPEG, RGB, longest side at most **768 px**. Quality is tuned per
style so that a typical image fits one 128 KB card block. No placeholder
images: a game with no usable art gets no file (an absent image is treated
as better than a wrong or empty one).

## Styles

Three, mutually exclusive: `box2d` (flat scan), `box3d` (3D render),
`mixrbv2` (screenshot in a TV frame). A user installs one. All three write
the **same paths** and share the same `db_id`, so consumers read whatever is
there and never need to know which style is installed — `manifest.tsv` says,
if it matters.

The three are published complete and serve **the same keys through the same
`index.tsv`**: switching styles is a clean replacement, never a game gained
or lost. Where a style has no image of its own for a game — ScreenScraper
holds none, or the one it holds was reviewed as unusable — the image comes
from another style rather than being left out, and `manifest.tsv` records
which style it came from.

## Screenshot and title packs

Two packs beside the boxes, each installed on its own:

| Pack | Folder | Content | Style label | db_id |
|---|---|---|---|---|
| Screenshots | `docs/<System>/Screenshots/` | an in-game screen | `snap` | `chipster6502/artworkdb-<system>-screenshots` |
| Titles | `docs/<System>/Titles/` | the title screen | `title` | `chipster6502/artworkdb-<system>-titles` |

Same keys, same five file types and the same resolution as the boxes; the
image is `<key>.png`. Two differences a consumer may notice:

- **Fewer files for the same games.** A screenshot is usually identical for
  every regional release of a game, so more dumps share one file than in the
  box pack. `index.tsv` still maps every dump; only the representative keys
  differ.
- **`manifest.tsv` has a fourth column**, `encoding`:

  ```
  #key	style	ss_system_id	encoding
  ```

  `style` is the source: `ss` or `sstitle` (ScreenScraper), `lr-snaps` or
  `lr-titles` ([libretro-thumbnails](https://github.com/libretro-thumbnails)).
  `encoding` is `lossless` for every image today. The column stays so a
  build with a size budget can mark images reduced to a 256-colour palette
  (`256colors`) without changing the layout.

### Images

PNG at the resolution the source was captured at, **never resampled**:

- An image that is an exact nearest-neighbour enlargement by one integer
  factor on both axes is reduced by that factor; nothing is lost, and the
  file is the native frame (a SNES 512×448 capture that is 256×224 doubled
  ships as 256×224). Axes are never reduced separately, so the aspect a
  1:1 display shows is the capture's own: an Atari 2600 frame stays at
  320×210 with doubled columns.
- Every image is lossless: a consumer decodes exactly what the source
  captured. Most 2D screens are a few KB; 3D and CD screens in full colour
  often need two or three 128 KB card blocks.
- A source larger than the console's picture — past 400×300 once an exact
  enlargement is undone — is an enlargement, usually filtered. When
  libretro-thumbnails has the same key within that size, its capture ships
  instead; otherwise the enlargement does. PlayStation, N64, Saturn and 3DO
  are exempt by choice: their large captures come from emulators, either
  renders above the console's resolution (ScreenScraper's N64 captures are
  640×480, twice the console's 320×240) or PlayStation frames enlarged and
  smoothed without the console's dithering, and were judged better than
  the native frame, so they ship as captured. Vectrex is exempt too: a
  vector display has no native raster.

Show these images with nearest-neighbour or integer scaling. Anything that
imitates a CRT — scanlines, masks, blur — belongs in the consumer at display
time, never in the file.

### Sources

ScreenScraper first: it covers almost every key and serves the capture of
the key's own region. [libretro-thumbnails](https://github.com/libretro-thumbnails),
matched by exact No-Intro/Redump name, fills in where ScreenScraper has no
image or only a JPEG (a JPEG can never become lossless), and replaces a
ScreenScraper enlargement of a 2D picture with a capture at native size.
Atari 2600 titles take libretro first: both sources are filtered upscales
there, and libretro's are a fraction of the size. Arcade and Neo Geo have
no libretro source, since that project names MAME sets by description.

## Distribution

Media lives in `artworkdb-<group>` repositories, one per hardware family,
with one branch per style plus a `db` branch:

```
https://github.com/chipster6502/artworkdb-<group>
    media-box2d      docs/<System>/Artwork/... for every system in the group
    media-box3d
    media-mixrbv2
    media-snap       docs/<System>/Screenshots/...
    media-title      docs/<System>/Titles/...
    db               <system>_<style>.json.zip (Downloader databases)
```

The Downloader database id is per **system**, regardless of group —
`chipster6502/artworkdb-<system-lowercase>` — so a future regrouping changes
URLs inside `db.json` and breaks nobody's `downloader.ini`. The screenshot
and title packs add `-screenshots` and `-titles` to it, which is what lets
them sit next to a box style. Every file is tagged `docs`, the folder name
(`artwork`, `screenshots`, `titles`), `<system>` and `<system><folder>`
(`snesartwork`, `snesscreenshots`) for filtering.

Any file can be fetched directly, without the Downloader. Two derivation
rules are all you need:

```
media    https://raw.githubusercontent.com/chipster6502/artworkdb-<group>/media-<style>/docs/<System>/Artwork/<key>.jpg
         https://raw.githubusercontent.com/chipster6502/artworkdb-<group>/media-snap/docs/<System>/Screenshots/<key>.png
         https://raw.githubusercontent.com/chipster6502/artworkdb-<group>/media-title/docs/<System>/Titles/<key>.png
db       https://raw.githubusercontent.com/chipster6502/artworkdb-<group>/db/<system-lowercase>_<style>.json.zip
db_id    chipster6502/artworkdb-<system-lowercase>            (boxes)
         chipster6502/artworkdb-<system-lowercase>-screenshots
         chipster6502/artworkdb-<system-lowercase>-titles
```

URL-encode the key, and note that `raw.githubusercontent.com` caches for a
few minutes after a publish.

### Published systems

As of 5 October 2026. Counts change with every publication; treat this as a
snapshot, not an interface.

| System | Group | Boxes | Screenshots | Titles |
|---|---|---:|---:|---:|
| 3DO | misc | 316 | 290 | 297 |
| AmigaCD32 | misc | 149 | 149 | 149 |
| Arcade | arcade | 4,619 | 4,585 | 4,541 |
| Atari2600 | atari | 595 | 574 | 597 |
| ATARI5200 | atari | 95 | 95 | 95 |
| ATARI7800 | atari | 66 | 66 | 66 |
| AtariLynx | atari | 88 | 88 | 87 |
| CD-i | misc | 160 | 148 | 139 |
| Coleco | misc | 165 | 165 | 165 |
| FDS | nintendo-consoles | 202 | 200 | 200 |
| GAMEBOY | nintendo-handhelds | 1,035 | 1,031 | 1,050 |
| GameGear | sega | 382 | 382 | 382 |
| GBA | nintendo-handhelds | 1,634 | 1,633 | 1,627 |
| GBC | nintendo-handhelds | 958 | 952 | 958 |
| Genesis | sega | 1,012 | 1,005 | 1,008 |
| Intellivision | misc | 153 | 153 | 153 |
| Jaguar | atari | 56 | 56 | 56 |
| MegaCD | sega | 246 | 211 | 217 |
| N64 | nintendo-consoles | 409 | 409 | 408 |
| NEOGEO | snk | 171 | 171 | 171 |
| NeoGeo-CD | snk | 97 | 96 | 96 |
| NeoGeoPocket | snk | 10 | 10 | 10 |
| NeoGeoPocket-Color | snk | 75 | 75 | 75 |
| NES | nintendo-consoles | 1,424 | 1,421 | 1,422 |
| ODYSSEY2 | misc | 83 | 83 | 25 |
| PSX | sony | 4,867 | 4,135 | 4,730 |
| S32X | sega | 40 | 40 | 40 |
| Satellaview | nintendo-consoles | 149 | 149 | 144 |
| Saturn | sega | 1,219 | 1,128 | 1,129 |
| SG-1000 | sega | 73 | 73 | 73 |
| SMS | sega | 343 | 342 | 343 |
| SNES | nintendo-consoles | 1,802 | 1,798 | 1,800 |
| SuperGrafx | nec | 5 | 5 | 5 |
| TGFX16 | nec | 301 | 297 | 298 |
| TGFX16-CD | nec | 396 | 379 | 385 |
| VECTREX | misc | 34 | 34 | 34 |
| VirtualBoy | nintendo-consoles | 27 | 27 | 27 |
| WonderSwan | misc | 111 | 111 | 111 |
| WonderSwanColor | misc | 91 | 91 | 91 |
| **39 systems** | | **23,658** | **22,657** | **23,204** |

*Boxes* counts each of the three box styles, which serve the same keys.
The group names the repository; with it, the rules above give the `db_id`,
the database and every image URL of a system in any style.

| Style | Folder | Systems | Images | Download | Card, 32 KB blocks | Card, 128 KB blocks |
|---|---|---:|---:|---:|---:|---:|
| `box2d` | `Artwork` | 39 | 23,658 | 2.39 GB | 2.75 GB | 3.24 GB |
| `box3d` | `Artwork` | 39 | 23,658 | 2.14 GB | 2.54 GB | 3.20 GB |
| `mixrbv2` | `Artwork` | 39 | 23,658 | 2.04 GB | 2.43 GB | 3.20 GB |
| `snap` | `Screenshots` | 39 | 22,657 | 1.16 GB | 1.66 GB | 3.57 GB |
| `title` | `Titles` | 39 | 23,204 | 1.19 GB | 1.71 GB | 3.64 GB |

The card columns are what the files take once installed. Every file takes
whole blocks however small it is, so a 2D screen of a few KB costs a full
block. With 32 KB blocks the screenshot and title packs take about two
thirds of a box style; with 128 KB blocks, the exFAT default on cards over
32 GB, 10–15% more, because 3D and CD screens often need two or three
blocks.
Each pack also carries its own TSV files, about 71 MB per style.

A consumer that walks the systems it finds under `docs/` needs none of this
table — the folder names on the card are the list. It matters only when
fetching over HTTP, where you have to know which repository holds a system.

## What you can rely on

Stable, treated as a contract:

- the path schemes `docs/<System>/Artwork/`, `docs/<System>/Screenshots/`
  and `docs/<System>/Titles/`, with `.jpg` boxes and `.png` screens;
- the five file names and their column layouts as specified above;
- keys being No-Intro / Redump names and MAME parent setnames;
- one image per game, no placeholders;
- absent things being absent (no empty files, no dummy rows).

Not guaranteed across releases:

- **which dump represents a game** — keys move when No-Intro renames or the
  election changes, so resolve at read time and don't cache keys;
- the exact set of synopsis languages per system;
- image dimensions below the 768 px cap (they follow the source scan), and
  screenshot dimensions (they follow the capture);
- image counts, and which systems exist — the catalogue grows.

## Credits

Artwork and metadata come from [ScreenScraper](https://www.screenscraper.fr),
contributed by its community. Some screenshots and title screens come from
[libretro-thumbnails](https://github.com/libretro-thumbnails); `manifest.tsv`
says which. Respect their terms when redistributing.
