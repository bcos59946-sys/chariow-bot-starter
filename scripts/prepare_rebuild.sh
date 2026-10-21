#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

# Usage: bash scripts/prepare_rebuild.sh [existing-project-dir]
# Safe migration: back up first, then move the old bot script into a timestamped quarantine.
# This script intentionally does NOT delete config.json, .env, data.json, keys, backups, or databases.

PROJECT_DIR="${1:-$HOME/cei_bot}"
STAMP="$(date +%Y%m%d-%H%M%S)"
if [ ! -d "$PROJECT_DIR" ]; then
  echo "Dossier introuvable : $PROJECT_DIR"
  echo "Aucun fichier modifié."
  exit 1
fi

BACKUP_DIR="$PROJECT_DIR/rebuild-backup-$STAMP"
mkdir -p "$BACKUP_DIR"
for item in bot.py config.json data.json .env .env.local; do
  if [ -f "$PROJECT_DIR/$item" ]; then
    cp -p "$PROJECT_DIR/$item" "$BACKUP_DIR/"
  fi
done
if [ -d "$PROJECT_DIR/.venv" ]; then
  echo "Environnement virtuel conservé (non copié)."
fi

if [ -f "$PROJECT_DIR/bot.py" ]; then
  mkdir -p "$PROJECT_DIR/legacy-quarantine-$STAMP"
  mv "$PROJECT_DIR/bot.py" "$PROJECT_DIR/legacy-quarantine-$STAMP/bot.py"
  echo "Ancien bot.py mis de côté dans : $PROJECT_DIR/legacy-quarantine-$STAMP/"
fi

echo
echo "Sauvegarde créée : $BACKUP_DIR"
echo "IMPORTANT : config.json, data.json, .env, les clés et les sauvegardes n'ont pas été supprimés."
echo "Audite le nouveau ZIP avant de remplacer/relier les configurations."
