#!/usr/bin/env bash
#
# Готовит ЭТОТ сервер к управлению по SSH из бота, который живёт на другом
# сервере — второй (региональный, например американский) VPN-сервер для
# смены страны в приложении, см. US_SSH_* в vpn/bot/.env.example и
# vpn/README.md → «Второй сервер (США)».
#
#   sudo bash vpn/server/install_remote_helper.sh "ssh-ed25519 AAAA... коммент"
#
# Публичный ключ — второй аргумент (или второй как единственный, в кавычках,
# если в нём пробелы). Берётся с того сервера, где живёт бот:
#
#   sudo -u ruvpnbot ssh-keygen -t ed25519 -f /opt/ruvpn/bot/us_server_key -N ""
#   sudo cat /opt/ruvpn/bot/us_server_key.pub
#
# Что делает этот скрипт:
#   * ставит WireGuard, если ещё не стоит (install_wireguard.sh — у этого
#     сервера свои ключи и свой wg0, с RU-сервером они никак не связаны);
#   * копирует vpn/server сюда же (/opt/ruvpn/server), владелец root — те
#     же пять скриптов, что умеет вызывать локальный бот, только этот
#     сервер вызывают по SSH;
#   * заводит системного пользователя ruvpn-remote без домашнего шелла и
#     добавляет присланный публичный ключ в его authorized_keys;
#   * через sudoers разрешает ему ровно те же команды, что и локальному
#     боту (см. install_bot.sh) — и ничего больше. Самого бота, systemd-
#     сервиса и .env здесь нет — этим сервером управляют только по SSH.
#
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST=/opt/ruvpn
REMOTE_USER=ruvpn-remote

die() { echo "ОШИБКА: $*" >&2; exit 1; }
info() { echo "==> $*"; }

[[ $EUID -eq 0 ]] || die "запускайте от root (sudo)"

PUBKEY="${1:-}"
[[ -n "$PUBKEY" ]] || die "нужен публичный ключ первым аргументом — см. комментарий в начале файла"
[[ "$PUBKEY" == ssh-* ]] || die "это не похоже на публичный ключ SSH (должен начинаться с ssh-...)"

if [[ ! -f /etc/wireguard/ruvpn.params ]]; then
    info "WireGuard ещё не стоит — ставлю (install_wireguard.sh)"
    bash "$SRC/server/install_wireguard.sh"
fi
# shellcheck disable=SC1091
source /etc/wireguard/ruvpn.params

info "Копируем скрипты в ${DEST}/server"
mkdir -p "$DEST"
cp -r "$SRC/server" "$DEST/"
chown -R root:root "$DEST/server"
chmod 755 "$DEST/server" "$DEST"/server/*.sh

info "Заводим пользователя ${REMOTE_USER}"
id -u "$REMOTE_USER" >/dev/null 2>&1 || useradd --system --create-home \
    --shell /usr/sbin/nologin "$REMOTE_USER"

HOME_DIR="$(getent passwd "$REMOTE_USER" | cut -d: -f6)"
mkdir -p "$HOME_DIR/.ssh"
if ! grep -qxF "$PUBKEY" "$HOME_DIR/.ssh/authorized_keys" 2>/dev/null; then
    echo "$PUBKEY" >> "$HOME_DIR/.ssh/authorized_keys"
fi
chown -R "$REMOTE_USER:$REMOTE_USER" "$HOME_DIR/.ssh"
chmod 700 "$HOME_DIR/.ssh"
chmod 600 "$HOME_DIR/.ssh/authorized_keys"

info "Права sudo (ровно на нужные команды)"
WG_BIN="$(command -v wg)"
cat > /etc/sudoers.d/ruvpn-remote <<EOF
${REMOTE_USER} ALL=(root) NOPASSWD: ${DEST}/server/add_client.sh, \\
    ${DEST}/server/show_client.sh, \\
    ${DEST}/server/suspend_client.sh, \\
    ${DEST}/server/resume_client.sh, \\
    ${DEST}/server/remove_client.sh, \\
    ${WG_BIN} show ${WG_IFACE} allowed-ips, \\
    ${WG_BIN} show ${WG_IFACE} transfer
EOF
chmod 440 /etc/sudoers.d/ruvpn-remote
visudo -c -q -f /etc/sudoers.d/ruvpn-remote || {
    rm -f /etc/sudoers.d/ruvpn-remote
    die "sudoers не прошёл проверку, файл удалён"
}

cat <<EOF

Готово. На сервере с ботом (RU) впишите в /opt/ruvpn/bot/.env:

  US_SSH_HOST=$(curl -s -4 ifconfig.me || echo "<ip этого сервера>")
  US_SSH_USER=${REMOTE_USER}
  US_SSH_KEY_PATH=/opt/ruvpn/bot/us_server_key
  US_SCRIPTS_DIR=${DEST}/server
  US_WG_IFACE=${WG_IFACE}

и перезапустите бота: sudo systemctl restart ruvpn-bot

Проверить с сервера бота:
  sudo -u ruvpnbot ssh -i /opt/ruvpn/bot/us_server_key -o BatchMode=yes \\
    ${REMOTE_USER}@<ip этого сервера> sudo -n ${DEST}/server/add_client.sh test-us
EOF
