#!/usr/bin/env bash
#
# Печатает сырую статистику трафика Xray (JSON от `xray api statsquery`) —
# бот сам достаёт из неё цифры по конкретным клиентам (по email == имени
# клиента в ruvpn-clients.json).
#
# Нужен блок "api"/"stats"/"policy" в config.json (см. install_xray.sh) —
# на сервере, поднятом ДО этой правки, придётся добавить эти три блока в
# /usr/local/etc/xray/config.json вручную и перезапустить xray, иначе
# statsquery ответит ошибкой (API не слушает).
#
#   sudo bash vpn/server/xray_traffic.sh
#
set -euo pipefail

die() { echo "ОШИБКА: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "запускайте от root (sudo)"
command -v xray >/dev/null || die "xray не установлен"

xray api statsquery -s 127.0.0.1:10085 -pattern "user>>>"
