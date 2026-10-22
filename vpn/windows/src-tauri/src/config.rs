//! Разбор конфига WireGuard. Плохой ввод отвергается ещё до сохранения
//! ключа — тем же разбором, которым потом поднимается туннель.
use base64::Engine;
use ipnet::IpNet;
use std::net::IpAddr;
use std::{fs, path::Path};

fn invalid(detail: &str) -> String {
    // Never include pasted lines or key values in a displayed error.
    format!("Неверная конфигурация VPN: {detail}. Заново вставьте полный ключ из бота.")
}

/// Разобранный конфиг — ровно то, что нужно встроенному туннелю (tunnel.rs).
/// PreUp/PostUp и прочие скрипты официального клиента не выполняются: их и
/// официальный WireGuard по умолчанию отключает, а в наших ключах их нет.
#[derive(Debug, Clone)]
pub struct Parsed {
    pub private_key: [u8; 32],
    pub listen_port: Option<u16>,
    pub addresses: Vec<IpNet>,
    pub dns: Vec<IpAddr>,
    pub mtu: Option<u32>,
    pub peers: Vec<ParsedPeer>,
}

#[derive(Debug, Clone)]
pub struct ParsedPeer {
    pub public_key: [u8; 32],
    pub preshared_key: Option<[u8; 32]>,
    pub allowed_ips: Vec<IpNet>,
    /// Хост и порт как в конфиге: имя превращается в адрес только при
    /// подключении (tunnel.rs), чтобы сохранение ключа не зависело от сети.
    pub endpoint: (String, u16),
    pub keepalive: Option<u16>,
}

fn decode_key(value: &str) -> Option<[u8; 32]> {
    base64::engine::general_purpose::STANDARD.decode(value).ok()?.try_into().ok()
}

/// "10.0.0.2/32" или просто "10.0.0.2" (тогда /32 или /128). Биты хоста в
/// сетях маршрутов обнуляются — Windows не принимает маршрут 10.0.0.1/24.
fn parse_net(value: &str) -> Option<IpNet> {
    let value = value.trim();
    value.parse::<IpNet>().ok().or_else(|| value.parse::<IpAddr>().ok().map(IpNet::from))
}

fn parse_endpoint(value: &str) -> Option<(String, u16)> {
    let (host, port) = value.trim().rsplit_once(':')?;
    let port = port.parse::<u16>().ok().filter(|port| *port != 0)?;
    let host = host.strip_prefix('[').and_then(|h| h.strip_suffix(']')).unwrap_or(host).trim();
    if host.is_empty() || host.contains(char::is_whitespace) {
        return None;
    }
    Some((host.to_string(), port))
}

fn list(value: &str) -> impl Iterator<Item = &str> {
    value.split(',').map(str::trim).filter(|item| !item.is_empty())
}

struct PeerDraft {
    public_key: Option<[u8; 32]>,
    preshared_key: Option<[u8; 32]>,
    allowed_ips: Vec<IpNet>,
    endpoint: Option<(String, u16)>,
    keepalive: Option<u16>,
}

pub fn parse(text: &str) -> Result<Parsed, String> {
    let text = text.trim().trim_start_matches('\u{feff}').trim();
    let mut section = "";
    let mut private_key = None;
    let mut listen_port = None;
    let mut addresses = Vec::new();
    let mut dns = Vec::new();
    let mut mtu = None;
    let mut peers: Vec<PeerDraft> = Vec::new();
    for (index, raw) in text.lines().enumerate() {
        let line = raw.split('#').next().unwrap_or("").trim();
        if line.is_empty() { continue; }
        if line.eq_ignore_ascii_case("[Interface]") {
            section = "interface";
            continue;
        }
        if line.eq_ignore_ascii_case("[Peer]") {
            section = "peer";
            peers.push(PeerDraft { public_key: None, preshared_key: None, allowed_ips: Vec::new(), endpoint: None, keepalive: None });
            continue;
        }
        if section.is_empty() {
            return Err(invalid("текст перед секцией [Interface]"));
        }
        let line_no = index + 1;
        let (field, value) = line.split_once('=')
            .ok_or_else(|| invalid(&format!("ошибка формата в строке {line_no}")))?;
        let field = field.trim().to_ascii_lowercase();
        let value = value.trim();
        if field.is_empty() || value.is_empty() {
            return Err(invalid(&format!("пустой параметр в строке {line_no}")));
        }
        let bad = |what: &str| invalid(&format!("неверный {what} в строке {line_no}"));
        match (section, field.as_str()) {
            ("interface", "privatekey") => {
                private_key = Some(decode_key(value).ok_or_else(|| invalid("повреждён PrivateKey"))?);
            }
            ("interface", "listenport") => {
                listen_port = Some(value.parse::<u16>().map_err(|_| bad("ListenPort"))?);
            }
            ("interface", "address") => {
                for item in list(value) {
                    addresses.push(parse_net(item).ok_or_else(|| bad("Address"))?);
                }
            }
            ("interface", "dns") => {
                // Не-IP в DNS — это домены поиска; нам они не нужны.
                dns.extend(list(value).filter_map(|item| item.parse::<IpAddr>().ok()));
            }
            ("interface", "mtu") => {
                let value = value.parse::<u32>().ok().filter(|v| (576..=65535).contains(v))
                    .ok_or_else(|| bad("MTU"))?;
                mtu = Some(value);
            }
            ("interface", "table" | "preup" | "postup" | "predown" | "postdown") => {}
            ("peer", "publickey") => {
                peers.last_mut().unwrap().public_key =
                    Some(decode_key(value).ok_or_else(|| invalid("повреждён PublicKey"))?);
            }
            ("peer", "presharedkey") => {
                peers.last_mut().unwrap().preshared_key =
                    Some(decode_key(value).ok_or_else(|| invalid("повреждён PresharedKey"))?);
            }
            ("peer", "allowedips") => {
                for item in list(value) {
                    let net = parse_net(item).ok_or_else(|| bad("AllowedIPs"))?;
                    peers.last_mut().unwrap().allowed_ips.push(net.trunc());
                }
            }
            ("peer", "endpoint") => {
                peers.last_mut().unwrap().endpoint = Some(parse_endpoint(value).ok_or_else(|| bad("Endpoint"))?);
            }
            ("peer", "persistentkeepalive") => {
                let keepalive = if value.eq_ignore_ascii_case("off") { 0 } else {
                    value.parse::<u16>().map_err(|_| bad("PersistentKeepalive"))?
                };
                peers.last_mut().unwrap().keepalive = Some(keepalive).filter(|k| *k != 0);
            }
            _ => return Err(invalid(&format!("неподдерживаемый параметр в строке {line_no}"))),
        }
    }
    let private_key = private_key.ok_or_else(|| invalid("нет [Interface] с PrivateKey"))?;
    if peers.is_empty() || peers.iter().any(|peer| peer.public_key.is_none()) {
        return Err(invalid("нет [Peer] с PublicKey"));
    }
    if addresses.is_empty() {
        return Err(invalid("нет Address в [Interface]"));
    }
    let peers = peers.into_iter().map(|peer| {
        Ok(ParsedPeer {
            public_key: peer.public_key.unwrap(),
            preshared_key: peer.preshared_key,
            allowed_ips: peer.allowed_ips,
            endpoint: peer.endpoint.ok_or_else(|| invalid("нет Endpoint в [Peer]"))?,
            keepalive: peer.keepalive,
        })
    }).collect::<Result<Vec<_>, String>>()?;
    Ok(Parsed { private_key, listen_port, addresses, dns, mtu, peers })
}

/// Проверяет конфиг целиком (тем же разбором, что и при подключении) и
/// возвращает нормализованный текст для сохранения.
#[cfg_attr(not(test), allow(dead_code))]
pub fn validate(text: &str) -> Result<String, String> {
    parse(text)?;
    Ok(text.trim().trim_start_matches('\u{feff}').trim().to_string())
}

#[cfg_attr(not(test), allow(dead_code))]
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
    fn parses_everything_the_tunnel_needs() {
        let parsed = parse(&fixture().replace("Address = 10.0.0.2/32", "Address = 10.0.0.2, fd00::2/128")
            .replace("DNS = 1.1.1.1", "DNS = 1.1.1.1, home.lan, 2606:4700::1111\nMTU = 1280")
            .replace("0.0.0.0/0, ::/0", "10.1.2.3/24, ::/0")).unwrap();
        assert_eq!(parsed.addresses.iter().map(|a| a.to_string()).collect::<Vec<_>>(), ["10.0.0.2/32", "fd00::2/128"]);
        assert_eq!(parsed.dns.len(), 2);
        assert_eq!(parsed.mtu, Some(1280));
        let peer = &parsed.peers[0];
        assert_eq!(peer.allowed_ips.iter().map(|a| a.to_string()).collect::<Vec<_>>(), ["10.1.2.0/24", "::/0"]);
        assert_eq!(peer.endpoint, ("vpn.example".to_string(), 51820));
        assert_eq!(peer.keepalive, Some(25));
        let v6 = parse(&fixture().replace("vpn.example:51820", "[2001:db8::1]:443")).unwrap();
        assert_eq!(v6.peers[0].endpoint, ("2001:db8::1".to_string(), 443));
    }

    #[test]
    fn rejects_settings_the_tunnel_cannot_use() {
        for (from, to) in [("Address = 10.0.0.2/32", "Address = ten"), ("Address = 10.0.0.2/32\n", ""),
            ("vpn.example:51820", "vpn.example"), ("vpn.example:51820", "vpn.example:0"),
            ("Endpoint = vpn.example:51820\n", ""), ("0.0.0.0/0, ::/0", "0.0.0.0/33"),
            ("PersistentKeepalive = 25", "PersistentKeepalive = soon")] {
            assert!(validate(&fixture().replace(from, to)).is_err(), "{to}");
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
