#!/usr/bin/env bash
#
# Ставит на ЭТОМ сервере (Яндекс.Облако) простое TCP-реле для Happ/Xray:
# всё, что приходит на порт реле, молча перекидывается на настоящий
# Xray-сервер (США). Никакой расшифровки/понимания протокола реле не
# делает — просто пересылает байты, поэтому VLESS+Reality проходит через
# него насквозь как есть.
#
# Смысл именно в Яндекс.Облаке: его IP-адреса остаются в «белом списке»
# у российских провайдеров (заблокировать их — значит сломать сами
# сервисы Яндекса), поэтому подключение к нему не режут ни при обычной
# фильтрации, ни в режиме белых списков во время тревоги БПЛА — в отличие
# от прямого подключения к обычному зарубежному VDS.
#
#   sudo bash vpn/server/install_relay.sh 69.40.207.38:443
#
# Переопределяемые переменные окружения:
#   RELAY_PORT - порт, который слушает реле (по умолчанию 443, тот же,
#                что и у настоящего Xray-сервера — чтобы просто заменить
#                адрес в ссылке клиента, ничего больше не меняя)
#
set -euo pipefail

RELAY_PORT="${RELAY_PORT:-443}"
RELAY_DIR="/etc/ruvpn-relay"
PARAMS="${RELAY_DIR}/ruvpn.params"

die() { echo "ОШИБКА: $*" >&2; exit 1; }
info() { echo "==> $*"; }

[[ $EUID -eq 0 ]] || die "запускайте от root (sudo)"
command -v apt-get >/dev/null || die "скрипт рассчитан на Debian/Ubuntu"

TARGET="${1:-}"
[[ -n "$TARGET" ]] || die "укажите адрес настоящего Xray-сервера: $0 <ip>:<порт>"
[[ "$TARGET" =~ ^[0-9a-zA-Z.-]+:[0-9]+$ ]] || die "адрес должен быть вида ip:порт"

info "Устанавливаем socat"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq socat >/dev/null

info "Пишем systemd-юнит"
cat > /etc/systemd/system/ruvpn-relay.service <<EOF
[Unit]
Description=Ru VPN relay (Yandex Cloud -> Xray)
After=network.target

[Service]
ExecStart=/usr/bin/socat TCP4-LISTEN:${RELAY_PORT},fork,reuseaddr TCP4:${TARGET}
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
EOF

mkdir -p "$RELAY_DIR"
cat > "$PARAMS" <<EOF
RELAY_PORT="${RELAY_PORT}"
RELAY_TARGET="${TARGET}"
EOF
chmod 600 "$PARAMS"

if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "^Status: active"; then
    info "Открываем порт в ufw"
    ufw allow "${RELAY_PORT}/tcp" >/dev/null
fi

info "Запускаем реле"
systemctl daemon-reload
systemctl enable --now ruvpn-relay >/dev/null 2>&1
systemctl is-active --quiet ruvpn-relay || die "сервис не поднялся: journalctl -u ruvpn-relay"

RELAY_ENDPOINT="$(curl -4 -s --max-time 10 https://api.ipify.org || true)"

cat <<EOF

Готово.
  Реле слушает : 0.0.0.0:${RELAY_PORT}
  Перекидывает : ${TARGET}
  Внешний адрес: ${RELAY_ENDPOINT}:${RELAY_PORT}

В ссылках vless:// для клиентов замените адрес сервера на ${RELAY_ENDPOINT}
(порт, ключи, sni — всё остальное остаётся как есть, реле просто
перекидывает байты дальше на настоящий Xray-сервер).

Если у Яндекс.Облака есть отдельный firewall/security group в консоли —
не забудьте разрешить там входящий TCP ${RELAY_PORT}.
EOF
