#!/usr/bin/env bash
#
# Печатает конфиг существующего клиента в stdout.
# Нужен телеграм-боту: сам бот работает не под root и в /etc/wireguard
# не заглядывает, а этот скрипт ему разрешён через sudoers.
#
#   sudo bash vpn/server/show_client.sh phone
#
set -euo pipefail

WG_DIR="/etc/wireguard"

die() { echo "ОШИБКА: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "запускайте от root (sudo)"

NAME="${1:-}"
[[ -n "$NAME" ]] || die "укажите имя клиента"
[[ "$NAME" =~ ^[a-zA-Z0-9_-]{1,32}$ ]] || die "имя: только латиница, цифры, _ и -"

CLIENT_CONF="${WG_DIR}/clients/${NAME}.conf"
[[ -f "$CLIENT_CONF" ]] || die "конфига клиента '${NAME}' нет"

cat "$CLIENT_CONF"
