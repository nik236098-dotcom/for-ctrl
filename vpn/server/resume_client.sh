#!/usr/bin/env bash
#
# Возвращает приостановленного клиента: раскомментирует его блок и
# применяет конфиг. Ключ у человека остаётся прежним.
#
#   sudo bash vpn/server/resume_client.sh tg123456
#
set -euo pipefail

WG_DIR="/etc/wireguard"
PARAMS="${WG_DIR}/ruvpn.params"

die() { echo "ОШИБКА: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "запускайте от root (sudo)"
[[ -f "$PARAMS" ]] || die "сервер не настроен, сначала install_wireguard.sh"
# shellcheck disable=SC1090
source "$PARAMS"

NAME="${1:-}"
[[ -n "$NAME" ]] || die "укажите имя клиента"
[[ "$NAME" =~ ^[a-zA-Z0-9_-]{1,32}$ ]] || die "имя: только латиница, цифры, _ и -"

CONF="${WG_DIR}/${WG_IFACE}.conf"
grep -q "^### client ${NAME}\$" "$CONF" || die "клиента '${NAME}' нет"

awk -v name="$NAME" '
    $0 == "### client " name { inblock = 1; print; next }
    inblock && $0 == "### end " name { inblock = 0; print; next }
    inblock && $0 ~ /^#[^#]/ { sub(/^#/, ""); print; next }
    { print }
' "$CONF" > "${CONF}.tmp"
mv "${CONF}.tmp" "$CONF"
chmod 600 "$CONF"

wg syncconf "$WG_IFACE" <(wg-quick strip "$WG_IFACE")

echo "Клиент '${NAME}' снова на связи."
