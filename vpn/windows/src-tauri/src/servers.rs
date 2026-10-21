//! Дополнительные серверы рядом с основным ключом от бота. Основной ключ
//! (key.txt) не трогаем: он по-прежнему меняет страну через сервер ключей.
//! Здесь — свои полные конфиги WireGuard (например, запасной сервер), между
//! которыми можно переключаться в том же окне «Сервер». Всё лежит в одном
//! файле servers.json в папке данных приложения.
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::Path;

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub struct Saved {
    pub id: String,
    pub name: String,
    pub config: String,
}

#[derive(Serialize, Deserialize, Default, Debug)]
pub struct Store {
    /// None — подключаемся основным ключом, Some(id) — этим сервером.
    pub selected: Option<String>,
    pub servers: Vec<Saved>,
}

/// Для окна «Сервер»: конфиг наружу не отдаём, только имя.
#[derive(Serialize, Clone, Debug, PartialEq)]
pub struct Entry {
    pub id: String,
    pub name: String,
}

impl Store {
    pub fn load(path: &Path) -> Store {
        fs::read_to_string(path)
            .ok()
            .and_then(|text| serde_json::from_str::<Store>(&text).ok())
            .map(Store::normalized)
            .unwrap_or_default()
    }

    pub fn save(&self, path: &Path) -> Result<(), String> {
        let text = serde_json::to_string_pretty(self).map_err(|e| e.to_string())?;
        fs::write(path, text).map_err(|e| e.to_string())
    }

    /// Выбранный сервер, которого уже нет, означает «основной ключ».
    fn normalized(mut self) -> Store {
        if let Some(id) = &self.selected {
            if !self.servers.iter().any(|server| &server.id == id) {
                self.selected = None;
            }
        }
        self
    }

    pub fn entries(&self) -> Vec<Entry> {
        self.servers.iter().map(|s| Entry { id: s.id.clone(), name: s.name.clone() }).collect()
    }

    pub fn selected_config(&self) -> Option<&str> {
        let id = self.selected.as_ref()?;
        self.servers.iter().find(|s| &s.id == id).map(|s| s.config.as_str())
    }

    /// Добавляет проверенный конфиг и сразу делает его выбранным. Тот же
    /// конфиг второй раз не дублируется — просто выбирается.
    pub fn add(&mut self, config: String, id: String) -> Result<Entry, String> {
        let parsed = crate::config::parse(&config)?;
        let config = crate::config::validate(&config)?;
        if let Some(existing) = self.servers.iter().find(|s| s.config == config) {
            self.selected = Some(existing.id.clone());
            return Ok(Entry { id: existing.id.clone(), name: existing.name.clone() });
        }
        let host = parsed.peers.first().map(|p| p.endpoint.0.clone()).unwrap_or_default();
        let base = if host.is_empty() { "Сервер".to_string() } else { format!("Сервер {host}") };
        let taken = |name: &str| self.servers.iter().any(|s| s.name == name);
        let mut name = base.clone();
        let mut n = 2;
        while taken(&name) {
            name = format!("{base} ({n})");
            n += 1;
        }
        self.servers.push(Saved { id: id.clone(), name: name.clone(), config });
        self.selected = Some(id.clone());
        Ok(Entry { id, name })
    }

    pub fn select(&mut self, id: Option<String>) -> Result<(), String> {
        if let Some(id) = &id {
            if !self.servers.iter().any(|s| &s.id == id) {
                return Err("Такого сервера уже нет в списке".to_string());
            }
        }
        self.selected = id;
        Ok(())
    }

    /// Возвращает true, если удалили именно выбранный сервер — тогда
    /// подключение переходит на основной ключ.
    pub fn remove(&mut self, id: &str) -> bool {
        self.servers.retain(|s| s.id != id);
        if self.selected.as_deref() == Some(id) {
            self.selected = None;
            return true;
        }
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use base64::Engine;

    fn conf(host: &str, key_byte: u8) -> String {
        let key = base64::engine::general_purpose::STANDARD.encode([key_byte; 32]);
        format!("[Interface]\nPrivateKey = {key}\nAddress = 10.13.13.2/32\n\n[Peer]\nPublicKey = {key}\nAllowedIPs = 0.0.0.0/0\nEndpoint = {host}:51820\n")
    }

    #[test]
    fn added_server_is_selected_named_by_host_and_not_duplicated() {
        let mut store = Store::default();
        let first = store.add(conf("77.91.72.22", 1), "a".into()).unwrap();
        assert_eq!(first.name, "Сервер 77.91.72.22");
        assert_eq!(store.selected.as_deref(), Some("a"));
        assert!(store.selected_config().unwrap().contains("77.91.72.22"));
        let again = store.add(conf("77.91.72.22", 1), "b".into()).unwrap();
        assert_eq!(again.id, "a");
        assert_eq!(store.servers.len(), 1);
        let second = store.add(conf("77.91.72.22", 2), "c".into()).unwrap();
        assert_eq!(second.name, "Сервер 77.91.72.22 (2)");
    }

    #[test]
    fn invalid_config_is_rejected_and_store_unchanged() {
        let mut store = Store::default();
        assert!(store.add("s".into(), "a".into()).is_err());
        assert!(store.servers.is_empty());
        assert_eq!(store.selected, None);
    }

    #[test]
    fn removing_or_losing_the_selected_server_falls_back_to_main_key() {
        let mut store = Store::default();
        store.add(conf("198.51.100.7", 1), "a".into()).unwrap();
        store.add(conf("198.51.100.8", 2), "b".into()).unwrap();
        assert!(!store.remove("a"));
        assert_eq!(store.selected.as_deref(), Some("b"));
        assert!(store.remove("b"));
        assert_eq!(store.selected, None);
        assert!(store.select(Some("missing".into())).is_err());

        let path = std::env::temp_dir().join(format!("ruvpn-servers-test-{}.json", std::process::id()));
        fs::write(&path, r#"{"selected":"gone","servers":[]}"#).unwrap();
        assert_eq!(Store::load(&path).selected, None);
        fs::write(&path, "not json").unwrap();
        assert!(Store::load(&path).servers.is_empty());
        fs::remove_file(path).unwrap();
    }
}
