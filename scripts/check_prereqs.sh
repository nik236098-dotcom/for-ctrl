#!/usr/bin/env bash
# Проверка сервера перед установкой redroid + Госключ-бота.
# Ничего не устанавливает — только диагностирует и подсказывает следующий шаг.

set -uo pipefail

ok()   { echo "  [OK]   $1"; }
warn() { echo "  [WARN] $1"; }
fail() { echo "  [FAIL] $1"; }

echo "== Виртуализация =="
VIRT="$(systemd-detect-virt 2>/dev/null || echo unknown)"
if [ "$VIRT" = "kvm" ] || [ "$VIRT" = "none" ]; then
    ok "systemd-detect-virt: $VIRT (можно грузить модули ядра)"
else
    fail "systemd-detect-virt: $VIRT — контейнерная виртуализация (OpenVZ/LXC), redroid, скорее всего, не запустится. Нужен KVM-сервер."
fi

echo
echo "== Ресурсы =="
RAM_MB=$(free -m | awk '/^Mem:/{print $2}')
CPU_N=$(nproc)
echo "  RAM: ${RAM_MB} MB, CPU: ${CPU_N} ядер"
if [ "$RAM_MB" -lt 2000 ]; then
    warn "Меньше 2 ГБ RAM — не хватит даже на один Android-контейнер."
elif [ "$RAM_MB" -lt 5000 ]; then
    warn "Хватит на один аккаунт (один redroid-контейнер), но на два одновременно (personal+ip) может быть тесно — учитывайте ещё и другие процессы на сервере."
else
    ok "RAM достаточно на оба аккаунта"
fi
if [ "$CPU_N" -lt 2 ]; then
    warn "1 ядро — будет тормозить. Рекомендуется от 2 ядер."
else
    ok "CPU достаточно"
fi

echo
echo "== Docker =="
if command -v docker >/dev/null 2>&1; then
    ok "Docker установлен: $(docker --version)"
    if docker compose version >/dev/null 2>&1; then
        ok "docker compose доступен"
    else
        warn "docker compose plugin не найден — setup.sh поставит его"
    fi
else
    warn "Docker не установлен — setup.sh поставит его"
fi

echo
echo "== Порты ADB (5555 личный, 5565 ИП) =="
for PORT in 5555 5565; do
    if command -v ss >/dev/null 2>&1 && ss -tln | grep -q ":${PORT} "; then
        warn "Порт ${PORT} уже занят другим процессом. Смените соответствующую переменную ADB_HOST_PORT_* в .env (см. .env.example) и ADB_DEVICE в .env.personal/.env.ip."
    else
        ok "Порт ${PORT} свободен"
    fi
done

echo
echo "== Поддержка binder (нужна для redroid) =="
if [ -e /dev/binderfs ] || ls /dev/binder* >/dev/null 2>&1; then
    ok "binder-устройства уже присутствуют в системе"
elif lsmod 2>/dev/null | grep -q binder_linux; then
    ok "модуль binder_linux уже загружен"
elif modprobe binder_linux devices="binder,hwbinder,vndbinder" 2>/dev/null; then
    ok "модуль binder_linux успешно подгружен (modprobe)"
    modprobe ashmem_linux 2>/dev/null || true
else
    fail "binder-модуль недоступен в этом ядре."
    echo "        Варианты:"
    echo "        1) Поставить готовый модуль из PPA проекта Anbox (если версия Ubuntu/ядра поддерживается):"
    echo "           sudo add-apt-repository ppa:morphis/anbox-support"
    echo "           sudo apt update && sudo apt install linux-headers-\$(uname -r) anbox-modules-dkms"
    echo "           sudo modprobe ashmem_linux && sudo modprobe binder_linux devices=\"binder,hwbinder,vndbinder\""
    echo "        2) Либо собрать модуль вручную из исходников ядра (сложнее, скажите — помогу отдельно)."
fi

echo
echo "Готово. Если всё [OK] — запускайте: bash setup.sh && docker compose up -d"
