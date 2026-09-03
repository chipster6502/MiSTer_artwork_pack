#!/usr/bin/env bash
# Push one assembled system to its media branch and its db zip to the db
# branch of ../pub-<group>. Two commits at most, each touching one system.
set -euo pipefail

[ $# -eq 2 ] || { echo "usage: publish.sh <System> <style>"; exit 1; }
SYSTEM=$1 STYLE=$2
[[ "$SYSTEM" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "invalid system: $SYSTEM"; exit 1; }
[[ "$STYLE" =~ ^[a-z0-9]+$ ]] || { echo "invalid style: $STYLE"; exit 1; }

PACK_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PACK_DIR"
SYS_LC=$(echo "$SYSTEM" | tr '[:upper:]' '[:lower:]')

GROUP=$(python3 - "$SYSTEM" <<'EOF'
import configparser, sys
cp = configparser.ConfigParser()
cp.read("scope.ini")
sec = f"system:{sys.argv[1]}"
if sec not in cp:
    sys.exit(f"scope.ini has no [system:{sys.argv[1]}]")
print(cp[sec].get("group", "").strip() or sys.argv[1].lower())
EOF
)

SRC_MEDIA="$PACK_DIR/out/media-$STYLE/docs/$SYSTEM"
SRC_DB="$PACK_DIR/out/db/${SYS_LC}_${STYLE}.json.zip"
[ -d "$SRC_MEDIA" ] || { echo "missing $SRC_MEDIA (run assemble?)"; exit 1; }
[ -f "$SRC_DB" ]    || { echo "missing $SRC_DB (run package?)"; exit 1; }

PUB_DIR="$PACK_DIR/../pub-$GROUP"
[ -d "$PUB_DIR/.git" ] || { echo "missing $PUB_DIR (run bootstrap_repos.sh?)"; exit 1; }
cd "$PUB_DIR"
git remote get-url origin | grep -q "artworkdb-$GROUP" \
    || { echo "origin of pub-$GROUP is not artworkdb-$GROUP"; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "pub-$GROUP has local changes; clean it first"; exit 1; }
git fetch -q origin

BR="media-$STYLE"
git rev-parse --verify -q "origin/$BR" >/dev/null \
    || { echo "branch $BR missing on origin (run bootstrap_repos.sh?)"; exit 1; }
git checkout -q "$BR" 2>/dev/null || git checkout -q -t "origin/$BR"
git pull -q --ff-only

if git cat-file -e "origin/$BR:docs/$SYSTEM" 2>/dev/null; then
    TYPE=chore VERB=update
else
    TYPE=feat VERB=publish
fi
rm -rf "docs/$SYSTEM"
mkdir -p docs
cp -r "$SRC_MEDIA" docs/
git add "docs/$SYSTEM"
# --no-renames: a rename emits the old path as a bare field with no status
# prefix, which the filter below would read as a change outside docs/<system>.
FOREIGN=$(git status --porcelain -z --no-renames | tr "\0" "\n" | grep -v "^.. docs/$SYSTEM/" || true)
[ -z "$FOREIGN" ] || { echo "changes outside docs/$SYSTEM; aborted:"; echo "$FOREIGN"; git reset -q; exit 1; }
if git diff --cached --quiet; then
    echo "media: no changes"
else
    N=$(git diff --cached --name-only | wc -l)
    git commit -q -m "$TYPE($SYS_LC): $VERB $STYLE artwork"
    git push -q origin "$BR"
    echo "media: $N file(s) -> $BR"
fi

ZIP=$(basename "$SRC_DB")
git checkout -q db 2>/dev/null || git checkout -q -t origin/db
git pull -q --ff-only
if git cat-file -e "origin/db:$ZIP" 2>/dev/null; then
    TYPE=chore VERB=update
else
    TYPE=feat VERB=publish
fi
cp "$SRC_DB" .
git add "$ZIP"
if git diff --cached --quiet; then
    echo "db: no changes"
else
    git commit -q -m "$TYPE($SYS_LC): $VERB $STYLE database"
    git push -q origin db
    echo "db: $ZIP -> db"
fi

echo "done; once the raw cache expires: python3 build_pack.py verify --system $SYSTEM --style $STYLE"
