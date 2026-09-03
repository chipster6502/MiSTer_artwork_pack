#!/usr/bin/env bash
# Rebuild and republish every system of one style without fetching:
# assemble --prune, package, publish.sh, verify. Stops at the first failure.
#   ./republish_all.sh box2d            the reference style
#   ./republish_all.sh box3d box2d      a style that mirrors box2d (--like)
set -euo pipefail
STYLE=${1:-box2d}
LIKE=${2:-}
cd "$(dirname "$0")"
for SYSTEM in $(grep -oP '^\[system:\K[^\]]+' scope.ini); do
  echo "=== $SYSTEM"
  python3 build_pack.py assemble --system "$SYSTEM" --style "$STYLE" --prune ${LIKE:+--like "$LIKE"}
  python3 build_pack.py package  --system "$SYSTEM" --style "$STYLE"
  ./publish.sh "$SYSTEM" "$STYLE"
  python3 build_pack.py verify   --system "$SYSTEM" --style "$STYLE"
done
python3 pack_index.py | tail -3
