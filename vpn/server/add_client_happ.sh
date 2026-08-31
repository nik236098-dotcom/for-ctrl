#!/usr/bin/env bash
#
# Добавляет клиента Happ (iPhone) и печатает готовую ссылку vless://.
#
#   sudo bash vpn/server/add_client_happ.sh tg123456
#
set -euo pipefail

die() { echo "ОШИБКА: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "запускайте от root (sudo)"

# shellcheck disable=SC1091
source "$(dirname "$0")/_xray_lib.sh"

NAME="${1:-}"
[[ -n "$NAME" ]] || die "укажите имя клиента: $0 tg123456"
[[ "$NAME" =~ ^[a-zA-Z0-9_-]{1,32}$ ]] || die "имя: только латиница, цифры, _ и -"

jq -e --arg name "$NAME" 'any(.[]; .name == $name) | not' "$CLIENTS_DB" >/dev/null \
    || die "клиент '${NAME}' уже есть (удалить: remove_client_happ.sh ${NAME})"

UUID="$(cat /proc/sys/kernel/random/uuid)"
jq --arg name "$NAME" --arg uuid "$UUID" '. + [{name: $name, uuid: $uuid, enabled: true}]' \
    "$CLIENTS_DB" > "${CLIENTS_DB}.tmp"
mv "${CLIENTS_DB}.tmp" "$CLIENTS_DB"
chmod 600 "$CLIENTS_DB"

xray_rebuild_and_restart

echo
echo "Клиент '${NAME}' добавлен."
echo
echo "Ссылка для Happ (вставить из буфера, отсканировать как QR или открыть как ссылку):"
xray_client_uri "$NAME"
echo
cat <<'MSG'
Важно: сама по себе эта ссылка заводит только прокси — по умолчанию Happ
погонит через него ВЕСЬ трафик. Чтобы российские сайты и банки открывались
напрямую (без прокси), в Happ ещё нужно один раз (общий шаг, не для
каждого пользователя) подключить готовый профиль маршрутизации — см.
vpn/README.md → «iPhone (Happ)».
MSG
