# MiSTer Artwork Pack

Builds game artwork packs for MiSTer FPGA, distributed through the standard
Downloader. Images are fetched from ScreenScraper once, offline, and served as
plain files on the SD card — consumers need no network access and no
credentials.

**Published today:** 39 systems, 24,756 images, 2.49 GB, in the `box2d`
style. The full list, with a `db_id` and repository per system, is in
[PACK_FORMAT.md](PACK_FORMAT.md).

This repository holds the builder. The images themselves live in separate
`artworkdb-*` repositories, one per hardware family.

## What a pack looks like on the SD card

```
docs/<System>/Artwork/<key>.jpg      the image
docs/<System>/Artwork/index.tsv      every known dump -> key
docs/<System>/Artwork/gameinfo.tsv   name, year, genre, developer, players
docs/<System>/Artwork/synopsis_*.tsv one per language
docs/<System>/Artwork/manifest.tsv   style and ScreenScraper system per image
```

The path is part of the format, not a configuration option. Reading a pack
means joining `docs/`, the system folder and the game key — nothing else.

**Keys** are No-Intro names for cartridges, Redump names for CD systems, and
MAME setnames for arcade and Neo Geo. **`index.tsv`** resolves everything
that is not an exact key: clones to their parent, alternate names, and
CRC+size. **`gameinfo.tsv`** also lists games that have metadata but no
image, so a consumer can still show details when artwork is missing.

Images are baseline JPEG, at most 768 px on the long side.

**Writing a consumer?** [PACK_FORMAT.md](PACK_FORMAT.md) is the
specification: the TSV columns, the resolution algorithm the MiSTer Monitor
uses, the shared-catalogue rules, and what is stable across releases.

## Styles

Three, mutually exclusive. A user installs one; consumers read whatever is
there and never need to know which.

| Style | Content | Example |
|---|---|:---:|
| `box2d` | flat box scan | <img src="https://raw.githubusercontent.com/chipster6502/artworkdb-sega/media-box2d/docs/Genesis/Artwork/Sonic%20The%20Hedgehog%20%28USA%2C%20Europe%29.jpg" height="200"> |
| `box3d` | 3D box render | <img src="https://raw.githubusercontent.com/chipster6502/artworkdb-sega/media-box3d/docs/Genesis/Artwork/Sonic%20The%20Hedgehog%20%28USA%2C%20Europe%29.jpg" height="200"> |
| `mixrbv2` | screenshot inside a TV frame | <img src="https://raw.githubusercontent.com/chipster6502/artworkdb-sega/media-mixrbv2/docs/Genesis/Artwork/Sonic%20The%20Hedgehog%20%28USA%2C%20Europe%29.jpg" height="200"> |

All three write the same path and share a `db_id`, so switching styles is a
clean replacement rather than two databases fighting over one file. `box2d`
is complete; `box3d` and `mixrbv2` are published for Genesis only so far.

## Installing

The usual route will be Update All, once the toggles exist. Until then, add
one section per system to `downloader.ini`. The `db_url` selects the style:

```ini
[chipster6502/artworkdb-genesis]
db_url = https://raw.githubusercontent.com/chipster6502/artworkdb-sega/db/genesis_box2d.json.zip
```

The URL follows one pattern for every system —
`artworkdb-<group>/db/<system>_<style>.json.zip` — and the group of each
system is in the table in [PACK_FORMAT.md](PACK_FORMAT.md).

Every file is tagged `docs`, `artwork`, `<system>` and `<system>artwork`, so
a global filter can narrow things down:

```ini
[mister]
filter = artwork !arcadeartwork
```

## Building

Four resumable stages, driven by `scope.ini`:

| Stage | Does |
|---|---|
| `identify` | queries ScreenScraper for each game in scope and caches the reply |
| `fetch` | downloads the first media in the style recipe that exists |
| `assemble` | normalises images, writes the TSVs |
| `package` | emits `db.json.zip` and the `downloader.ini` section |

Plus `verify`, which compares what is published against what was built —
run it before announcing an update — and `resolve`, which sweeps
ScreenScraper subsystems for games the transversality guard rejected and
proposes `overrides.tsv` lines.

```bash
export SS_DEVID=... SS_DEVPASSWORD=... SS_SSID=... SS_SSPASSWORD=...
python3 build_pack.py identify --system Genesis
python3 build_pack.py fetch    --system Genesis --style box3d
python3 build_pack.py assemble --system Genesis --style box3d --prune
python3 build_pack.py package  --system Genesis --style box3d
python3 build_pack.py verify   --system Genesis --style box3d
```

All state lives on disk. Any stage can be interrupted and resumed, and
deleting a file forces only that piece to be rebuilt. `--prune` removes
images whose key left the scope; `--retry-miss` re-queries games previously
marked as misses, which is how an edited `overrides.tsv` takes effect.

Two reviewed tables refine a build: `overrides.tsv` pins the ScreenScraper
subsystem for a key whose name resolves elsewhere, and `names.tsv` fixes a
display name where ScreenScraper's own is wrong. `neogeo_dat.py` turns the
Neo Geo core's `romsets.xml` into the Parent/Clone DAT the builder reads.
`validate_db.py` checks a generated database against the Downloader's own
parser.

## Configuration

Copy `scope.ini.example` to `scope.ini` and fill in your ScreenScraper
softname. Credentials are read **only** from the environment and are redacted
from caches and error output; they never touch the file.

Each style declares its own recipe and, where it matters, its own quality:

```ini
[style:box3d]
recipe = box-3D>mixrbv2
quality = 95
```

Quality is per style on purpose. Card blocks are typically 128 KB, so an image
below that threshold occupies a whole block anyway — the useful setting is the
highest quality that still fits in one. That lands at 85 for `box2d`, 95 for
`box3d` and 65 for `mixrbv2`. An image that still spills into a second block
is compressed a little harder until it fits, down to a floor of 75.

Each system declares which repository hosts its media:

```ini
[system:Genesis]
ss_id = 1
group = sega
```

The `db_id` stays per system regardless of `group`, so regrouping later only
changes the URL inside `db.json` and breaks nobody's `downloader.ini`.

## Credits

Artwork and metadata come from [ScreenScraper](https://www.screenscraper.fr),
contributed by its community. Arcade scope is derived from the MAME listxml.
