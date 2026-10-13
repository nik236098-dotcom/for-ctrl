#!/usr/bin/env bash
#
# Приостанавливает клиента Happ: снимается с живого конфига, но UUID
# сохраняется — resume_client_happ.sh вернёт доступ тем же самым ключом.
#
#   sudo bash vpn/server/suspend_client_happ.sh tg123456
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

jq --arg name "$NAME" 'map(if .name == $name then .enabled = false else . end)' \
    "$CLIENTS_DB" > "${CLIENTS_DB}.tmp"
mv "${CLIENTS_DB}.tmp" "$CLIENTS_DB"
chmod 600 "$CLIENTS_DB"

xray_rebuild_and_restart

echo "Клиент '${NAME}' приостановлен."
