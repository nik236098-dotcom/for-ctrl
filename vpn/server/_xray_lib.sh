#!/usr/bin/env bash
#
# Общий код add/remove/suspend/resume/show_client_happ.sh — не отдельный
# скрипт, сам по себе не запускается и в sudoers не прописан (только сами
# *_client_happ.sh, которые его подключают через `source`). В отличие от
# WireGuard-скриптов (там дублирование небольшое), тут общая часть — это
# jq-трансформация JSON, ошибиться в четырёх местах по-разному было бы
# слишком легко.
#
set -euo pipefail

XRAY_DIR="/usr/local/etc/xray"
PARAMS="${XRAY_DIR}/ruvpn.params"
CLIENTS_DB="${XRAY_DIR}/ruvpn-clients.json"
CONFIG="${XRAY_DIR}/config.json"

[[ -f "$PARAMS" ]] || die "сервер не настроен, сначала install_xray.sh"
# shellcheck disable=SC1090
source "$PARAMS"

# Пересобирает inbounds[0].settings.clients в config.json из
# ruvpn-clients.json (берёт только enabled:true) и перезапускает xray.
# Приостановленный клиент остаётся в базе — просто временно не попадает
# в живой конфиг, поэтому resume возвращает тот же самый UUID.
xray_rebuild_and_restart() {
    local clients
    clients="$(jq '[.[] | select(.enabled) | {id: .uuid, email: .name, flow: "xtls-rprx-vision"}]' "$CLIENTS_DB")"
    jq --argjson clients "$clients" '.inbounds[0].settings.clients = $clients' "$CONFIG" > "${CONFIG}.tmp"
    mv "${CONFIG}.tmp" "$CONFIG"
    chmod 600 "$CONFIG"
    systemctl restart xray
}

# Печатает готовую ссылку vless://... для уже существующего клиента NAME.
xray_client_uri() {
    local name="$1" uuid
    uuid="$(jq -r --arg name "$name" '.[] | select(.name == $name) | .uuid' "$CLIENTS_DB")"
    [[ -n "$uuid" ]] || die "клиента '${name}' нет"
    echo "vless://${uuid}@${HAPP_ENDPOINT}:${HAPP_PORT}?encryption=none&security=reality&sni=${REALITY_SERVER_NAME}&fp=chrome&pbk=${REALITY_PUBLIC_KEY}&sid=${REALITY_SHORT_ID}&type=tcp&flow=xtls-rprx-vision#${name}"
}
