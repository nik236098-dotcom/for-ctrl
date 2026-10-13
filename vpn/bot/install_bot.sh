#!/usr/bin/env bash
#
# Ставит телеграм-бота выдачи ключей на тот же сервер, где живёт WireGuard.
#
#   sudo bash vpn/bot/install_bot.sh
#
# Что делает:
#   * копирует vpn/server и vpn/bot в /opt/ruvpn (владелец root — чтобы
#     никто, кроме root, не мог подменить скрипты, которые бот зовёт с sudo);
#   * заводит системного пользователя ruvpnbot без домашнего каталога и шелла;
#   * даёт ему через sudoers ровно шесть команд и ничего больше;
#   * поднимает systemd-сервис.
#
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST=/opt/ruvpn
BOT_USER=ruvpnbot
DB_DIR=/var/lib/ruvpn-bot

die() { echo "ОШИБКА: $*" >&2; exit 1; }
info() { echo "==> $*"; }

[[ $EUID -eq 0 ]] || die "запускайте от root (sudo)"
[[ -f /etc/wireguard/ruvpn.params ]] || die "сначала поднимите сервер: install_wireguard.sh"

# shellcheck disable=SC1091
source /etc/wireguard/ruvpn.params

info "Ставим зависимости"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip sudo >/dev/null

info "Копируем в ${DEST}"
mkdir -p "$DEST"
cp -r "$SRC/server" "$SRC/bot" "$DEST/"
chown -R root:root "$DEST"
chmod 755 "$DEST" "$DEST/server" "$DEST/bot"
chmod 755 "$DEST"/server/*.sh

info "Заводим пользователя ${BOT_USER}"
id -u "$BOT_USER" >/dev/null 2>&1 || useradd --system --no-create-home \
    --home-dir "$DEST/bot" --shell /usr/sbin/nologin "$BOT_USER"

mkdir -p "$DB_DIR"
chown "$BOT_USER:$BOT_USER" "$DB_DIR"
chmod 750 "$DB_DIR"

info "Виртуальное окружение"
python3 -m venv "$DEST/bot/venv"
"$DEST/bot/venv/bin/pip" install -q --upgrade pip
"$DEST/bot/venv/bin/pip" install -q -r "$DEST/bot/requirements.txt"

info "Права sudo (ровно на нужные команды)"
WG_BIN="$(command -v wg)"
cat > /etc/sudoers.d/ruvpn-bot <<EOF
${BOT_USER} ALL=(root) NOPASSWD: ${DEST}/server/add_client.sh, \\
    ${DEST}/server/show_client.sh, \\
    ${DEST}/server/suspend_client.sh, \\
    ${DEST}/server/resume_client.sh, \\
    ${DEST}/server/remove_client.sh, \\
    ${WG_BIN} show ${WG_IFACE} allowed-ips, \\
    ${WG_BIN} show ${WG_IFACE} transfer
EOF
chmod 440 /etc/sudoers.d/ruvpn-bot
visudo -c -q -f /etc/sudoers.d/ruvpn-bot || {
    rm -f /etc/sudoers.d/ruvpn-bot
    die "sudoers не прошёл проверку, файл удалён"
}

if [[ ! -f "$DEST/bot/.env" ]]; then
    cp "$DEST/bot/.env.example" "$DEST/bot/.env"
    sed -i "s|^SCRIPTS_DIR=.*|SCRIPTS_DIR=${DEST}/server|; s|^WG_IFACE=.*|WG_IFACE=${WG_IFACE}|" \
        "$DEST/bot/.env"
    ENV_IS_NEW=1
else
    ENV_IS_NEW=0
fi
chown root:root "$DEST/bot/.env"
chmod 600 "$DEST/bot/.env"

info "Сервис systemd"
cp "$DEST/bot/ruvpn-bot.service" /etc/systemd/system/ruvpn-bot.service
systemctl daemon-reload

if [[ "$ENV_IS_NEW" == "1" ]] || ! grep -q "^BOT_TOKEN=." "$DEST/bot/.env"; then
    cat <<EOF

Почти готово. Осталось заполнить настройки:

  sudo nano ${DEST}/bot/.env      # BOT_TOKEN и ADMIN_IDS обязательны
  sudo systemctl enable --now ruvpn-bot
  sudo journalctl -u ruvpn-bot -f

Токен бота берётся у @BotFather, свой Telegram ID — у @userinfobot.
EOF
    exit 0
fi

systemctl enable --now ruvpn-bot
systemctl is-active --quiet ruvpn-bot || die "сервис не поднялся: journalctl -u ruvpn-bot"
cat <<EOF

Бот запущен. Дальше — в Telegram:
  /invite   — выдать одноразовый инвайт-код
  /users    — кто есть и до какого числа
  /key      — получить ключ себе

Логи: sudo journalctl -u ruvpn-bot -f
EOF
