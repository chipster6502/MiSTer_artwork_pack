# MiSTer Artwork Pack

Builds game artwork packs for MiSTer FPGA, distributed through the standard
Downloader and selectable from **Update All** (2.10 or newer) under
*Extra Content → Game Artwork DBs*. Images are fetched from ScreenScraper
once, offline, and served as plain files on the SD card — consumers need no
network access and no credentials.

**Published today:** 39 systems in five packs. Three box styles with
23,658 images each — 2.39 GB in `box2d`, 2.14 GB in `box3d`, 2.04 GB in
`mixrbv2` — serve the same games through the same index, so switching styles
is a clean replacement. Next to them go two packs of their own, in-game
screenshots (22,657 images, 1.16 GB) and title screens (23,204 images,
1.19 GB), PNG as captured, never resampled; see
[Screenshot and title packs](#screenshot-and-title-packs). The full list,
with the repository of each system and what every pack takes on a card, is
in [PACK_FORMAT.md](PACK_FORMAT.md).

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
The screenshot and title packs are the same five files in
`docs/<System>/Screenshots/` and `docs/<System>/Titles/`, with `<key>.png`.

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
clean replacement rather than two databases fighting over one file. All
three are published for every system in the table.

## Screenshot and title packs

Two packs of their own, installed alongside whichever box style is chosen:

| Pack | Style label | Folder | Images | db_id |
|---|---|---|---:|---|
| Screenshots | `snap` | `docs/<System>/Screenshots/` | 22,657 | `chipster6502/artworkdb-<system>-screenshots` |
| Titles | `title` | `docs/<System>/Titles/` | 23,204 | `chipster6502/artworkdb-<system>-titles` |

- **PNG, never resampled.** A capture that is an exact integer enlargement
  is reduced to its native frame; everything else ships as captured. Pixel
  art stays pixel art, and a consumer scales it with nearest neighbour or
  adds a CRT effect at display time.
- **Lossless, every image.** Nothing is recompressed: what a consumer
  decodes is what the source captured, ready to scale or convert without
  compounding losses.
- **Sources:** ScreenScraper first, [libretro-thumbnails](https://github.com/libretro-thumbnails)
  where ScreenScraper has no image or only a JPEG, and where ScreenScraper's
  capture of a 2D picture is an enlargement while libretro's is at native
  size.
- **Coverage:** `index.tsv` resolves about as many dumps as the box packs
  (51,123 for screenshots and 50,996 for title screens, against 51,162);
  the games left without an image are mostly arcade sets ScreenScraper
  holds no capture for.

Each is about 1.2 GB to download. On the card every file takes whole
blocks, however small: with 128 KB blocks, the exFAT default on cards over
32 GB, each pack takes about 3.6 GB, a little more than a box style, because
3D and CD screens often need two or three blocks; with 32 KB blocks, usual
on cards of 32 GB or less and on FAT32, it is about 1.7 GB, since most 2D
screens are a few KB.

## Installing

### From Update All (recommended)

Update All 2.10 lists every box pack. Run *Update All* from the Scripts menu,
press **UP** during the countdown to open the settings, and go to
**Extra Content → Game Artwork DBs**:

- **Select All** enables every system; each entry below it toggles one.
- **Style for Selected DBs** sets the style for everything selected. Each
  system can also carry its own style; the same three names appear in both
  places: **2D Boxes** (`box2d`), **3D Boxes** (`box3d`) and
  **Box + Screenshot** (`mixrbv2`).
- Save and let Update All run. It writes the selection to
  `downloader_chipster6502_artworkdb.ini` and the Downloader installs the
  images under `docs/`.

Changing a system's style replaces its images on the next run.
Deselecting a system stops its updates but keeps its images on the card. To
delete them, highlight the system and choose **Uninstall** (**Uninstall
All** on the *Select All* row), or use **System Options → Database
Manager** (Update All 2.11), which lists every database the Downloader has
installed. Consumers do not need to know any of this — they read whatever
is on the card.

### By hand

For setups that run the Downloader without Update All, add one section per
system to `downloader.ini`. The `db_url` selects the style:

```ini
[chipster6502/artworkdb-genesis]
db_url = https://raw.githubusercontent.com/chipster6502/artworkdb-sega/db/genesis_box2d.json.zip
```

The URL follows one pattern for every system —
`artworkdb-<group>/db/<system>_<style>.json.zip` — and the group of each
system is in the table in [PACK_FORMAT.md](PACK_FORMAT.md).

Removing a section leaves its files on the card. `update.sh --uninstall
<db_id>` deletes them, keeps any file another database still owns, and
removes the section.

Every file is tagged `docs`, its folder (`artwork`, `screenshots`,
`titles`), `<system>` and `<system><folder>`, so a global filter can narrow
things down:

```ini
[mister]
filter = artwork !arcadeartwork
```

## Building

Four resumable stages, driven by `scope.ini`:

| Stage | Does |
|---|---|
| `identify` | queries ScreenScraper for each game in scope and caches the reply |
| `fetch` | downloads the first media in the style recipe that exists, from ScreenScraper or libretro-thumbnails, and a later one when that is past `native_max` |
| `assemble` | normalises images (JPEG boxes, PNG screens), writes the TSVs |
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

Six reviewed tables refine a build, and together with the DATs they are
what makes it reproducible: `overrides.tsv` pins the ScreenScraper subsystem
for a key whose name resolves elsewhere, `names.tsv` fixes a display name
where ScreenScraper's own is wrong, `rotations.tsv` turns a scan that
ScreenScraper stores sideways, `excludes.tsv` lists the keys reviewed out of
the pack — a demo, a hardware test, a box that is not the game's — which
`identify` never queries again, `media_rejects.tsv` refuses one media of
one game without dropping the key, so a bad mix costs that game its mix and
nothing else, and `image_aliases.tsv` makes a key share another key's image
in one pack (`#style system key winner`): the key's file goes and its dumps
point to the winner in `index.tsv`. That is how a screen reviewed as the same
picture under two fiches ends up as one file; on its own, the builder merges
only keys of one fiche whose sources are byte-identical. `neogeo_dat.py` turns the
Neo Geo core's `romsets.xml` into the Parent/Clone DAT the builder reads.

The screenshot and title packs are built the same way, with `--style snap`
and `--style title`. Their recipes name ScreenScraper media (`ss`,
`sstitle`) and libretro-thumbnails folders (`lr-snaps`, `lr-titles`); the
libretro listing comes from a blob-less clone in `work/libretro/`, so `git`
must be on the PATH. When ScreenScraper answers `NOMEDIA` for a media its
stored fiche lists — it moves files between regions — `fetch` re-reads the
fiche's media list and tries again.

A second style mirrors the first: `--like box2d` makes `fetch` request only
the keys the `box2d` pack serves and `assemble` write the same keys through
the same `index.tsv`, copying the `box2d` image where the new style has
none — `manifest.tsv` records which style each image came from.

## Helpers

Around the four stages, in the order they are used:

| Script | Does |
|---|---|
| `stage_dats.sh` | copies the No-Intro DATs `scope.ini` expects from an unpacked No-Intro pack into `dats/`, under stable names; reports first, `apply` copies |
| `bootstrap_repos.sh` | clones every `artworkdb-<group>` repository into `../pub-<group>` and creates the `media-<style>` and `db` branches |
| `exclude_keys.py` | adds reviewed keys to `excludes.tsv` and deletes what was already built for them |
| `rotate_keys.py` | registers rotations in `rotations.tsv` for keys reviewed as sideways and deletes their built images so `assemble` re-encodes them |
| `reject_media.py` | registers a refused media in `media_rejects.tsv` and deletes only the images each style's manifest attributes to it; in a screenshot pack, the next `fetch` then takes the next source |
| `publish.sh` | pushes one system to its media branch and its database to the `db` branch; refuses if anything outside that system would change |
| `republish_all.sh` | runs assemble–package–publish–verify for every system in `scope.ini`, stopping at the first failure |
| `tools/pack_index.py` | prints the *Published systems* tables of `PACK_FORMAT.md` from the built databases — images per system and pack, download and card size per style — so the document is pasted, never typed |
| `validate_db.py` | parses a generated database with the Downloader's own code |
| `tools/dup_classify.py` | a measurement, not a step: checks that no two images in a system are the same box, and lists the byte-identical pairs across fiches for review |

Both key-list scripts read the keys from a file, one per line, because
No-Intro names carry parentheses, quotes and `!!` that the shell mangles even
when quoted.

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

A screenshot style is declared like any other, plus what makes it a
separate pack:

```ini
[style:snap]
recipe = ss > lr-snaps
format = png
folder = Screenshots
placeholder_min = 0
lossless = all
```

`folder` gives it its own path and `db_id`; `placeholder_min = 0` keeps
screens that several games legitimately share (a series of discs), which
the box check would drop; `lossless` lists the systems whose images keep
every colour (`all` here) — any other system would drop an image past one
card block to 256 colours. Each system names its libretro-thumbnails
repository and, where a measured comparison says so, its own source order:

```ini
[system:Atari2600]
libretro = Atari_-_2600
recipe_title = lr-titles > sstitle
```

A system can also turn the placeholder check back on for one style with
`placeholder_min_<style>`, where its source repeats one generic screen across
games. For screenshots the check compares pixels after the native reduction,
so a screen stored doubled in one file and native in another still counts as
one:

```ini
[system:ODYSSEY2]
placeholder_min_title = 10
```

`native_max` is the largest picture a screenshot style takes as the
console's own. A source past it, even after an exact enlargement is undone,
is an enlargement; when a later source of the recipe fits, that one is
used, and otherwise the enlargement stays. `fetch` downloads the later
source for keys whose pooled images are all past the box, and `assemble`
rebuilds any image whose source changed. A system can set its own box with
`native_max_<style>`, or turn the rule off: Vectrex has no native raster,
and the large captures of the 3D consoles, emulator renders or smoothed
enlargements, were judged better than the native frames:

```ini
[style:snap]
native_max = 400x300

[system:N64]
native_max_snap = off
native_max_title = off
```

## Credits

Artwork and metadata come from [ScreenScraper](https://www.screenscraper.fr),
contributed by its community. Some screenshots and title screens come from
[libretro-thumbnails](https://github.com/libretro-thumbnails), matched by
exact No-Intro/Redump name. Arcade scope is derived from the MAME listxml.
