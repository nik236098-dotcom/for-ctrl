//! Туннель целиком внутри приложения. Драйвер WireGuardNT поставляется в
//! самом установщике Ru VPN (wireguard.dll — официальная подписанная сборка
//! с download.wireguard.com, см. .github/workflows/build-vpn-windows.yml).
//! Ни отдельной программы WireGuard, ни служб Windows: адаптер создаётся
//! прямо этим процессом и живёт, пока он его держит. Закрыли приложение или
//! оно упало — Windows сама удаляет адаптер, зависнуть нечему.
use crate::config::Parsed;
use ipnet::IpNet;
use std::net::{SocketAddr, ToSocketAddrs};
use std::path::Path;
use std::sync::{Mutex, OnceLock};
use wireguard_nt::{Adapter, SetInterface, SetPeer, Wireguard};

/// Имя адаптера в «Сетевых подключениях». Не "ruvpn": так звался адаптер
/// старой версии на официальном WireGuard, и если тот где-то завис, новое
/// имя с ним не столкнётся.
pub const ADAPTER_NAME: &str = "Ru VPN";
/// Постоянный GUID — Windows узнаёт ту же сеть при каждом подключении и не
/// плодит «Сеть 2», «Сеть 3» со своим профилем брандмауэра.
const ADAPTER_GUID: u128 = 0x6d2b_7a51_93c4_4e0f_a8b2_52f1_0c3e_9d71;

static LIBRARY: OnceLock<Wireguard> = OnceLock::new();
static ACTIVE: Mutex<Option<Adapter>> = Mutex::new(None);

fn active() -> std::sync::MutexGuard<'static, Option<Adapter>> {
    ACTIVE.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn library(dll: &Path) -> Result<Wireguard, String> {
    if let Some(library) = LIBRARY.get() {
        return Ok(library.clone());
    }
    // SAFETY: грузим только wireguard.dll из папки установки самого
    // приложения (её путь даёт Tauri), не из поиска по системным путям.
    let library = unsafe { wireguard_nt::load_from_path(dll) }
        .map_err(|e| format!("Не найден драйвер VPN ({e}). Переустановите Ru VPN."))?;
    Ok(LIBRARY.get_or_init(|| library).clone())
}

/// Имя сервера в адрес — до создания адаптера, обычной сетью. IPv4 в
/// приоритете: он есть у всех, IPv6 у многих провайдеров нет.
fn resolve(host: &str, port: u16) -> Result<SocketAddr, String> {
    let addresses: Vec<SocketAddr> = (host, port).to_socket_addrs()
        .map_err(|e| format!("Не удалось найти сервер VPN {host}: {e}"))?
        .collect();
    addresses.iter().find(|a| a.is_ipv4()).or_else(|| addresses.first()).copied()
        .ok_or_else(|| format!("Не удалось найти сервер VPN {host}"))
}

/// Поднимает туннель. Уже поднят — ничего не делает. Любая ошибка по пути
/// удаляет недонастроенный адаптер (Drop у Adapter), полусостояний нет.
pub fn start(dll: &Path, config: &Parsed) -> Result<(), String> {
    let mut active = active();
    if active.is_some() {
        return Ok(());
    }
    let peers = config.peers.iter().map(|peer| Ok(SetPeer {
        public_key: Some(peer.public_key),
        preshared_key: peer.preshared_key,
        keep_alive: peer.keepalive,
        endpoint: resolve(&peer.endpoint.0, peer.endpoint.1)?,
        allowed_ips: peer.allowed_ips.clone(),
    })).collect::<Result<Vec<_>, String>>()?;
    let routes: Vec<IpNet> = peers.iter().flat_map(|peer| peer.allowed_ips.iter().copied()).collect();

    let library = library(dll)?;
    // Первый вызов на компьютере сам ставит драйвер из wireguard.dll —
    // это несколько секунд, дальше мгновенно.
    let adapter = Adapter::create(&library, ADAPTER_NAME, ADAPTER_NAME, Some(ADAPTER_GUID))
        .map_err(|e| format!("Не удалось создать сетевой адаптер VPN: {e}"))?;
    adapter.set_config(&SetInterface {
        listen_port: config.listen_port,
        public_key: None,
        private_key: Some(config.private_key),
        peers,
    }).map_err(|e| format!("Драйвер VPN не принял настройки: {e}"))?;
    adapter.up().map_err(|e| format!("Не удалось включить адаптер VPN: {e}"))?;
    crate::net::configure(adapter.get_luid(), ADAPTER_NAME, &config.addresses, &routes, config.mtu, &config.dns)
        .map_err(|e| format!("Не удалось настроить сеть VPN: {e}"))?;
    *active = Some(adapter);
    Ok(())
}

/// Выключает и удаляет адаптер. Маршруты, адреса и DNS уходят вместе с ним.
pub fn stop() {
    if let Some(adapter) = active().take() {
        adapter.down().ok();
        drop(adapter);
    }
}

pub struct Stats {
    pub up: bool,
    pub rx: u64,
    pub tx: u64,
    /// Был ли хоть один обмен ключами с сервером: без него трафика нет.
    pub handshake: bool,
}

pub fn stats() -> Stats {
    let active = active();
    let Some(adapter) = active.as_ref() else {
        return Stats { up: false, rx: 0, tx: 0, handshake: false };
    };
    let up = adapter.is_up().unwrap_or(false);
    // get_config у крейта на неожиданном ответе драйвера паникует (assert);
    // счётчики — не повод ронять приложение.
    let config = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| adapter.get_config())).ok();
    let (rx, tx, handshake) = config.map(|config| config.peers.iter().fold((0, 0, false), |(rx, tx, hs), peer| {
        (rx + peer.rx_bytes, tx + peer.tx_bytes, hs || peer.last_handshake.is_some())
    })).unwrap_or((0, 0, false));
    Stats { up, rx, tx, handshake }
}
