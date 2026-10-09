#!/usr/bin/env bash
#
# Возвращает приостановленного клиента Happ. UUID у человека остаётся прежним.
#
#   sudo bash vpn/server/resume_client_happ.sh tg123456
#
set -euo pipefail

die() { echo "ОШИБКА: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "запускайте от root (sudo)"

# shellcheck disable=SC1091
source "$(dirname "$0")/_xray_lib.sh"

NAME="${1:-}"
[[ -n "$NAME" ]] || die "укажите имя клиента"
[[ "$NAME" =~ ^[a-zA-Z0-9_-]{1,32}$ ]] || die "имя: только латиница, цифры, _ и -"

jq -e --arg name "$NAME" 'any(.[]; .name == $name)' "$CLIENTS_DB" >/dev/null \
    || die "клиента '${NAME}' нет"

jq --arg name "$NAME" 'map(if .name == $name then .enabled = true else . end)' \
    "$CLIENTS_DB" > "${CLIENTS_DB}.tmp"
mv "${CLIENTS_DB}.tmp" "$CLIENTS_DB"
chmod 600 "$CLIENTS_DB"

xray_rebuild_and_restart

echo "Клиент '${NAME}' снова на связи."
