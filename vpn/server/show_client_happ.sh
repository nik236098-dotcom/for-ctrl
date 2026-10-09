#!/usr/bin/env bash
#
# Печатает ссылку vless:// существующего клиента Happ в stdout.
# Нужен телеграм-боту: сам бот работает не под root — этот скрипт ему
# разрешён через sudoers.
#
#   sudo bash vpn/server/show_client_happ.sh tg123456
#
set -euo pipefail

die() { echo "ОШИБКА: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "запускайте от root (sudo)"

# shellcheck disable=SC1091
source "$(dirname "$0")/_xray_lib.sh"

NAME="${1:-}"
[[ -n "$NAME" ]] || die "укажите имя клиента"
[[ "$NAME" =~ ^[a-zA-Z0-9_-]{1,32}$ ]] || die "имя: только латиница, цифры, _ и -"

xray_client_uri "$NAME"
