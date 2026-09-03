#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-report}"
PACK_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$PACK_DIR/No-Intro"
DST="$PACK_DIR/dats"
[ -d "$SRC" ] || { echo "falta $SRC"; exit 1; }
mkdir -p "$DST"

MAP=(
    "Nintendo - Nintendo Entertainment System (Headered)*.dat|nes.dat"
    "Nintendo - Nintendo 64 (BigEndian)*.dat|n64.dat"
    "Nintendo - Virtual Boy*.dat|virtualboy.dat"
    "Nintendo - Satellaview*.dat|satellaview.dat"
    "Nintendo - Game Boy (Parent-Clone)*.dat|gameboy.dat"
    "Nintendo - Game Boy Color (Parent-Clone)*.dat|gbc.dat"
    "Nintendo - Family Computer Disk System (FDS)*.dat|fds.dat"
    "Nintendo - Game Boy Advance (Parent-Clone)*.dat|gba.dat"
    "Sega - Master System - Mark III*.dat|sms.dat"
    "Sega - Game Gear*.dat|gamegear.dat"
    "Sega - SG-1000*.dat|sg1000.dat"
    "Sega - 32X*.dat|s32x.dat"
    "Atari - Atari 2600 (Parent-Clone)*.dat|atari2600.dat"
    "Atari - Atari 5200 (Parent-Clone)*.dat|atari5200.dat"
    "Atari - Atari 7800 (BIN)*.dat|atari7800.dat"
    "Atari - Atari Jaguar (J64)*.dat|jaguar.dat"
    "Atari - Atari Lynx (LYX)*.dat|atarilynx.dat"
    "NEC - PC Engine - TurboGrafx*.dat|tgfx16.dat"
    "NEC - PC Engine SuperGrafx*.dat|supergrafx.dat"
    "SNK - NeoGeo Pocket (*.dat|ngp.dat"
    "SNK - NeoGeo Pocket Color*.dat|ngpc.dat"
    "Bandai - WonderSwan (*.dat|wonderswan.dat"
    "Bandai - WonderSwan Color*.dat|wsc.dat"
    "Coleco - ColecoVision*.dat|coleco.dat"
    "Mattel - Intellivision*.dat|intellivision.dat"
    "GCE - Vectrex*.dat|vectrex.dat"
    "Magnavox - Odyssey*.dat|odyssey2.dat"
)

problems=0
for entry in "${MAP[@]}"; do
    pat="${entry%%|*}"
    dest="${entry##*|}"
    mapfile -t matches < <(compgen -G "$SRC/$pat" || true)
    if [ ${#matches[@]} -eq 0 ]; then
        echo "FALTA    $dest  <-  $pat"
        problems=$((problems + 1))
    elif [ ${#matches[@]} -gt 1 ]; then
        echo "AMBIGUO  $dest  <-  ${#matches[@]} candidatos:"
        printf '           %s\n' "${matches[@]##*/}"
        problems=$((problems + 1))
    elif [ -f "$DST/$dest" ]; then
        echo "YA       $dest"
    elif [ "$MODE" = "apply" ]; then
        cp "${matches[0]}" "$DST/$dest"
        echo "COPIADO  $dest  <-  ${matches[0]##*/}"
    else
        echo "OK       $dest  <-  ${matches[0]##*/}"
    fi
done

if [ "$problems" -gt 0 ]; then
    echo "--- $problems entrada(s) sin resolver: ajusta el patron en MAP y repite"
    exit 1
fi
[ "$MODE" = "apply" ] || echo "--- todo resuelto; ejecuta: stage_dats.sh apply"
