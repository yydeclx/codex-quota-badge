#!/bin/sh
set -eu
SOURCE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
TARGET_DIR=$1
TMP=$(mktemp -d)
BACKUP="$TARGET_DIR/.update-backups/$(date +%Y%m%d-%H%M%S)"
rollback() {
  code=$?
  if [ "$code" -ne 0 ] && [ -d "$BACKUP" ]; then
    for f in "$BACKUP"/* "$BACKUP"/.[!.]*; do
      [ -e "$f" ] || continue
      base=$(basename "$f")
      rm -rf "$TARGET_DIR/$base"
      cp -R "$f" "$TARGET_DIR/$base"
    done
  fi
  rm -rf "$TMP"
  exit "$code"
}
trap rollback EXIT INT TERM
python3 -m py_compile "$SOURCE_DIR"/*.py
cp "$SOURCE_DIR"/*.py "$SOURCE_DIR"/*.sh "$TMP"/
cp "$SOURCE_DIR"/README.md "$SOURCE_DIR"/PORTABLE-INSTALL.md "$SOURCE_DIR"/package-manifest.json "$TMP"/
cp -R "$SOURCE_DIR"/.codex-plugin "$TMP"/.codex-plugin
cp -R "$SOURCE_DIR"/skills "$TMP"/skills
mkdir -p "$BACKUP"
for f in "$TMP"/* "$TMP"/.[!.]*; do
  [ -e "$f" ] || continue
  base=$(basename "$f")
  if [ -e "$TARGET_DIR/$base" ]; then cp -R "$TARGET_DIR/$base" "$BACKUP/$base"; fi
done
for f in "$TMP"/* "$TMP"/.[!.]*; do
  [ -e "$f" ] || continue
  base=$(basename "$f")
  rm -rf "$TARGET_DIR/$base"
  cp -R "$f" "$TARGET_DIR/$base"
done
python3 -m py_compile "$TARGET_DIR"/*.py
trap - EXIT INT TERM
rm -rf "$TMP"
nohup "$TARGET_DIR/start-mac.sh" >/dev/null 2>&1 &
echo "macOS update completed; backup: $BACKUP"
