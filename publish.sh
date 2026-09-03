#!/usr/bin/env bash
set -euo pipefail

[ $# -eq 2 ] || { echo "uso: publish.sh <Sistema> <estilo>"; exit 1; }
SYSTEM=$1 STYLE=$2
[[ "$SYSTEM" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "sistema invalido: $SYSTEM"; exit 1; }
[[ "$STYLE" =~ ^[a-z0-9]+$ ]] || { echo "estilo invalido: $STYLE"; exit 1; }

PACK_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PACK_DIR"
SYS_LC=$(echo "$SYSTEM" | tr '[:upper:]' '[:lower:]')

GROUP=$(python3 - "$SYSTEM" <<'EOF'
import configparser, sys
cp = configparser.ConfigParser()
cp.read("scope.ini")
sec = f"system:{sys.argv[1]}"
if sec not in cp:
    sys.exit(f"no hay [system:{sys.argv[1]}] en scope.ini")
print(cp[sec].get("group", "").strip() or sys.argv[1].lower())
EOF
)

SRC_MEDIA="$PACK_DIR/out/media-$STYLE/docs/$SYSTEM"
SRC_DB="$PACK_DIR/out/db/${SYS_LC}_${STYLE}.json.zip"
[ -d "$SRC_MEDIA" ] || { echo "falta $SRC_MEDIA (assemble?)"; exit 1; }
[ -f "$SRC_DB" ]    || { echo "falta $SRC_DB (package?)"; exit 1; }

PUB_DIR="$PACK_DIR/../pub-$GROUP"
[ -d "$PUB_DIR/.git" ] || { echo "falta $PUB_DIR (bootstrap_repos.sh?)"; exit 1; }
cd "$PUB_DIR"
git remote get-url origin | grep -q "artworkdb-$GROUP" \
    || { echo "origin de pub-$GROUP no es artworkdb-$GROUP"; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "pub-$GROUP sucio; limpialo antes"; exit 1; }
git fetch -q origin

BR="media-$STYLE"
git rev-parse --verify -q "origin/$BR" >/dev/null \
    || { echo "rama $BR no existe en origin (bootstrap_repos.sh?)"; exit 1; }
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
[ -z "$FOREIGN" ] || { echo "cambios fuera de docs/$SYSTEM; abortado:"; echo "$FOREIGN"; git reset -q; exit 1; }
if git diff --cached --quiet; then
    echo "media: sin cambios"
else
    N=$(git diff --cached --name-only | wc -l)
    git commit -q -m "$TYPE($SYS_LC): $VERB $STYLE artwork"
    git push -q origin "$BR"
    echo "media: $N fichero(s) -> $BR"
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
    echo "db: sin cambios"
else
    git commit -q -m "$TYPE($SYS_LC): $VERB $STYLE database"
    git push -q origin db
    echo "db: $ZIP -> db"
fi

echo "hecho; tras unos minutos de cache: python3 build_pack.py verify --system $SYSTEM --style $STYLE"
