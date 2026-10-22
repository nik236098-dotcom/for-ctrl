#!/usr/bin/env bash
#
# Включает на этом сервере регистрацию устройств без ключей (provision.py):
# приложения Ru VPN подключаются одной кнопкой, без бота и без ключей.
# Запускать на сервере, где уже стоит WireGuard (install_wireguard.sh).
#
#   sudo bash vpn/server/install_provision.sh
#
# Переопределяемые переменные окружения:
#   PROVISION_PORT         TCP-порт (по умолчанию 8082)
#   PROVISION_MAX_DEVICES  сколько устройств можно зарегистрировать (50)
#
set -euo pipefail

PARAMS="/etc/wireguard/ruvpn.params"
ENV_FILE="/etc/ruvpn-provision.env"
UNIT="/etc/systemd/system/ruvpn-provision.service"
SCRIPT="$(readlink -f "$(dirname "$0")/provision.py")"
PORT="${PROVISION_PORT:-8082}"
MAX_DEVICES="${PROVISION_MAX_DEVICES:-50}"

die() { echo "ОШИБКА: $*" >&2; exit 1; }
info() { echo "==> $*"; }

[[ $EUID -eq 0 ]] || die "запускайте от root (sudo)"
[[ -f "$PARAMS" ]] || die "WireGuard не настроен, сначала install_wireguard.sh"
[[ -f "$SCRIPT" ]] || die "не найден $SCRIPT"

if ! command -v python3 >/dev/null; then
    info "Ставим python3"
    apt-get update -qq && apt-get install -y -qq python3 >/dev/null
fi

info "Пишем $ENV_FILE"
cat > "$ENV_FILE" <<EOF
PROVISION_PORT=${PORT}
PROVISION_MAX_DEVICES=${MAX_DEVICES}
RUVPN_PARAMS=${PARAMS}
EOF
chmod 600 "$ENV_FILE"

info "Пишем службу ruvpn-provision"
cat > "$UNIT" <<EOF
[Unit]
Description=Ru VPN: регистрация устройств без ключей
After=network-online.target wg-quick@wg0.service
Wants=network-online.target

[Service]
EnvironmentFile=${ENV_FILE}
ExecStart=/usr/bin/env python3 ${SCRIPT}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "^Status: active"; then
    info "Открываем порт ${PORT}/tcp в ufw"
    ufw allow "${PORT}/tcp" >/dev/null
fi

systemctl daemon-reload
systemctl enable ruvpn-provision >/dev/null 2>&1
systemctl restart ruvpn-provision
sleep 2
systemctl is-active --quiet ruvpn-provision || die "служба не поднялась: journalctl -u ruvpn-provision -n 30"
curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null || die "служба не отвечает на порту ${PORT}"

cat <<EOF

Готово: приложения Ru VPN теперь подключаются к этому серверу без ключей.
  Порт регистрации: ${PORT}/tcp
  Мест для устройств: ${MAX_DEVICES}

Устройства видны в списке клиентов с именами app-…:
  bash $(dirname "$SCRIPT")/remove_client.sh --list

Если у хостинга есть внешний firewall — разрешите в нём TCP ${PORT}
(и UDP 51820 для самого VPN).
EOF
