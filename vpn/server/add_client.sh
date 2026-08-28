#!/usr/bin/env bash
#
# Добавляет клиента (телефон, ноутбук) к серверу WireGuard и печатает
# готовый конфиг + QR-код.
#
#   sudo bash vpn/server/add_client.sh phone
#
# Переопределяемые переменные окружения:
#   ALLOWED_IPS - что заворачивать в туннель. По умолчанию весь трафик
#                 (0.0.0.0/0, ::/0) — только так внешний IP станет российским.
#   WG_DNS      - DNS-серверы для клиента (по умолчанию из ruvpn.params)
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
[[ -n "$NAME" ]] || die "укажите имя клиента: $0 phone"
[[ "$NAME" =~ ^[a-zA-Z0-9_-]{1,32}$ ]] || die "имя: только латиница, цифры, _ и -"

CONF="${WG_DIR}/${WG_IFACE}.conf"
CLIENT_CONF="${WG_DIR}/clients/${NAME}.conf"
ALLOWED_IPS="${ALLOWED_IPS:-0.0.0.0/0, ::/0}"

grep -q "^### client ${NAME}\$" "$CONF" && die "клиент '${NAME}' уже есть (удалить: remove_client.sh ${NAME})"

# Следующий свободный адрес в подсети туннеля.
BASE4="$(echo "$WG_SUBNET" | cut -d/ -f1 | sed 's/\.[0-9]*$//')"
BASE6="$(echo "$WG_SUBNET6" | cut -d/ -f1)"
NEXT=2
# Считаем и закомментированные строки тоже: у приостановленного клиента
# адрес остаётся за ним, иначе после возобновления будет конфликт.
for octet in $(grep -E '^[[:space:]]*#?[[:space:]]*AllowedIPs' "$CONF" \
        | grep -oE "$(echo "$BASE4" | sed 's/\./\\./g')\.[0-9]+" \
        | awk -F. '{print $4}' | sort -n); do
    (( octet >= NEXT )) && NEXT=$(( octet + 1 ))
done
(( NEXT <= 254 )) || die "свободные адреса в ${WG_SUBNET} кончились"

CLIENT_IPV4="${BASE4}.${NEXT}"
CLIENT_IPV6="${BASE6}${NEXT}"

umask 077
CLIENT_PRIV="$(wg genkey)"
CLIENT_PUB="$(echo "$CLIENT_PRIV" | wg pubkey)"
CLIENT_PSK="$(wg genpsk)"

cat >> "$CONF" <<EOF

### client ${NAME}
[Peer]
PublicKey = ${CLIENT_PUB}
PresharedKey = ${CLIENT_PSK}
AllowedIPs = ${CLIENT_IPV4}/32, ${CLIENT_IPV6}/128
### end ${NAME}
EOF

mkdir -p "${WG_DIR}/clients"
cat > "$CLIENT_CONF" <<EOF
[Interface]
PrivateKey = ${CLIENT_PRIV}
Address = ${CLIENT_IPV4}/32, ${CLIENT_IPV6}/128
DNS = ${WG_DNS}

[Peer]
PublicKey = ${SERVER_PUB}
PresharedKey = ${CLIENT_PSK}
Endpoint = ${WG_ENDPOINT}:${WG_PORT}
AllowedIPs = ${ALLOWED_IPS}
PersistentKeepalive = 25
EOF
chmod 600 "$CLIENT_CONF"

# Применяем на живом интерфейсе, не разрывая соединения остальных клиентов.
wg syncconf "$WG_IFACE" <(wg-quick strip "$WG_IFACE")

echo
echo "Клиент '${NAME}' добавлен: ${CLIENT_IPV4}"
echo "Конфиг: ${CLIENT_CONF}"
echo
if command -v qrencode >/dev/null; then
    echo "QR-код (импорт в приложении WireGuard):"
    qrencode -t ansiutf8 < "$CLIENT_CONF"
fi
cat <<EOF

Забрать конфиг на компьютер:
  scp root@${WG_ENDPOINT}:${CLIENT_CONF} .

Для сборки своего APK положите его в vpn/android/app/src/main/assets/wg.conf
EOF
