#!/usr/bin/env bash
# Бэкап volume redroid (в т.ч. сохранённая сессия Госключ).
# Поставить в cron: 0 4 * * * /path/to/scripts/backup_redroid.sh
#
# Восстановление в случае проблем:
#   docker compose down
#   docker run --rm -v goskey-redroid-data:/data \
#       -v $(pwd)/backups:/backup alpine \
#       sh -c "rm -rf /data/* && tar xzf /backup/<нужный_файл>.tar.gz -C /"
#   docker compose up -d

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
VOLUME_NAME="${VOLUME_NAME:-goskey-redroid-data}"
KEEP_DAYS="${KEEP_DAYS:-14}"

mkdir -p "$BACKUP_DIR"

STAMP=$(date +%F_%H%M)
OUT="$BACKUP_DIR/redroid-data-$STAMP.tar.gz"

docker run --rm \
    -v "$VOLUME_NAME":/data \
    -v "$(realpath "$BACKUP_DIR")":/backup \
    alpine \
    tar czf "/backup/redroid-data-$STAMP.tar.gz" /data

echo "Бэкап сохранён: $OUT"

find "$BACKUP_DIR" -name 'redroid-data-*.tar.gz' -mtime +"$KEEP_DAYS" -delete
