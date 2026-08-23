#!/usr/bin/env bash
# Бэкап volume(ов) redroid (в т.ч. сохранённая сессия Госключ).
# По умолчанию бэкапит оба аккаунта — personal и ip.
# Поставить в cron: 0 4 * * * /path/to/scripts/backup_redroid.sh
#
# Только один аккаунт: VOLUME_NAMES="goskey-redroid-data-personal" bash scripts/backup_redroid.sh
#
# Восстановление в случае проблем (пример для personal):
#   docker compose stop redroid-personal
#   docker run --rm -v goskey-redroid-data-personal:/data \
#       -v $(pwd)/backups:/backup alpine \
#       sh -c "rm -rf /data/* && tar xzf /backup/<нужный_файл>.tar.gz -C /"
#   docker compose up -d redroid-personal

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
VOLUME_NAMES="${VOLUME_NAMES:-goskey-redroid-data-personal goskey-redroid-data-ip}"
KEEP_DAYS="${KEEP_DAYS:-14}"

mkdir -p "$BACKUP_DIR"
STAMP=$(date +%F_%H%M)

for VOLUME_NAME in $VOLUME_NAMES; do
    if ! docker volume inspect "$VOLUME_NAME" >/dev/null 2>&1; then
        echo "Пропускаю $VOLUME_NAME — такого volume нет (аккаунт не поднят?)"
        continue
    fi

    OUT="$BACKUP_DIR/${VOLUME_NAME}-$STAMP.tar.gz"
    docker run --rm \
        -v "$VOLUME_NAME":/data \
        -v "$(realpath "$BACKUP_DIR")":/backup \
        alpine \
        tar czf "/backup/${VOLUME_NAME}-$STAMP.tar.gz" /data

    echo "Бэкап сохранён: $OUT"
done

find "$BACKUP_DIR" -name '*-????-??-??_????.tar.gz' -mtime +"$KEEP_DAYS" -delete
