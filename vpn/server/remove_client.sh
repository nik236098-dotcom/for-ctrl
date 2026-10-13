#!/usr/bin/env bash
#
# Удаляет клиента с сервера WireGuard.
#
#   sudo bash vpn/server/remove_client.sh phone
#   sudo bash vpn/server/remove_client.sh --list
#
set -euo pipefail

WG_DIR="/etc/wireguard"
PARAMS="${WG_DIR}/ruvpn.params"

die() { echo "ОШИБКА: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "запускайте от root (sudo)"
[[ -f "$PARAMS" ]] || die "сервер не настроен, сначала install_wireguard.sh"
# shellcheck disable=SC1090
source "$PARAMS"

CONF="${WG_DIR}/${WG_IFACE}.conf"

if [[ "${1:-}" == "--list" || -z "${1:-}" ]]; then
    echo "Клиенты:"
    grep -oP '^### client \K.*' "$CONF" || echo "  (пусто)"
    [[ "${1:-}" == "--list" ]] && exit 0
    die "укажите имя клиента: $0 phone"
fi

NAME="$1"
grep -q "^### client ${NAME}\$" "$CONF" || die "клиента '${NAME}' нет"

# Вырезаем блок между маркерами вместе с пустой строкой перед ним.
awk -v name="$NAME" '
    $0 == "### client " name { inblock = 1; next }
    inblock && $0 == "### end " name { inblock = 0; next }
    !inblock { print }
' "$CONF" > "${CONF}.tmp"
mv "${CONF}.tmp" "$CONF"
chmod 600 "$CONF"

rm -f "${WG_DIR}/clients/${NAME}.conf"

wg syncconf "$WG_IFACE" <(wg-quick strip "$WG_IFACE")

echo "Клиент '${NAME}' удалён."
