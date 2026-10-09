//! Reject malformed input before replacing a saved key or touching a service.
//! This is a structural preflight; WireGuard still validates network settings.
use base64::Engine;
use std::{fs, path::Path};

fn invalid(detail: &str) -> String {
    // Never include pasted lines or key values in a displayed error.
    format!("Неверная конфигурация VPN: {detail}. Заново вставьте полный ключ из бота.")
}

fn valid_key(value: &str) -> bool {
    base64::engine::general_purpose::STANDARD.decode(value)
        .map(|bytes| bytes.len() == 32).unwrap_or(false)
}

pub fn validate(text: &str) -> Result<String, String> {
    let text = text.trim().trim_start_matches('\u{feff}').trim();
    let mut section = "";
    let mut private_key = false;
    let mut peers = Vec::new();
    for (index, raw) in text.lines().enumerate() {
        let line = raw.split('#').next().unwrap_or("").trim();
        if line.is_empty() { continue; }
        if line.eq_ignore_ascii_case("[Interface]") {
            section = "interface";
            continue;
        }
        if line.eq_ignore_ascii_case("[Peer]") {
            section = "peer";
            peers.push(false);
            continue;
        }
        if section.is_empty() {
            return Err(invalid("текст перед секцией [Interface]"));
        }
        let (field, value) = line.split_once('=')
            .ok_or_else(|| invalid(&format!("ошибка формата в строке {}", index + 1)))?;
        let field = field.trim().to_ascii_lowercase();
        let value = value.trim();
        if field.is_empty() || value.is_empty() {
            return Err(invalid(&format!("пустой параметр в строке {}", index + 1)));
        }
        match (section, field.as_str()) {
            ("interface", "privatekey") => {
                if !valid_key(value) { return Err(invalid("повреждён PrivateKey")); }
                private_key = true;
            }
            ("peer", "publickey") => {
                if !valid_key(value) { return Err(invalid("повреждён PublicKey")); }
                *peers.last_mut().unwrap() = true;
            }
            ("peer", "presharedkey") => {
                if !valid_key(value) { return Err(invalid("повреждён PresharedKey")); }
            }
            ("interface", "address" | "dns" | "mtu" | "listenport" | "table"
                | "preup" | "postup" | "predown" | "postdown") => {}
            ("peer", "allowedips" | "endpoint" | "persistentkeepalive") => {}
            _ => return Err(invalid(&format!("неподдерживаемый параметр в строке {}", index + 1))),
        }
    }
    if !private_key { return Err(invalid("нет [Interface] с PrivateKey")); }
    if peers.is_empty() || peers.iter().any(|key| !key) {
        return Err(invalid("нет [Peer] с PublicKey"));
    }
    Ok(text.to_string())
}

pub fn write_validated(path: &Path, text: &str) -> Result<(), String> {
    let validated = validate(text)?;
    fs::write(path, validated).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> String {
        // Synthetic bytes, never a user's VPN key.
        let key = base64::engine::general_purpose::STANDARD.encode([1u8; 32]);
        format!("[Interface]\nPrivateKey = {key}\nAddress = 10.0.0.2/32\nDNS = 1.1.1.1\n\n[Peer]\nPublicKey = {key}\nAllowedIPs = 0.0.0.0/0, ::/0\nEndpoint = vpn.example:51820\nPersistentKeepalive = 25\n")
    }

    #[test]
    fn rejects_reported_s_and_successful_http_error_bodies() {
        for text in ["s", "", "abcdefgh", "ruvpn://abcdefgh", "<html>error</html>", "{\"error\":\"unavailable\"}"] {
            assert!(validate(text).is_err());
        }
        assert!(validate(&format!("s\n{}", fixture())).is_err());
    }

    #[test]
    fn accepts_comments_crlf_bom_and_case_insensitive_sections() {
        let text = format!("\u{feff}# exported configuration\r\n{}", fixture().replace('\n', "\r\n")
            .replace("[Interface]", "[interface]").replace("[Peer]", "[PEER]"));
        assert!(!validate(&text).unwrap().starts_with('\u{feff}'));
    }

    #[test]
    fn rejects_truncated_keys_missing_peers_and_wrong_sections() {
        let key = base64::engine::general_purpose::STANDARD.encode([1u8; 32]);
        for text in [fixture().replace(&key, "broken"), fixture().split("[Peer]").next().unwrap().to_string(),
            fixture().replace("PublicKey", "PrivateKey"), format!("{}\n[Peer]\nEndpoint = vpn.example:51820", fixture())] {
            assert!(validate(&text).is_err());
        }
    }

    #[test]
    fn invalid_replacement_preserves_existing_file_and_does_not_expose_input() {
        let path = std::env::temp_dir().join(format!("ruvpn-config-test-{}.conf", std::process::id()));
        write_validated(&path, &fixture()).unwrap();
        let original = fs::read_to_string(&path).unwrap();
        let error = write_validated(&path, "secret-invalid-input").unwrap_err();
        assert_eq!(fs::read_to_string(&path).unwrap(), original);
        assert!(!error.contains("secret-invalid-input"));
        fs::remove_file(path).unwrap();
    }
}
