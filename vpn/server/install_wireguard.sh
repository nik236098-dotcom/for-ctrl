#!/usr/bin/env bash
#
# Поднимает WireGuard-сервер на VDS. Запускать НА СЕРВЕРЕ, который стоит
# в России, — именно его IP увидят сайты, когда телефон подключится.
#
#   sudo bash vpn/server/install_wireguard.sh
#
# Переопределяемые переменные окружения:
#   WG_PORT      - UDP-порт (по умолчанию 51820)
#   WG_SUBNET    - подсеть туннеля IPv4 (по умолчанию 10.13.13.0/24)
#   WG_DNS       - DNS для клиентов (по умолчанию Яндекс.DNS)
#   WG_ENDPOINT  - внешний адрес сервера, если автоопределение промахнулось
#
set -euo pipefail

WG_IFACE="${WG_IFACE:-wg0}"
WG_PORT="${WG_PORT:-51820}"
WG_SUBNET="${WG_SUBNET:-10.13.13.0/24}"
WG_SUBNET6="${WG_SUBNET6:-fd42:13:13::/64}"
WG_DNS="${WG_DNS:-77.88.8.8, 77.88.8.1}"
WG_DIR="/etc/wireguard"
PARAMS="${WG_DIR}/ruvpn.params"

die() { echo "ОШИБКА: $*" >&2; exit 1; }
info() { echo "==> $*"; }

[[ $EUID -eq 0 ]] || die "запускайте от root (sudo)"
command -v apt-get >/dev/null || die "скрипт рассчитан на Debian/Ubuntu"

if [[ -f "$PARAMS" ]] && [[ "${FORCE:-0}" != "1" ]]; then
    die "$PARAMS уже существует — сервер настроен. Клиента добавляйте через add_client.sh, переустановка: FORCE=1 $0"
fi

info "Устанавливаем пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq wireguard iptables qrencode curl >/dev/null

info "Включаем маршрутизацию пакетов"
cat > /etc/sysctl.d/99-wireguard.conf <<EOF
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
EOF
sysctl -q --system

# Внешний интерфейс, через который сервер ходит в интернет.
WAN_IFACE="$(ip -4 route show default | awk '{print $5; exit}')"
[[ -n "$WAN_IFACE" ]] || die "не удалось определить внешний интерфейс"

# Внешний адрес сервера — то, что попадёт в Endpoint клиента.
if [[ -z "${WG_ENDPOINT:-}" ]]; then
    WG_ENDPOINT="$(curl -4 -s --max-time 10 https://api.ipify.org || true)"
fi
if [[ -z "$WG_ENDPOINT" ]]; then
    WG_ENDPOINT="$(ip -4 addr show "$WAN_IFACE" | awk '/inet /{print $2; exit}' | cut -d/ -f1)"
fi
[[ -n "$WG_ENDPOINT" ]] || die "не удалось определить внешний IP, задайте WG_ENDPOINT=..."

SERVER_IPV4="$(echo "$WG_SUBNET" | cut -d/ -f1 | sed 's/0$/1/')"
SERVER_IPV6="$(echo "$WG_SUBNET6" | cut -d/ -f1)1"
PREFIX_LEN="$(echo "$WG_SUBNET" | cut -d/ -f2)"
PREFIX_LEN6="$(echo "$WG_SUBNET6" | cut -d/ -f2)"

info "Генерируем ключи сервера"
mkdir -p "$WG_DIR/clients"
chmod 700 "$WG_DIR"
umask 077
SERVER_PRIV="$(wg genkey)"
SERVER_PUB="$(echo "$SERVER_PRIV" | wg pubkey)"

info "Пишем $WG_DIR/$WG_IFACE.conf"
cat > "$WG_DIR/$WG_IFACE.conf" <<EOF
[Interface]
Address = ${SERVER_IPV4}/${PREFIX_LEN}, ${SERVER_IPV6}/${PREFIX_LEN6}
ListenPort = ${WG_PORT}
PrivateKey = ${SERVER_PRIV}

# NAT: трафик клиентов уходит в интернет с адреса сервера.
PostUp = iptables -t nat -A POSTROUTING -s ${WG_SUBNET} -o ${WAN_IFACE} -j MASQUERADE
PostUp = iptables -A FORWARD -i %i -j ACCEPT
PostUp = iptables -A FORWARD -o %i -j ACCEPT
PostUp = ip6tables -t nat -A POSTROUTING -s ${WG_SUBNET6} -o ${WAN_IFACE} -j MASQUERADE || true
PostUp = ip6tables -A FORWARD -i %i -j ACCEPT || true
PostUp = ip6tables -A FORWARD -o %i -j ACCEPT || true
PostDown = iptables -t nat -D POSTROUTING -s ${WG_SUBNET} -o ${WAN_IFACE} -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT
PostDown = iptables -D FORWARD -o %i -j ACCEPT
PostDown = ip6tables -t nat -D POSTROUTING -s ${WG_SUBNET6} -o ${WAN_IFACE} -j MASQUERADE || true
PostDown = ip6tables -D FORWARD -i %i -j ACCEPT || true
PostDown = ip6tables -D FORWARD -o %i -j ACCEPT || true
EOF
chmod 600 "$WG_DIR/$WG_IFACE.conf"

cat > "$PARAMS" <<EOF
WG_IFACE=${WG_IFACE}
WG_PORT=${WG_PORT}
WG_SUBNET=${WG_SUBNET}
WG_SUBNET6=${WG_SUBNET6}
WG_DNS=${WG_DNS}
WG_ENDPOINT=${WG_ENDPOINT}
WAN_IFACE=${WAN_IFACE}
SERVER_PUB=${SERVER_PUB}
EOF
chmod 600 "$PARAMS"

if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "^Status: active"; then
    info "Открываем порт в ufw"
    ufw allow "${WG_PORT}/udp" >/dev/null
fi

info "Запускаем wg-quick@${WG_IFACE}"
systemctl enable --now "wg-quick@${WG_IFACE}" >/dev/null 2>&1
systemctl is-active --quiet "wg-quick@${WG_IFACE}" || die "сервис не поднялся: journalctl -u wg-quick@${WG_IFACE}"

cat <<EOF

Готово.
  Внешний адрес : ${WG_ENDPOINT}:${WG_PORT}/udp
  Интерфейс     : ${WG_IFACE} (${SERVER_IPV4})
  Публичный ключ: ${SERVER_PUB}

Дальше — клиент для телефона:
  sudo bash $(dirname "$0")/add_client.sh phone

Если у провайдера/облака есть внешний firewall (security group) —
не забудьте разрешить в нём UDP ${WG_PORT}.
EOF
