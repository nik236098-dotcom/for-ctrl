#!/usr/bin/env bash
#
# Делает так, чтобы Telegram работал у клиентов RU-сервера: адреса Telegram
# исключаются из туннеля (AllowedIPs), и трафик к ним идёт напрямую через
# собственный интернет клиента — у людей за границей Telegram не заблокирован,
# а выход из России его режет. Всё остальное по-прежнему идёт через туннель,
# внешний IP остаётся российским.
#
# Запускать ТОЛЬКО на российском сервере. На американском этого делать не
# нужно: там Telegram и так доступен, а у людей в России (которые через
# «зеркало» США) прямой интернет — российский, Telegram в обход туннеля у
# них как раз и сломался бы.
#
#   sudo bash vpn/server/telegram_bypass.sh        # включить
#   sudo bash vpn/server/telegram_bypass.sh --off  # вернуть весь трафик в туннель
#
# Что делает:
#   1. записывает ALLOWED_IPS в ruvpn.params — add_client.sh подхватит его
#      для всех новых клиентов;
#   2. переписывает AllowedIPs у уже существующих клиентов в
#      /etc/wireguard/clients/*.conf;
#   3. обновляет конфиги, которые бот уже выдал по коротким ключам
#      (таблица access_keys в базе бота) — сервер ключей отдаёт именно их.
#
# Приложению на телефоне/компьютере конфиг отдаётся один раз, при вставке
# ключа, дальше оно работает с сохранённой копией — уже подключённым людям
# нужно один раз заново вставить тот же ключ, чтобы подхватить изменение.
#
set -euo pipefail

WG_DIR="/etc/wireguard"
PARAMS="${WG_DIR}/ruvpn.params"
DB_PATH="${DB_PATH:-/var/lib/ruvpn-bot/bot.db}"

die() { echo "ОШИБКА: $*" >&2; exit 1; }
info() { echo "==> $*"; }

[[ $EUID -eq 0 ]] || die "запускайте от root (sudo)"
[[ -f "$PARAMS" ]] || die "сервер не настроен, сначала install_wireguard.sh"
command -v python3 >/dev/null || die "нужен python3"

if [[ "${1:-}" == "--off" ]]; then
    ALLOWED_IPS="0.0.0.0/0, ::/0"
    info "Возвращаем весь трафик в туннель"
else
    # Сети Telegram (AS62041 и соседние, по публичным спискам). Весь остальной
    # интернет = дополнение до 0.0.0.0/0 и ::/0 — считаем через ipaddress, а
    # не держим готовый список из ~70 CIDR руками.
    ALLOWED_IPS="$(python3 - <<'EOF'
import ipaddress
telegram = [
    "91.105.192.0/23", "91.108.4.0/22", "91.108.8.0/22", "91.108.12.0/22",
    "91.108.16.0/22", "91.108.20.0/22", "91.108.56.0/22", "149.154.160.0/20",
    "185.76.151.0/24",
    "2001:67c:4e8::/48", "2001:b28:f23c::/48", "2001:b28:f23d::/48",
    "2001:b28:f23f::/48", "2a0a:f280::/32",
]
result = []
for universe in ("0.0.0.0/0", "::/0"):
    nets = [ipaddress.ip_network(universe)]
    for ex in map(ipaddress.ip_network, telegram):
        if ex.version != nets[0].version:
            continue
        out = []
        for n in nets:
            out.extend(n.address_exclude(ex) if ex.subnet_of(n) else [n])
        nets = out
    result += [str(n) for n in sorted(ipaddress.collapse_addresses(nets))]
print(", ".join(result))
EOF
)"
    info "Исключаем адреса Telegram из туннеля"
fi

info "Записываем ALLOWED_IPS в $PARAMS (для новых клиентов)"
grep -v '^ALLOWED_IPS=' "$PARAMS" > "${PARAMS}.tmp" || true
printf 'ALLOWED_IPS="%s"\n' "$ALLOWED_IPS" >> "${PARAMS}.tmp"
mv "${PARAMS}.tmp" "$PARAMS"
chmod 600 "$PARAMS"

COUNT=0
shopt -s nullglob
for conf in "${WG_DIR}"/clients/*.conf; do
    sed -i "s|^AllowedIPs = .*|AllowedIPs = ${ALLOWED_IPS}|" "$conf"
    COUNT=$((COUNT + 1))
done
info "Обновлено конфигов клиентов: ${COUNT}"

if [[ -f "$DB_PATH" ]]; then
    UPDATED="$(ALLOWED_IPS="$ALLOWED_IPS" DB_PATH="$DB_PATH" python3 - <<'EOF'
import os, re, sqlite3
db = sqlite3.connect(os.environ["DB_PATH"])
new = "AllowedIPs = " + os.environ["ALLOWED_IPS"]
count = 0
# Только домашний сервер (access_keys). «Зеркала» других стран лежат в
# access_key_regions — их не трогаем, см. шапку скрипта.
for code, config in db.execute("SELECT code, config FROM access_keys").fetchall():
    patched = re.sub(r"^AllowedIPs = .*$", new, config, flags=re.M)
    if patched != config:
        db.execute("UPDATE access_keys SET config = ? WHERE code = ?", (patched, code))
        count += 1
db.commit()
print(count)
EOF
)"
    info "Обновлено уже выданных ключей в базе бота: ${UPDATED}"
else
    info "База бота ${DB_PATH} не найдена — выданные ключи не трогал"
fi

cat <<EOF

Готово. Новые ключи получают эту настройку сразу. Тем, кто уже подключён,
нужно один раз заново вставить свой ключ в приложении — конфиг приложение
скачивает только в момент вставки.
EOF
