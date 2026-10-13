#!/usr/bin/env bash
#
# Поднимает Xray (VLESS + Reality) на этом сервере — отдельный проект под
# iPhone через приложение Happ (https://www.happ.su/), не связанный с
# WireGuard. Happ понимает только VLESS/VMess/Trojan/Shadowsocks/SOCKS/
# Hysteria2, WireGuard он не умеет — поэтому это второй, независимый
# сервис рядом с уже работающим wg0 на этом же сервере, не замена ему.
#
# Запускать НА американском сервере — только он нужен для этой задачи:
# у пользователей уже есть свой обычный (российский) интернет для рунета,
# через Happ им нужно попадать только на зарубежные сервисы.
#
#   sudo bash vpn/server/install_xray.sh
#
# Переопределяемые переменные окружения:
#   HAPP_PORT     - TCP-порт (по умолчанию 443 — Reality маскируется под
#                   обычный HTTPS, поэтому стандартный порт важен)
#   REALITY_DEST  - настоящий сайт, под который маскируется хендшейк
#                   (по умолчанию www.cloudflare.com:443) — Reality вместо
#                   себя показывает цензору подлинный TLS-сертификат этого
#                   сайта, поэтому годится только реальный, живой TLS 1.3
#                   сайт с поддержкой HTTP/2, не подставной адрес.
#                   НЕ www.microsoft.com: проверено вживую — его сертификат
#                   сейчас весит 8273 байта, а в самой библиотеке Reality
#                   зашит жёсткий лимит буфера 8192 байт — handshake ломается
#                   у всех клиентов всегда, это не проблема настройки
#                   (см. github.com/XTLS/Xray-core issues #6356, #6402).
#
set -euo pipefail

XRAY_DIR="/usr/local/etc/xray"
PARAMS="${XRAY_DIR}/ruvpn.params"
CLIENTS_DB="${XRAY_DIR}/ruvpn-clients.json"

HAPP_PORT="${HAPP_PORT:-443}"
REALITY_DEST="${REALITY_DEST:-www.cloudflare.com:443}"
REALITY_SERVER_NAME="${REALITY_DEST%:*}"

die() { echo "ОШИБКА: $*" >&2; exit 1; }
info() { echo "==> $*"; }

[[ $EUID -eq 0 ]] || die "запускайте от root (sudo)"
command -v apt-get >/dev/null || die "скрипт рассчитан на Debian/Ubuntu"

if [[ -f "$PARAMS" ]] && [[ "${FORCE:-0}" != "1" ]]; then
    die "$PARAMS уже существует — Xray настроен. Клиента добавляйте через add_client_happ.sh, переустановка: FORCE=1 $0"
fi

info "Устанавливаем jq (для управления списком клиентов) и curl"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq jq curl >/dev/null

info "Ставим Xray-core (официальный установщик проекта)"
bash -c "$(curl -L https://raw.githubusercontent.com/XTLS/Xray-install/main/install-release.sh)" @ install
command -v xray >/dev/null || die "xray не установился"

# Официальный юнит запускает xray от пользователя nobody — а config.json
# ниже мы пишем в режиме 600 (только root), потому что там лежит приватный
# ключ Reality. Nobody физически не может его прочитать — служба падает в
# цикле с "permission denied" (проверено вживую). Проще и надёжнее не
# подбирать права на файлы под nobody, а просто запускать xray от root —
# override, а не правка самого юнита, чтобы это работало вне зависимости от
# того, что именно написал официальный установщик.
info "Правим systemd-юнит: xray от root (иначе не читает свой же config.json)"
mkdir -p /etc/systemd/system/xray.service.d
cat > /etc/systemd/system/xray.service.d/override.conf <<'EOF'
[Service]
User=root
Group=root
EOF

info "Генерируем ключи Reality"
# Формат вывода `xray x25519` менялся между версиями — старые пишут
# "Private key:"/"Public key:", новые (25.3.6+) "PrivateKey:"/"Password:", а в
# 26.3.27 (проверено вживую) и вовсе "Password (PublicKey):" — между словом и
# двоеточием ещё текст в скобках. Поэтому не требуем двоеточие сразу после
# слова: матчим по началу строки, а всё до первого ":" просто отрезаем —
# так переживёт и следующее переименование, если оно снова добавит суффикс.
X25519_OUT="$(xray x25519)"
REALITY_PRIVATE_KEY="$(echo "$X25519_OUT" | grep -iE '^private' | head -1 | sed -E 's/^[^:]+:[[:space:]]*//')"
REALITY_PUBLIC_KEY="$(echo "$X25519_OUT" | grep -iE '^(password|public)' | head -1 | sed -E 's/^[^:]+:[[:space:]]*//')"
[[ -n "$REALITY_PRIVATE_KEY" && -n "$REALITY_PUBLIC_KEY" ]] || die "не удалось разобрать вывод xray x25519:\n${X25519_OUT}"

REALITY_SHORT_ID="$(openssl rand -hex 8)"

# Внешний адрес сервера — то, что попадёт в ссылку vless:// клиента.
if [[ -z "${HAPP_ENDPOINT:-}" ]]; then
    HAPP_ENDPOINT="$(curl -4 -s --max-time 10 https://api.ipify.org || true)"
fi
[[ -n "$HAPP_ENDPOINT" ]] || die "не удалось определить внешний IP, задайте HAPP_ENDPOINT=..."

info "Пишем $XRAY_DIR/config.json"
mkdir -p "$XRAY_DIR"
umask 077
echo "[]" > "$CLIENTS_DB"

cat > "${XRAY_DIR}/config.json" <<EOF
{
  "log": { "loglevel": "warning" },
  "api": {
    "tag": "api",
    "listen": "127.0.0.1:10085",
    "services": ["StatsService"]
  },
  "stats": {},
  "policy": {
    "levels": {
      "0": { "statsUserUplink": true, "statsUserDownlink": true }
    }
  },
  "inbounds": [
    {
      "listen": "0.0.0.0",
      "port": ${HAPP_PORT},
      "protocol": "vless",
      "settings": {
        "clients": [],
        "decryption": "none"
      },
      "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
          "show": false,
          "dest": "${REALITY_DEST}",
          "xver": 0,
          "serverNames": ["${REALITY_SERVER_NAME}"],
          "privateKey": "${REALITY_PRIVATE_KEY}",
          "shortIds": ["${REALITY_SHORT_ID}"]
        }
      },
      "sniffing": {
        "enabled": true,
        "destOverride": ["http", "tls", "quic"]
      }
    }
  ],
  "outbounds": [
    { "protocol": "freedom", "tag": "direct" },
    { "protocol": "blackhole", "tag": "blocked" }
  ]
}
EOF
chmod 600 "${XRAY_DIR}/config.json" "$CLIENTS_DB"

cat > "$PARAMS" <<EOF
HAPP_PORT="${HAPP_PORT}"
HAPP_ENDPOINT="${HAPP_ENDPOINT}"
REALITY_DEST="${REALITY_DEST}"
REALITY_SERVER_NAME="${REALITY_SERVER_NAME}"
REALITY_PUBLIC_KEY="${REALITY_PUBLIC_KEY}"
REALITY_SHORT_ID="${REALITY_SHORT_ID}"
EOF
chmod 600 "$PARAMS"

if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "^Status: active"; then
    info "Открываем порт в ufw"
    ufw allow "${HAPP_PORT}/tcp" >/dev/null
fi

info "Запускаем xray"
systemctl daemon-reload
systemctl enable --now xray >/dev/null 2>&1
systemctl is-active --quiet xray || die "сервис не поднялся: journalctl -u xray"

cat <<EOF

Готово.
  Внешний адрес : ${HAPP_ENDPOINT}:${HAPP_PORT}
  Маскировка    : ${REALITY_DEST}
  Публичный ключ: ${REALITY_PUBLIC_KEY}

Дальше — ключ для конкретного пользователя (iPhone/Happ):
  sudo bash $(dirname "$0")/add_client_happ.sh tg123456

Если у провайдера/облака есть внешний firewall (security group) —
не забудьте разрешить в нём TCP ${HAPP_PORT}.
EOF
