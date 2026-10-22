//! Подключение без ключей. Список серверов зашит в приложение
//! (vpn/shared/servers.json — общий с Android). Ключ WireGuard создаётся
//! здесь же, на компьютере, и никуда не отправляется: на сервер
//! (vpn/server/provision.py) уходит только публичная половина, в ответ
//! приходит конфиг без PrivateKey, мы вставляем свой ключ и подключаемся.
use base64::Engine;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::path::Path;
use std::time::Duration;

/// Порт службы регистрации на серверах (install_provision.sh).
pub const API_PORT: u16 = 8082;
/// Общий токен приложений — тот же, что в provision.py по умолчанию.
pub const APP_TOKEN: &str = "ruvpn-app-1";

const SERVERS_JSON: &str = include_str!("../../../shared/servers.json");

#[derive(Deserialize, Serialize, Clone, Debug, PartialEq)]
pub struct Server {
    pub id: String,
    pub name: String,
    /// Код страны для флага в окне выбора.
    #[serde(default)]
    pub country: String,
    pub host: String,
}

pub fn builtin() -> Vec<Server> {
    serde_json::from_str(SERVERS_JSON).unwrap_or_default()
}

/// Ключ устройства для одного сервера и последний полученный конфиг.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub struct Registration {
    pub private_key: String,
    pub public_key: String,
    /// Конфиг от сервера, без PrivateKey.
    pub config: String,
}

#[derive(Serialize, Deserialize, Default, Debug)]
pub struct Store {
    pub selected: Option<String>,
    #[serde(default)]
    pub registrations: BTreeMap<String, Registration>,
}

impl Store {
    pub fn load(path: &Path) -> Store {
        fs::read_to_string(path)
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
            .unwrap_or_default()
    }

    pub fn save(&self, path: &Path) -> Result<(), String> {
        let text = serde_json::to_string_pretty(self).map_err(|e| e.to_string())?;
        fs::write(path, text).map_err(|e| e.to_string())
    }

    /// Выбранный сервер; если ничего не выбрано или выбранного больше нет
    /// в списке — первый.
    pub fn selected_server(&self, servers: &[Server]) -> Option<Server> {
        self.selected
            .as_ref()
            .and_then(|id| servers.iter().find(|s| &s.id == id))
            .or_else(|| servers.first())
            .cloned()
    }
}

/// Новая пара ключей X25519 (как `wg genkey` / `wg pubkey`).
pub fn generate_keypair() -> Result<(String, String), String> {
    let mut secret = [0u8; 32];
    getrandom::getrandom(&mut secret).map_err(|e| format!("не удалось создать ключ: {e}"))?;
    // Clamping, как у `wg genkey`.
    secret[0] &= 248;
    secret[31] &= 127;
    secret[31] |= 64;
    Ok(encode_pair(secret))
}

fn encode_pair(secret: [u8; 32]) -> (String, String) {
    let public = x25519_dalek::x25519(secret, x25519_dalek::X25519_BASEPOINT_BYTES);
    let b64 = base64::engine::general_purpose::STANDARD;
    (b64.encode(secret), b64.encode(public))
}

/// Вставляет свой приватный ключ в конфиг от сервера.
pub fn with_private_key(config: &str, private_key: &str) -> String {
    let mut out = String::new();
    let mut inserted = false;
    for line in config.lines() {
        out.push_str(line);
        out.push('\n');
        if !inserted && line.trim().eq_ignore_ascii_case("[Interface]") {
            out.push_str(&format!("PrivateKey = {private_key}\n"));
            inserted = true;
        }
    }
    out
}

/// Регистрирует (или повторно, без изменений) этот ключ на сервере и
/// возвращает конфиг без PrivateKey.
pub async fn register(server: &Server, public_key: &str, device: &str) -> Result<String, String> {
    let url = format!("http://{}:{}/register", server.host, API_PORT);
    let response = reqwest::Client::new()
        .post(&url)
        .json(&serde_json::json!({
            "token": APP_TOKEN,
            "public_key": public_key,
            "device": device,
        }))
        .timeout(Duration::from_secs(15))
        .send()
        .await
        .map_err(|_| format!("сервер «{}» не отвечает", server.name))?;
    let status = response.status();
    let body: serde_json::Value = response.json().await
        .map_err(|_| format!("сервер «{}» ответил непонятно", server.name))?;
    if !status.is_success() {
        let reason = body.get("error").and_then(|v| v.as_str()).unwrap_or("ошибка сервера");
        return Err(format!("сервер «{}»: {reason}", server.name));
    }
    let config = body.get("config").and_then(|v| v.as_str())
        .ok_or_else(|| format!("сервер «{}» не прислал настройки", server.name))?;
    // Проверяем тем же разбором, которым потом поднимается туннель.
    crate::config::parse(&with_private_key(config, &base64::engine::general_purpose::STANDARD.encode([1u8; 32])))
        .map_err(|_| format!("сервер «{}» прислал неверные настройки", server.name))?;
    Ok(config.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builtin_list_is_valid_and_starts_with_russia() {
        let servers = builtin();
        assert!(!servers.is_empty());
        assert_eq!(servers[0].id, "ru");
        assert!(servers.iter().all(|s| !s.host.is_empty() && !s.name.is_empty()));
    }

    #[test]
    fn public_key_matches_rfc7748_vector() {
        // RFC 7748, 6.1: закрытый ключ Алисы → её открытый ключ.
        let secret: [u8; 32] = hex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a");
        let (_, public) = encode_pair(secret);
        let b64 = base64::engine::general_purpose::STANDARD;
        assert_eq!(b64.decode(public).unwrap(), hex::<32>("8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a"));
        let (a, b) = generate_keypair().unwrap();
        assert_ne!(a, b);
        assert_eq!(b64.decode(a).unwrap().len(), 32);
    }

    fn hex<const N: usize>(text: &str) -> [u8; N] {
        let mut out = [0u8; N];
        for (i, byte) in out.iter_mut().enumerate() {
            *byte = u8::from_str_radix(&text[i * 2..i * 2 + 2], 16).unwrap();
        }
        out
    }

    #[test]
    fn private_key_goes_right_after_interface_and_parses() {
        let config = "[Interface]\nAddress = 10.13.13.4/32, fd42:13:13::4/128\nDNS = 77.88.8.8\n\n[Peer]\nPublicKey = U1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1M=\nEndpoint = 77.91.72.22:51820\nAllowedIPs = 0.0.0.0/0, ::/0\nPersistentKeepalive = 25\n";
        let (private, _) = generate_keypair().unwrap();
        let full = with_private_key(config, &private);
        assert!(full.starts_with(&format!("[Interface]\nPrivateKey = {private}\nAddress")));
        let parsed = crate::config::parse(&full).unwrap();
        assert_eq!(parsed.peers[0].endpoint, ("77.91.72.22".to_string(), 51820));
    }

    #[test]
    fn store_falls_back_to_first_server() {
        let servers = builtin();
        let mut store = Store::default();
        assert_eq!(store.selected_server(&servers).unwrap().id, "ru");
        store.selected = Some("us".into());
        assert_eq!(store.selected_server(&servers).unwrap().id, "us");
        store.selected = Some("gone".into());
        assert_eq!(store.selected_server(&servers).unwrap().id, "ru");
    }
}
