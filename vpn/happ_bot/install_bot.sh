#!/usr/bin/env bash
#
# Ставит телеграм-бота подписки (Happ/iPhone, VLESS+Reality) на тот же
# сервер, где живёт Xray. Отдельный бот от ruvpn_bot (WireGuard) — свой
# systemd-сервис, своя база, свой системный пользователь.
#
#   sudo bash vpn/happ_bot/install_bot.sh
#
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST=/opt/happ-vpn
BOT_USER=happvpnbot
DB_DIR=/var/lib/happ-vpn-bot

die() { echo "ОШИБКА: $*" >&2; exit 1; }
info() { echo "==> $*"; }

[[ $EUID -eq 0 ]] || die "запускайте от root (sudo)"
[[ -f /usr/local/etc/xray/ruvpn.params ]] || die "сначала поднимите Xray: install_xray.sh"

info "Ставим зависимости"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip sudo >/dev/null

info "Копируем в ${DEST}"
mkdir -p "$DEST"
cp -r "$SRC/server" "$SRC/happ_bot" "$DEST/"
chown -R root:root "$DEST"
chmod 755 "$DEST" "$DEST/server" "$DEST/happ_bot"
chmod 755 "$DEST"/server/*.sh

info "Заводим пользователя ${BOT_USER}"
id -u "$BOT_USER" >/dev/null 2>&1 || useradd --system --no-create-home \
    --home-dir "$DEST/happ_bot" --shell /usr/sbin/nologin "$BOT_USER"

mkdir -p "$DB_DIR"
chown "$BOT_USER:$BOT_USER" "$DB_DIR"
chmod 750 "$DB_DIR"

info "Виртуальное окружение"
python3 -m venv "$DEST/happ_bot/venv"
"$DEST/happ_bot/venv/bin/pip" install -q --upgrade pip
"$DEST/happ_bot/venv/bin/pip" install -q -r "$DEST/happ_bot/requirements.txt"

info "Права sudo (ровно на нужные команды)"
cat > /etc/sudoers.d/happ-vpn-bot <<EOF
${BOT_USER} ALL=(root) NOPASSWD: ${DEST}/server/add_client_happ.sh, \\
    ${DEST}/server/show_client_happ.sh, \\
    ${DEST}/server/suspend_client_happ.sh, \\
    ${DEST}/server/resume_client_happ.sh, \\
    ${DEST}/server/remove_client_happ.sh, \\
    ${DEST}/server/xray_traffic.sh
EOF
chmod 440 /etc/sudoers.d/happ-vpn-bot
visudo -c -q -f /etc/sudoers.d/happ-vpn-bot || {
    rm -f /etc/sudoers.d/happ-vpn-bot
    die "sudoers не прошёл проверку, файл удалён"
}

if [[ ! -f "$DEST/happ_bot/.env" ]]; then
    cp "$DEST/happ_bot/.env.example" "$DEST/happ_bot/.env"
    sed -i "s|^SCRIPTS_DIR=.*|SCRIPTS_DIR=${DEST}/server|" "$DEST/happ_bot/.env"
    ENV_IS_NEW=1
else
    ENV_IS_NEW=0
fi
chown root:root "$DEST/happ_bot/.env"
chmod 600 "$DEST/happ_bot/.env"

info "Сервис systemd"
cp "$DEST/happ_bot/happ-bot.service" /etc/systemd/system/happ-bot.service
systemctl daemon-reload

if [[ "$ENV_IS_NEW" == "1" ]] || ! grep -q "^BOT_TOKEN=." "$DEST/happ_bot/.env"; then
    cat <<EOF

Почти готово. Осталось заполнить настройки:

  sudo nano ${DEST}/happ_bot/.env      # BOT_TOKEN, ADMIN_IDS, платёжные токены
  sudo systemctl enable --now happ-bot
  sudo journalctl -u happ-bot -f

Токен бота берётся у @BotFather (заведите отдельного бота, не тот же, что
для WireGuard), свой Telegram ID — у @userinfobot.

Если config.json Xray был поднят ДО добавления учёта трафика — добавьте в
него вручную блоки "api"/"stats"/"policy" (см. install_xray.sh) и
перезапустите xray, иначе счётчики трафика в разделе устройств будут пустыми.
EOF
    exit 0
fi

systemctl enable --now happ-bot
systemctl is-active --quiet happ-bot || die "сервис не поднялся: journalctl -u happ-bot"
cat <<EOF

Бот запущен. Логи: sudo journalctl -u happ-bot -f
EOF
