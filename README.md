# MiSTer Artwork Pack

> **Work in progress.** Three systems are published as a proof of concept.
> The scope is not complete, and paths, keys and databases may still change.
> Not ready for general use yet.

Builds game artwork packs for MiSTer FPGA, distributed through the standard
Downloader. Images are fetched from ScreenScraper once, offline, and served as
plain files on the SD card — consumers need no network access and no
credentials.

This repository holds the builder. The images themselves live in separate
`artworkdb-*` repositories, one per hardware family.

## What a pack looks like on the SD card

```
docs/<System>/Artwork/<key>.jpg      the image
docs/<System>/Artwork/index.tsv      variant -> key
docs/<System>/Artwork/gameinfo.tsv   year, genre, players
docs/<System>/Artwork/synopsis_*.tsv one per language
```

The path is part of the format, not a configuration option. Reading a pack
means joining `docs/`, the system folder and the game key — nothing else.

**Keys** are No-Intro ROM names for consoles and MAME parent setnames for
arcade. **`index.tsv`** resolves everything that is not an exact key: arcade
clones to their parent, alternate names and CRC+size for consoles.
**`gameinfo.tsv`** also lists games that have metadata but no image, so a
consumer can still show details when artwork is missing.

Images are baseline JPEG, at most 768 px on the long side.

## Styles

Three, mutually exclusive. A user installs one; consumers read whatever is
there and never need to know which.

| Style | Content | Example |
|---|---|---|
| `box2d` | flat box scan | <img src="https://raw.githubusercontent.com/chipster6502/artworkdb-sega/media-box2d/docs/Genesis/Artwork/Sonic%20The%20Hedgehog%20%28USA%2C%20Europe%29.jpg" height="200"> |
| `box3d` | 3D box render | <img src="https://raw.githubusercontent.com/chipster6502/artworkdb-sega/media-box3d/docs/Genesis/Artwork/Sonic%20The%20Hedgehog%20%28USA%2C%20Europe%29.jpg" height="200"> |
| `mixrbv2` | screenshot inside a TV frame | <img src="https://raw.githubusercontent.com/chipster6502/artworkdb-sega/media-mixrbv2/docs/Genesis/Artwork/Sonic%20The%20Hedgehog%20%28USA%2C%20Europe%29.jpg" height="200"> |

All three write the same path and share a `db_id`, so switching styles is a
clean replacement rather than two databases fighting over one file.

## Installing

The usual route will be Update All, once the toggles exist. Until then, add
one section per system to `downloader.ini`. The `db_url` selects the style:

```ini
[chipster6502/artworkdb-genesis]
db_url = https://raw.githubusercontent.com/chipster6502/artworkdb-sega/db/genesis_box2d.json.zip
```

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
run it before announcing an update.

```bash
export SS_DEVID=... SS_DEVPASSWORD=... SS_SSID=... SS_SSPASSWORD=...
python3 build_pack.py identify --system Genesis
python3 build_pack.py fetch    --system Genesis --style box3d
python3 build_pack.py assemble --system Genesis --style box3d
python3 build_pack.py package  --system Genesis --style box3d
python3 build_pack.py verify   --system Genesis --style box3d
```

All state lives on disk. Any stage can be interrupted and resumed, and
deleting a file forces only that piece to be rebuilt.

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
`box3d` and 65 for `mixrbv2`.

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
