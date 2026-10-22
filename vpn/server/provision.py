#!/usr/bin/env python3
"""
Регистрация устройств без ключей.

Приложения Ru VPN (Windows и Android) сами создают себе ключ WireGuard на
устройстве и при первом подключении присылают сюда только ПУБЛИЧНУЮ его
часть. Сервис добавляет устройство пиром в wg0 — ровно так же, как
add_client.sh (те же метки «### client …», та же раздача адресов, поэтому
remove_client.sh / suspend_client.sh работают и с такими устройствами) — и
возвращает конфиг без PrivateKey: приватный ключ никогда не покидает
устройство. Повторная регистрация того же ключа ничего не создаёт заново,
а просто отдаёт актуальный конфиг (например, после telegram_bypass.sh).

Ни бота, ни подписок, ни ключей у человека: нажал «Соединить» — подключился.

    POST /register  {"token": "...", "public_key": "<base64>", "device": "Pixel 7"}
      200 {"config": "[Interface]\\nAddress = …"}   (без строки PrivateKey)
      400 неверный запрос, 403 неверный токен / устройство отключено,
      429 закончились места (PROVISION_MAX_DEVICES)
    GET /health → {"ok": true}

Переменные окружения (см. install_provision.sh, /etc/ruvpn-provision.env):
    PROVISION_PORT         TCP-порт, по умолчанию 8082
    PROVISION_TOKEN        общий токен приложений (по умолчанию тот же, что
                           зашит в приложения — защищает только от
                           случайных сканеров, не от того, у кого есть
                           само приложение)
    PROVISION_MAX_DEVICES  сколько устройств можно зарегистрировать, 50
    RUVPN_PARAMS           /etc/wireguard/ruvpn.params
"""
import base64
import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import fcntl  # есть только на Linux/macOS, где и работает служба
except ImportError:  # тесты на Windows-машине CI
    fcntl = None

DEFAULT_TOKEN = "ruvpn-app-1"
DEFAULT_ALLOWED_IPS = "0.0.0.0/0, ::/0"


class ProvisionError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def read_params(path):
    """KEY="value" строки ruvpn.params (тот же файл, что source-ят скрипты)."""
    params = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            match = re.match(r'^\s*([A-Z0-9_]+)=(.*)$', line.rstrip("\n"))
            if match:
                params[match.group(1)] = match.group(2).strip().strip('"').strip("'")
    for key in ("WG_IFACE", "WG_PORT", "WG_SUBNET", "WG_SUBNET6", "WG_DNS", "WG_ENDPOINT", "SERVER_PUB"):
        if not params.get(key):
            raise ProvisionError(500, f"в {path} нет {key}")
    return params


def valid_public_key(value):
    try:
        return len(base64.b64decode(value, validate=True)) == 32
    except Exception:
        return False


def device_label(device, public_key):
    """Имя пира: только [a-zA-Z0-9_-], до 32 символов (как требует add_client.sh)."""
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "-", device or "").strip("-")[:14] or "device"
    suffix = base64.b64decode(public_key).hex()[:6]
    return f"app-{clean}-{suffix}"


def find_block(conf_text, public_key):
    """(имя, текст блока, отключён ли) для пира с этим ключом, иначе None."""
    for match in re.finditer(r"^### client (\S+)\n(.*?)^### end \1$", conf_text, re.M | re.S):
        body = match.group(2)
        key_line = re.search(r"^(\s*#\s*)?PublicKey\s*=\s*(\S+)\s*$", body, re.M)
        if key_line and key_line.group(2) == public_key:
            return match.group(1), body, bool(key_line.group(1))
    return None


def next_octet(conf_text, base4):
    """Следующий свободный последний октет — учитывая и закомментированные строки."""
    used = [1]
    pattern = re.escape(base4) + r"\.(\d+)"
    for line in conf_text.splitlines():
        if re.match(r"^\s*#?\s*AllowedIPs", line):
            used += [int(n) for n in re.findall(pattern, line)]
    candidate = max(used) + 1
    if candidate > 254:
        raise ProvisionError(429, "на сервере закончились свободные адреса")
    return candidate


def client_config(params, address, psk):
    return (
        "[Interface]\n"
        f"Address = {address}\n"
        f"DNS = {params['WG_DNS']}\n"
        "\n"
        "[Peer]\n"
        f"PublicKey = {params['SERVER_PUB']}\n"
        + (f"PresharedKey = {psk}\n" if psk else "")
        + f"Endpoint = {params['WG_ENDPOINT']}:{params['WG_PORT']}\n"
        f"AllowedIPs = {params.get('ALLOWED_IPS') or DEFAULT_ALLOWED_IPS}\n"
        "PersistentKeepalive = 25\n"
    )


def apply_live(iface):
    """Применяем на живом интерфейсе, не разрывая соединения остальных."""
    subprocess.run(
        ["bash", "-c", f'wg syncconf "{iface}" <(wg-quick strip "{iface}")'],
        check=True, timeout=30,
    )


_lock = threading.Lock()


def register(params_path, public_key, device, max_devices, apply=None):
    if not valid_public_key(public_key):
        raise ProvisionError(400, "неверный public_key")
    params = read_params(params_path)
    conf_path = os.path.join(os.path.dirname(params_path), f"{params['WG_IFACE']}.conf")
    with _lock, open(conf_path + ".lock", "w") as lock_file:
        # Межпроцессная блокировка: add_client.sh и бот могут писать тот же файл.
        if fcntl is not None:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
        with open(conf_path, encoding="utf-8") as f:
            conf_text = f.read()

        existing = find_block(conf_text, public_key)
        if existing:
            _, body, suspended = existing
            if suspended:
                raise ProvisionError(403, "это устройство отключено на сервере")
            address = re.search(r"^\s*AllowedIPs\s*=\s*(.+?)\s*$", body, re.M).group(1)
            psk_line = re.search(r"^\s*PresharedKey\s*=\s*(\S+)\s*$", body, re.M)
            psk = psk_line.group(1) if psk_line else None
            return client_config(params, address, psk)

        devices = len(re.findall(r"^### client app-", conf_text, re.M))
        if devices >= max_devices:
            raise ProvisionError(429, "на сервере закончились места для новых устройств")

        base4 = params["WG_SUBNET"].split("/")[0].rsplit(".", 1)[0]
        base6 = params["WG_SUBNET6"].split("/")[0]
        octet = next_octet(conf_text, base4)
        address = f"{base4}.{octet}/32, {base6}{octet}/128"
        psk = base64.b64encode(os.urandom(32)).decode()
        name = device_label(device, public_key)

        block = (
            f"\n### client {name}\n"
            "[Peer]\n"
            f"PublicKey = {public_key}\n"
            f"PresharedKey = {psk}\n"
            f"AllowedIPs = {address}\n"
            f"### end {name}\n"
        )
        with open(conf_path, "a", encoding="utf-8") as f:
            f.write(block)
        os.chmod(conf_path, 0o600)
        (apply or apply_live)(params["WG_IFACE"])
        return client_config(params, address, psk)


class Handler(BaseHTTPRequestHandler):
    server_version = "ruvpn-provision"

    def _send(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/register":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 4096:
                raise ProvisionError(400, "неверный запрос")
            try:
                data = json.loads(self.rfile.read(length))
            except ValueError:
                raise ProvisionError(400, "неверный запрос")
            if not isinstance(data, dict):
                raise ProvisionError(400, "неверный запрос")
            if data.get("token") != self.server.token:
                raise ProvisionError(403, "неверный токен приложения")
            config = register(
                self.server.params_path,
                str(data.get("public_key") or ""),
                str(data.get("device") or ""),
                self.server.max_devices,
            )
            self._send(200, {"config": config})
        except ProvisionError as e:
            self._send(e.status, {"error": e.message})
        except Exception as e:  # noqa: BLE001 — ответить клиенту, а не уронить поток
            self.log_error("register failed: %r", e)
            self._send(500, {"error": "ошибка на сервере"})

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


def main():
    port = int(os.environ.get("PROVISION_PORT", "8082"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.token = os.environ.get("PROVISION_TOKEN") or DEFAULT_TOKEN
    server.max_devices = int(os.environ.get("PROVISION_MAX_DEVICES", "50"))
    server.params_path = os.environ.get("RUVPN_PARAMS", "/etc/wireguard/ruvpn.params")
    read_params(server.params_path)  # сразу падаем, если сервер не настроен
    print(f"ruvpn-provision: порт {port}, мест {server.max_devices}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
