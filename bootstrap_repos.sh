#!/usr/bin/env bash
# Clone every artworkdb-<group> repo into ../pub-<group> and create the
# branches publish.sh pushes to. Empty root commits: media and db branches
# share no history with each other or with main.
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
            || { echo "pub-$G: clone failed; create artworkdb-$G on GitHub first"; exit 1; }
        echo "pub-$G: cloned"
    fi
    cd "$DIR"
    git fetch -q origin
    for BR in "${BRANCHES[@]}"; do
        if git ls-remote --exit-code --heads origin "$BR" >/dev/null 2>&1; then
            echo "pub-$G: $BR exists"
        else
            TREE=$(git hash-object -w -t tree /dev/null)
            C=$(git commit-tree "$TREE" -m "chore: init $BR branch")
            git push -q origin "$C:refs/heads/$BR"
            echo "pub-$G: $BR created"
        fi
    done
done
