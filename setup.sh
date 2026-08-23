#!/usr/bin/env bash
# Установка Docker (если нужно) + подготовка модулей binder/ashmem для redroid.
# Запускать на самом VDS. Требует sudo.

set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
    echo "Устанавливаю Docker..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER"
    echo "Docker установлен. Может понадобиться перелогиниться, чтобы группа docker применилась."
fi

echo "Подгружаю binder/ashmem (если ядро поддерживает)..."
sudo modprobe ashmem_linux 2>/dev/null || echo "  ashmem_linux не подгрузился — см. scripts/check_prereqs.sh для инструкций"
sudo modprobe binder_linux devices="binder,hwbinder,vndbinder" 2>/dev/null || echo "  binder_linux не подгрузился — см. scripts/check_prereqs.sh для инструкций"

mkdir -p data downloads

echo
echo "Готово. Дальше:"
echo "  docker compose up -d"
echo "  adb connect 127.0.0.1:5555"
