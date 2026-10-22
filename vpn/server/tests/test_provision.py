"""Регистрация устройств без ключей (vpn/server/provision.py)."""
import base64
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import provision  # noqa: E402

SERVER_PUB = base64.b64encode(b"S" * 32).decode()


def key(byte):
    return base64.b64encode(bytes([byte]) * 32).decode()


class ProvisionTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.params = os.path.join(self.dir.name, "ruvpn.params")
        self.conf = os.path.join(self.dir.name, "wg0.conf")
        with open(self.params, "w") as f:
            f.write(
                'WG_IFACE="wg0"\nWG_PORT="51820"\nWG_SUBNET="10.13.13.0/24"\n'
                'WG_SUBNET6="fd42:13:13::/64"\nWG_DNS="77.88.8.8, 77.88.8.1"\n'
                f'WG_ENDPOINT="77.91.72.22"\nWAN_IFACE="eth0"\nSERVER_PUB="{SERVER_PUB}"\n'
            )
        # Уже есть клиент от add_client.sh и приостановленный клиент с .3.
        with open(self.conf, "w") as f:
            f.write(
                "[Interface]\nAddress = 10.13.13.1/24\nListenPort = 51820\nPrivateKey = x\n"
                f"\n### client nikita-pc\n[Peer]\nPublicKey = {key(1)}\nPresharedKey = {key(2)}\n"
                "AllowedIPs = 10.13.13.2/32, fd42:13:13::2/128\n### end nikita-pc\n"
                f"\n### client old\n#[Peer]\n#PublicKey = {key(3)}\n"
                "#AllowedIPs = 10.13.13.3/32, fd42:13:13::3/128\n### end old\n"
            )
        self.applied = []

    def tearDown(self):
        self.dir.cleanup()

    def register(self, public_key, device="Pixel 7", max_devices=50):
        return provision.register(self.params, public_key, device, max_devices, apply=self.applied.append)

    def test_new_device_gets_next_free_address_and_no_private_key(self):
        config = self.register(key(9))
        self.assertIn("Address = 10.13.13.4/32, fd42:13:13::4/128", config)
        self.assertIn(f"PublicKey = {SERVER_PUB}", config)
        self.assertIn("Endpoint = 77.91.72.22:51820", config)
        self.assertIn("AllowedIPs = 0.0.0.0/0, ::/0", config)
        self.assertNotIn("PrivateKey", config)
        with open(self.conf) as f:
            conf = f.read()
        self.assertRegex(conf, r"### client app-Pixel-7-[0-9a-f]{6}\n\[Peer\]\nPublicKey = " + key(9).replace("+", r"\+"))
        self.assertEqual(self.applied, ["wg0"])

    def test_same_key_twice_returns_same_config_without_new_peer(self):
        first = self.register(key(9))
        second = self.register(key(9), device="other name")
        self.assertEqual(first, second)
        with open(self.conf) as f:
            self.assertEqual(f.read().count(key(9)), 1)
        self.assertEqual(len(self.applied), 1)

    def test_telegram_bypass_allowed_ips_are_used(self):
        with open(self.params, "a") as f:
            f.write('ALLOWED_IPS="0.0.0.0/2, 64.0.0.0/4"\n')
        self.assertIn("AllowedIPs = 0.0.0.0/2, 64.0.0.0/4", self.register(key(9)))

    def test_rejections(self):
        for bad in ["", "abc", base64.b64encode(b"x" * 31).decode()]:
            with self.assertRaises(provision.ProvisionError) as e:
                self.register(bad)
            self.assertEqual(e.exception.status, 400)
        with self.assertRaises(provision.ProvisionError) as e:
            self.register(key(3))  # приостановлен на сервере
        self.assertEqual(e.exception.status, 403)
        self.register(key(9), max_devices=1)
        with self.assertRaises(provision.ProvisionError) as e:
            self.register(key(10), max_devices=1)
        self.assertEqual(e.exception.status, 429)

    def test_device_label_is_safe_for_shell_scripts(self):
        label = provision.device_label("Мой ноут; rm -rf /", key(9))
        self.assertRegex(label, r"^[a-zA-Z0-9_-]{1,32}$")


class HttpTest(unittest.TestCase):
    def test_http_register_checks_token(self):
        with tempfile.TemporaryDirectory() as d:
            params = os.path.join(d, "ruvpn.params")
            with open(params, "w") as f:
                f.write(
                    'WG_IFACE="wg0"\nWG_PORT="51820"\nWG_SUBNET="10.13.13.0/24"\n'
                    'WG_SUBNET6="fd42:13:13::/64"\nWG_DNS="1.1.1.1"\n'
                    f'WG_ENDPOINT="198.51.100.1"\nSERVER_PUB="{SERVER_PUB}"\n'
                )
            with open(os.path.join(d, "wg0.conf"), "w") as f:
                f.write("[Interface]\nAddress = 10.13.13.1/24\n")
            original = provision.apply_live
            provision.apply_live = lambda iface: None
            server = provision.ThreadingHTTPServer(("127.0.0.1", 0), provision.Handler)
            server.token, server.max_devices, server.params_path = "t", 5, params
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/register"

                def post(body):
                    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
                    try:
                        with urllib.request.urlopen(req, timeout=5) as r:
                            return r.status, json.loads(r.read())
                    except urllib.error.HTTPError as e:
                        return e.code, json.loads(e.read())

                status, body = post({"token": "wrong", "public_key": key(9)})
                self.assertEqual(status, 403)
                status, body = post({"token": "t", "public_key": key(9), "device": "pc"})
                self.assertEqual(status, 200)
                self.assertIn("Address = 10.13.13.2/32", body["config"])
            finally:
                server.shutdown()
                server.server_close()
                provision.apply_live = original


if __name__ == "__main__":
    unittest.main()
