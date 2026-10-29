#!/bin/sh
set -eu
SOURCE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
[ "$#" -eq 1 ] || { echo 'usage: update-mac.sh INSTALL_DIR' >&2; exit 2; }
TARGET_DIR="$(CDPATH= cd -- "$1" && pwd)"
TMP=$(mktemp -d)
STAGE="$TMP/stage"
BACKUP="$TARGET_DIR/.update-backups/$(date +%Y%m%d-%H%M%S)-$$"
CHANGED=0
WAS_RUNNING=0
rollback() {
  code=$?
  trap - EXIT INT TERM
  if [ "$code" -ne 0 ] && [ "$CHANGED" -eq 1 ]; then
    while IFS= read -r base; do
      rm -rf "$TARGET_DIR/$base"
      if [ -e "$BACKUP/$base" ]; then cp -R "$BACKUP/$base" "$TARGET_DIR/$base"; fi
    done < "$TMP/entries"
    if [ "$WAS_RUNNING" -eq 1 ] && [ -f "$TARGET_DIR/start-mac.sh" ]; then
      nohup sh "$TARGET_DIR/start-mac.sh" >/dev/null 2>&1 &
    fi
  fi
  rm -rf "$TMP"
  exit "$code"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir -p "$STAGE"
python3 -m py_compile "$SOURCE_DIR"/*.py
cp "$SOURCE_DIR"/*.py "$SOURCE_DIR"/*.sh "$STAGE"/
cp "$SOURCE_DIR"/README.md "$SOURCE_DIR"/PORTABLE-INSTALL.md "$SOURCE_DIR"/package-manifest.json "$STAGE"/
cp -R "$SOURCE_DIR"/.codex-plugin "$STAGE"/.codex-plugin
cp -R "$SOURCE_DIR"/skills "$STAGE"/skills
: > "$TMP/entries"
mkdir -p "$BACKUP"
for f in "$STAGE"/* "$STAGE"/.[!.]*; do
  [ -e "$f" ] || continue
  base=$(basename "$f")
  printf '%s\n' "$base" >> "$TMP/entries"
  if [ -e "$TARGET_DIR/$base" ]; then cp -R "$TARGET_DIR/$base" "$BACKUP/$base"; fi
done
STOP_RESULT=$(python3 "$SOURCE_DIR/mac_lifecycle.py" stop "$TARGET_DIR")
[ "$STOP_RESULT" = 'was_running=1' ] && WAS_RUNNING=1
CHANGED=1
while IFS= read -r base; do
  rm -rf "$TARGET_DIR/$base"
  cp -R "$STAGE/$base" "$TARGET_DIR/$base"
done < "$TMP/entries"
python3 -m py_compile "$TARGET_DIR"/*.py
nohup sh "$TARGET_DIR/start-mac.sh" >/dev/null 2>&1 &
trap - EXIT INT TERM
rm -rf "$TMP"
echo "macOS update completed; backup: $BACKUP"
