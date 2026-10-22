// Ru VPN для Windows. Туннель поднимает само приложение через встроенный
// драйвер WireGuardNT (wireguard.dll в установщике, см. tunnel.rs): ни
// отдельной программы WireGuard, ни служб Windows. Ключей тоже нет: при
// первом подключении к серверу приложение само регистрирует этот компьютер
// (provision.rs, на сервере — vpn/server/provision.py).
#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

use serde::Serialize;
use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::time::Duration;
use tauri::Manager;

mod config;
mod provision;
#[cfg(windows)]
mod net;
#[cfg(windows)]
mod tunnel;

// Подключение и отключение — строго по одному, даже при двойном клике.
static TUNNEL_OPERATION: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

async fn command_output(command: Command, timeout: Duration) -> Result<std::process::Output, String> {
    let mut command = tokio::process::Command::from(command);
    command.kill_on_drop(true);
    tokio::time::timeout(timeout, command.output()).await
        .map_err(|_| "Команда Windows не завершилась вовремя. Попробуйте ещё раз.".to_string())?
        .map_err(|e| e.to_string())
}

/// Обычный `Command::new` из оконного приложения на Windows на долю
/// секунды показывает мелькающее окно консоли. CREATE_NO_WINDOW убирает это
/// окно полностью, поведение команды не меняет.
fn new_command(program: impl AsRef<std::ffi::OsStr>) -> Command {
    let mut cmd = Command::new(program);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd
}

// --- серверы ------------------------------------------------------------

fn store_file(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let dir = app.path().app_data_dir().map_err(|e| e.to_string())?;
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    Ok(dir.join("devices.json"))
}

#[derive(Serialize)]
struct ServerEntry {
    id: String,
    name: String,
    country: String,
}

#[derive(Serialize)]
struct ServerList {
    selected: Option<String>,
    servers: Vec<ServerEntry>,
}

#[tauri::command]
fn list_servers(app: tauri::AppHandle) -> ServerList {
    let servers = provision::builtin();
    let store = store_file(&app).map(|path| provision::Store::load(&path)).unwrap_or_default();
    ServerList {
        selected: store.selected_server(&servers).map(|s| s.id),
        servers: servers.into_iter().map(|s| ServerEntry { id: s.id, name: s.name, country: s.country }).collect(),
    }
}

#[tauri::command]
fn select_server(app: tauri::AppHandle, id: String) -> Result<(), String> {
    if !provision::builtin().iter().any(|s| s.id == id) {
        return Err("Такого сервера нет".to_string());
    }
    let path = store_file(&app)?;
    let mut store = provision::Store::load(&path);
    store.selected = Some(id);
    store.save(&path)
}

/// Конфиг для выбранного сервера. Ключ этого компьютера создаётся один раз
/// на сервер; при каждом подключении регистрация повторяется (сервер просто
/// отдаёт актуальные настройки и заново добавляет устройство, если его
/// переустановили). Сервер регистрации недоступен, но настройки уже есть —
/// подключаемся с ними.
async fn config_for_selected(app: &tauri::AppHandle) -> Result<String, String> {
    let path = store_file(app)?;
    let servers = provision::builtin();
    let mut store = provision::Store::load(&path);
    let server = store.selected_server(&servers).ok_or_else(|| "В приложении нет серверов".to_string())?;
    let mut registration = match store.registrations.get(&server.id) {
        Some(existing) => existing.clone(),
        None => {
            let (private_key, public_key) = provision::generate_keypair()?;
            provision::Registration { private_key, public_key, config: String::new() }
        }
    };
    let device = std::env::var("COMPUTERNAME").unwrap_or_else(|_| "windows".to_string());
    match provision::register(&server, &registration.public_key, &device).await {
        Ok(config) => registration.config = config,
        Err(error) if registration.config.is_empty() => return Err(error),
        Err(_) => {}
    }
    store.registrations.insert(server.id.clone(), registration.clone());
    store.save(&path)?;
    Ok(provision::with_private_key(&registration.config, &registration.private_key))
}

// --- туннель -------------------------------------------------------------

/// wireguard.dll из папки установки (Tauri кладёт туда resources/ из
/// tauri.conf.json).
fn driver_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    app.path()
        .resolve("resources/wireguard.dll", tauri::path::BaseDirectory::Resource)
        .map_err(|e| format!("Не найден драйвер VPN ({e}). Переустановите Ru VPN."))
}

#[tauri::command]
async fn connect(app: tauri::AppHandle) -> Result<(), String> {
    let _operation = TUNNEL_OPERATION.try_lock()
        .map_err(|_| "Подключение уже меняется. Дождитесь завершения операции.".to_string())?;
    let config_text = config_for_selected(&app).await?;
    start_tunnel(config_text, || driver_path(&app)).await
}

async fn start_tunnel(config_text: String, driver: impl FnOnce() -> Result<PathBuf, String>) -> Result<(), String> {
    // Битый конфиг отвергается до любых действий с сетью.
    let parsed = config::parse(&config_text)?;
    let driver = driver()?;
    #[cfg(windows)]
    {
        tokio::task::spawn_blocking(move || tunnel::start(&driver, &parsed))
            .await
            .map_err(|e| e.to_string())?
    }
    #[cfg(not(windows))]
    {
        let _ = (driver, parsed);
        Err("Ru VPN подключается только в Windows".to_string())
    }
}

#[tauri::command]
async fn disconnect() -> Result<(), String> {
    let _operation = TUNNEL_OPERATION.try_lock()
        .map_err(|_| "Подключение уже меняется. Дождитесь завершения операции.".to_string())?;
    #[cfg(windows)]
    tokio::task::spawn_blocking(tunnel::stop).await.map_err(|e| e.to_string())?;
    Ok(())
}

#[derive(Serialize)]
struct TunnelStatus {
    up: bool,
    state: &'static str,
    transitioning: bool,
    rx: u64,
    tx: u64,
    handshake: bool,
}

#[tauri::command]
async fn tunnel_status() -> Result<TunnelStatus, String> {
    #[cfg(windows)]
    let (up, rx, tx, handshake) = {
        let stats = tokio::task::spawn_blocking(tunnel::stats).await.map_err(|e| e.to_string())?;
        (stats.up, stats.rx, stats.tx, stats.handshake)
    };
    #[cfg(not(windows))]
    let (up, rx, tx, handshake) = (false, 0, 0, false);
    Ok(TunnelStatus {
        up,
        state: if up { "running" } else { "stopped" },
        // Подключение и отключение происходят внутри самих команд connect /
        // disconnect — между вызовами туннель всегда в конечном состоянии.
        transitioning: false,
        rx,
        tx,
        handshake,
    })
}

/// Версии до 0.2.0 поднимали туннель службой официального WireGuard
/// (WireGuardTunnel$ruvpn). Если она осталась — особенно зависшая в
/// «Запускается», — её маршруты спорили бы с новым туннелем. Отключаем её
/// автозапуск (это работает даже у зависшей службы, и после перезагрузки
/// она больше не поднимется), пробуем остановить и удалить. Всё — лучшая
/// попытка в фоне, на подключение не влияет.
#[cfg(windows)]
async fn retire_old_wireguard_service() {
    const OLD_SERVICE: &str = "WireGuardTunnel$ruvpn";
    let mut disable = new_command("sc.exe");
    disable.args(["config", OLD_SERVICE, "start=", "disabled"]);
    match command_output(disable, Duration::from_secs(10)).await {
        Ok(output) if output.status.success() => {}
        // Службы нет (код 1060) — значит, и делать нечего.
        _ => return,
    }
    let program_files = std::env::var("ProgramFiles").unwrap_or_else(|_| r"C:\Program Files".to_string());
    let wireguard = PathBuf::from(program_files).join("WireGuard").join("wireguard.exe");
    if wireguard.exists() {
        let mut uninstall = new_command(&wireguard);
        uninstall.args(["/uninstalltunnelservice", "ruvpn"]);
        command_output(uninstall, Duration::from_secs(30)).await.ok();
    }
    let mut delete = new_command("sc.exe");
    delete.args(["delete", OLD_SERVICE]);
    command_output(delete, Duration::from_secs(10)).await.ok();
}

// --- свой ip --------------------------------------------------------------

#[derive(Serialize)]
struct IpResult {
    ip: String,
    country_code: String,
}

#[tauri::command]
async fn check_ip() -> Result<IpResult, String> {
    let response = reqwest::Client::new()
        .get("https://ipwho.is/")
        .timeout(Duration::from_secs(10))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let response = response.error_for_status().map_err(|e| e.to_string())?;
    let json: serde_json::Value = response.json().await.map_err(|e| e.to_string())?;
    let ip = json.get("ip").and_then(|value| value.as_str())
        .filter(|value| value.parse::<std::net::IpAddr>().is_ok())
        .ok_or_else(|| "сервис не вернул IP-адрес".to_string())?;
    if json.get("success").and_then(|value| value.as_bool()) == Some(false) {
        return Err("сервис проверки IP недоступен".to_string());
    }
    Ok(IpResult {
        ip: ip.to_string(),
        country_code: json
            .get("country_code")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
    })
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .invoke_handler(tauri::generate_handler![
            list_servers,
            select_server,
            connect,
            disconnect,
            tunnel_status,
            check_ip,
        ])
        .setup(|_app| {
            #[cfg(windows)]
            tauri::async_runtime::spawn(retire_old_wireguard_service());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("ошибка запуска Ru VPN")
        .run(|_app, event| {
            // Адаптер и так исчезает вместе с процессом; здесь — аккуратно и сразу.
            if let tauri::RunEvent::Exit = event {
                #[cfg(windows)]
                tunnel::stop();
            }
        });
}

#[cfg(test)]
mod tests {
    #[test]
    fn invalid_config_is_rejected_before_loading_the_driver() {
        tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
            let error = super::start_tunnel("s".to_string(), || panic!("driver must not be touched")).await.unwrap_err();
            assert!(error.starts_with("Неверная конфигурация VPN:"));
        });
    }

    #[cfg(windows)]
    #[test]
    fn a_stalled_child_command_has_a_deadline() {
        let runtime = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        runtime.block_on(async {
            let mut command = super::new_command("powershell.exe");
            command.args(["-NoProfile", "-NonInteractive", "-Command", "Start-Sleep -Seconds 30"]);
            let start = std::time::Instant::now();
            let result = super::command_output(command, std::time::Duration::from_millis(200)).await;
            assert!(result.is_err());
            assert!(start.elapsed() < std::time::Duration::from_secs(5));
        });
    }
}

