#!/usr/bin/env bash
set -euo pipefail

GH_BASE="${GH_BASE:-https://github.com/chipster6502}"
GRPS=("$@")
[ ${#GRPS[@]} -gt 0 ] || GRPS=(nintendo-consoles nintendo-handhelds sega atari nec sony snk misc arcade)
BRANCHES=(media-box2d media-box3d media-mixrbv2 db)

BASE="$(cd "$(dirname "$0")/.." && pwd)"
for G in "${GRPS[@]}"; do
    DIR="$BASE/pub-$G"
    if [ ! -d "$DIR/.git" ]; then
        git clone -q "$GH_BASE/artworkdb-$G.git" "$DIR" \
            || { echo "pub-$G: clon fallido; crea artworkdb-$G en GitHub primero"; exit 1; }
        echo "pub-$G: clonado"
    fi
    cd "$DIR"
    git fetch -q origin
    for BR in "${BRANCHES[@]}"; do
        if git ls-remote --exit-code --heads origin "$BR" >/dev/null 2>&1; then
            echo "pub-$G: $BR ya existe"
        else
            TREE=$(git hash-object -w -t tree /dev/null)
            C=$(git commit-tree "$TREE" -m "chore: init $BR branch")
            git push -q origin "$C:refs/heads/$BR"
            echo "pub-$G: $BR creada"
        fi
    done
done
