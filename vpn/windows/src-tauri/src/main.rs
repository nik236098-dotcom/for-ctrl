// Ru VPN для Windows — тот же дизайн, что в Android-приложении. Туннель
// поднимает само приложение через встроенный драйвер WireGuardNT
// (wireguard.dll в установщике, см. tunnel.rs): ни отдельной программы
// WireGuard, ни служб Windows.
#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

use serde::Serialize;
use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::time::Duration;
use tauri::Manager;

mod config;
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

const PREFIX: &str = "ruvpn://";
const DEMO_KEY: &str = "test1590";
const DEMO_SENTINEL: &str = "demo:test1590";
const DEFAULT_COUNTRY: &str = "ru";

/// Адрес сервера ключей — вшивается на этапе сборки (см. .github/workflows,
/// переменная окружения KEY_SERVER_URL при `cargo build`), точно так же, как
/// KEY_SERVER_URL встраивается в Android через buildConfigField.
fn key_server_url() -> &'static str {
    option_env!("KEY_SERVER_URL").unwrap_or("")
}

// --- хранилище ключа ---------------------------------------------------

fn key_file(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let dir = app.path().app_data_dir().map_err(|e| e.to_string())?;
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    Ok(dir.join("key.txt"))
}

fn meta_file(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let dir = app.path().app_data_dir().map_err(|e| e.to_string())?;
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    Ok(dir.join("key_meta.json"))
}

/// Код (если ключ пришёл коротким кодом от бота — не вставленным вручную
/// конфигом и не старым base64-ключом) и текущая выбранная страна. Только
/// имея код можно сменить страну позже — см. [switch_country].
#[derive(Serialize, serde::Deserialize)]
struct Meta {
    code: Option<String>,
    country: String,
}

fn read_meta(app: &tauri::AppHandle) -> Option<Meta> {
    let path = meta_file(app).ok()?;
    let text = fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

fn write_meta(app: &tauri::AppHandle, code: Option<&str>, country: &str) -> Result<(), String> {
    let path = meta_file(app)?;
    match code {
        None => {
            fs::remove_file(&path).ok();
            Ok(())
        }
        Some(code) => {
            let meta = Meta {
                code: Some(code.to_string()),
                country: country.to_string(),
            };
            let text = serde_json::to_string(&meta).map_err(|e| e.to_string())?;
            fs::write(path, text).map_err(|e| e.to_string())
        }
    }
}

#[tauri::command]
fn load_key(app: tauri::AppHandle) -> Option<String> {
    let path = key_file(&app).ok()?;
    let text = fs::read_to_string(path).ok()?;
    let trimmed = text.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
}

/// [code] — короткий код, которым это получено (см. [resolve_key]), если
/// получено им; None — вставили готовый конфиг или старый base64-ключ,
/// смена страны для такого недоступна.
#[tauri::command]
fn save_key(app: tauri::AppHandle, text: String, code: Option<String>) -> Result<(), String> {
    let path = key_file(&app)?;
    config::write_validated(&path, &text)?;
    write_meta(&app, code.as_deref(), DEFAULT_COUNTRY)
}

#[tauri::command]
fn save_demo(app: tauri::AppHandle) -> Result<(), String> {
    let path = key_file(&app)?;
    fs::write(path, DEMO_SENTINEL).map_err(|e| e.to_string())?;
    write_meta(&app, None, DEFAULT_COUNTRY)
}

#[tauri::command]
fn is_demo(app: tauri::AppHandle) -> bool {
    load_key(app).as_deref() == Some(DEMO_SENTINEL)
}

/// Текущая выбранная страна ("ru", если ключ не менял страну ни разу или
/// у него нет кода для смены).
#[tauri::command]
fn current_country(app: tauri::AppHandle) -> String {
    read_meta(&app)
        .map(|meta| meta.country)
        .unwrap_or_else(|| DEFAULT_COUNTRY.to_string())
}

/// Доступна ли смена страны для сохранённого ключа — только для тех, что
/// пришли коротким кодом.
#[tauri::command]
fn has_switchable_code(app: tauri::AppHandle) -> bool {
    read_meta(&app).and_then(|meta| meta.code).is_some()
}

/// Сетевые ошибки и старый сервер без /status не означают отзыв ключа.
/// Проверка ничего не меняет в сохранённом конфиге или на сервере.
#[tauri::command]
async fn key_status(app: tauri::AppHandle) -> &'static str {
    let meta = match read_meta(&app) {
        Some(meta) => meta,
        None => return "unmanaged",
    };
    let code = match meta.code {
        Some(code) if is_short_code(&code) => code,
        _ => return "unmanaged",
    };
    let base = key_server_url().trim_end_matches('/');
    if base.is_empty() {
        return "unknown";
    }
    let response = match reqwest::Client::new()
        .get(format!("{base}/key/{code}/status"))
        .query(&[("country", meta.country.as_str())])
        .timeout(Duration::from_secs(10))
        .send()
        .await
    {
        Ok(response) => response,
        Err(_) => return "unknown",
    };
    let status = response.status().as_u16();
    let body = match response.json::<serde_json::Value>().await {
        Ok(body) => body,
        Err(_) => return "unknown",
    };
    key_response_status(status, &body)
}

fn key_response_status(status: u16, body: &serde_json::Value) -> &'static str {
    if status != 200 {
        return "unknown";
    }
    match body.get("status").and_then(|value| value.as_str()) {
        Some("active") => "active",
        Some("revoked") => "revoked",
        _ => "unknown",
    }
}

/// Меняет сервер (страну) для уже сохранённого ключа: тот же самый код,
/// что уже ввели, просто переспрашивается у сервера ключей с другой
/// страной. Возвращает false, если у сохранённого ключа нет кода — тогда
/// снаружи ничего не менялось (это вставленный вручную конфиг).
#[tauri::command]
async fn switch_country(app: tauri::AppHandle, country: String) -> Result<bool, String> {
    let meta = match read_meta(&app) {
        Some(meta) => meta,
        None => return Ok(false),
    };
    let code = match meta.code {
        Some(code) => code,
        None => return Ok(false),
    };
    if meta.country == country {
        return Ok(true);
    }
    let text = fetch_config(&code, &country).await?;
    let key_path = key_file(&app)?;
    config::write_validated(&key_path, &text)?;
    write_meta(&app, Some(&code), &country)?;
    Ok(true)
}

// --- разбор ключа --------------------------------------------------------

/// Тестовый ключ (см. Android KeyStore.DEMO_KEY) — только чтобы посмотреть
/// анимацию кнопки, без настоящего подключения.
#[tauri::command]
fn is_demo_key(text: String) -> bool {
    let value = text.trim();
    let payload = if value.to_ascii_lowercase().starts_with(&PREFIX.to_ascii_lowercase()) {
        value[PREFIX.len()..].trim()
    } else {
        value
    };
    payload.eq_ignore_ascii_case(DEMO_KEY)
}

fn is_short_code(payload: &str) -> bool {
    let len = payload.chars().count();
    (4..=16).contains(&len) && payload.chars().all(|c| c.is_ascii_alphanumeric())
}

/// Быстрая проверка «похоже на ключ» — чтобы подставлять из буфера обмена.
#[tauri::command]
fn looks_like_key(text: String) -> bool {
    let value = text.trim();
    if value.to_ascii_lowercase().starts_with(&PREFIX.to_ascii_lowercase())
        || value.contains("[Interface]")
    {
        return true;
    }
    is_short_code(value)
}

async fn fetch_config(code: &str, country: &str) -> Result<String, String> {
    let base = key_server_url().trim_end_matches('/');
    if base.is_empty() {
        return Err("адрес сервера ключей не задан в этой сборке".to_string());
    }
    let url = format!("{base}/key/{code}?country={country}");
    let response = reqwest::Client::new()
        .get(&url)
        .timeout(Duration::from_secs(10))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if !response.status().is_success() {
        return Err(format!("сервер ответил {}", response.status()));
    }
    let text = response.text().await.map_err(|e| e.to_string())?;
    config::validate(&text)
}

/// Старый формат ключа: весь конфиг, упакованный в base64 прямо в строке.
fn decode_legacy(payload: &str) -> Result<String, String> {
    use base64::Engine;
    let mut normalized: String = payload.chars().filter(|c| !c.is_whitespace()).collect();
    normalized = normalized.replace('-', "+").replace('_', "/");
    while normalized.len() % 4 != 0 {
        normalized.push('=');
    }
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(&normalized)
        .map_err(|e| e.to_string())?;
    String::from_utf8(bytes).map_err(|e| e.to_string())
}

/// Результат [resolve_key]: готовый конфиг и код, которым он получен (None —
/// если это был вставленный вручную конфиг или старый base64-ключ, без
/// короткого кода — тогда смена страны для него недоступна).
#[derive(Serialize)]
struct ResolvedKey {
    text: String,
    code: Option<String>,
}

/// Превращает вставленный текст в настоящий конфиг тунеля — см.
/// KeyStore.kt::resolve на Android, логика та же самая. Страна при первой
/// вставке всегда домашняя (DEFAULT_COUNTRY) — сменить её можно уже после,
/// через [switch_country].
#[tauri::command]
async fn resolve_key(text: String) -> Result<ResolvedKey, String> {
    let value = text.trim().to_string();
    let lower = value.to_ascii_lowercase();
    if !lower.starts_with(&PREFIX.to_ascii_lowercase()) {
        return if is_short_code(&value) {
            let config = fetch_config(&value, DEFAULT_COUNTRY).await?;
            Ok(ResolvedKey { text: config, code: Some(value) })
        } else {
            Ok(ResolvedKey { text: config::validate(&value)?, code: None })
        };
    }
    let payload = value[PREFIX.len()..].trim().to_string();
    if is_short_code(&payload) {
        let config = fetch_config(&payload, DEFAULT_COUNTRY).await?;
        Ok(ResolvedKey { text: config, code: Some(payload) })
    } else {
        Ok(ResolvedKey { text: config::validate(&decode_legacy(&payload)?)?, code: None })
    }
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
async fn connect(app: tauri::AppHandle, config_text: String) -> Result<(), String> {
    connect_with(config_text, || driver_path(&app)).await
}

async fn connect_with(config_text: String, driver: impl FnOnce() -> Result<PathBuf, String>) -> Result<(), String> {
    let _operation = TUNNEL_OPERATION.try_lock()
        .map_err(|_| "Подключение уже меняется. Дождитесь завершения операции.".to_string())?;
    // Битый сохранённый ключ отвергается до любых действий с сетью.
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

#[tauri::command]
fn clipboard_text() -> Option<String> {
    arboard::Clipboard::new().ok()?.get_text().ok()
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
            load_key,
            save_key,
            save_demo,
            is_demo,
            is_demo_key,
            looks_like_key,
            resolve_key,
            current_country,
            has_switchable_code,
            key_status,
            switch_country,
            connect,
            disconnect,
            tunnel_status,
            check_ip,
            clipboard_text,
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
    use super::key_response_status;
    use serde_json::json;

    #[test]
    fn invalid_raw_and_legacy_inputs_are_rejected_before_saving() {
        use base64::Engine;
        tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
            for input in ["s".to_string(), "<html>upstream unavailable</html>".to_string(),
                format!("ruvpn://{}", base64::engine::general_purpose::STANDARD.encode("not a wireguard configuration"))] {
                let error = super::resolve_key(input).await.err().expect("invalid input must be rejected");
                assert!(error.starts_with("Неверная конфигурация VPN:"));
            }
        });
    }

    #[test]
    fn invalid_saved_config_is_rejected_before_loading_the_driver() {
        tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
            let error = super::connect_with("s".to_string(), || panic!("driver must not be touched")).await.unwrap_err();
            assert!(error.starts_with("Неверная конфигурация VPN:"));
        });
    }

    #[test]
    fn only_explicit_successful_revocation_is_actionable() {
        assert_eq!(key_response_status(200, &json!({"status": "revoked"})), "revoked");
        assert_eq!(key_response_status(200, &json!({"status": "active"})), "active");
        for status in [401, 403, 404, 429, 500, 502, 503] {
            assert_eq!(key_response_status(status, &json!({"status": "revoked"})), "unknown");
        }
        for body in [json!({}), json!({"error": "revoked"}), json!({"status": "unknown"}), json!(null)] {
            assert_eq!(key_response_status(200, &body), "unknown");
        }
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

