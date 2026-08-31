// Ru VPN для Windows — тот же дизайн, что в Android-приложении, но
// туннель поднимает не своя реализация WireGuard, а официальный
// wireguard.exe (WireGuard for Windows), которым это приложение просто
// управляет из-под капота. Причина — см. vpn/windows/README.md: подписанный
// ядерный драйвер (Wintun) с нуля своими силами не собрать и не подписать,
// а официальный уже есть и работает.
#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

use serde::Serialize;
use std::fs;
use std::io::{Read, Write};
use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;
use tauri::Manager;

const PREFIX: &str = "ruvpn://";
const DEMO_KEY: &str = "test1590";
const DEMO_SENTINEL: &str = "demo:test1590";
const TUNNEL_NAME: &str = "ruvpn";
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
    fs::write(path, text.trim()).map_err(|e| e.to_string())?;
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
    // Проверку, что это действительно рабочий конфиг тунеля, здесь не
    // делаем (в отличие от Android с её встроенным парсером WireGuard) —
    // сервер ключей и так отдаёт уже готовый конфиг; настоящая проверка
    // произойдёт при попытке подключиться (connect() отдаст ошибку от
    // самого wireguard.exe, если там что-то не так).
    let key_path = key_file(&app)?;
    fs::write(key_path, text.trim()).map_err(|e| e.to_string())?;
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
    response.text().await.map_err(|e| e.to_string())
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
            Ok(ResolvedKey { text: value, code: None })
        };
    }
    let payload = value[PREFIX.len()..].trim().to_string();
    if is_short_code(&payload) {
        let config = fetch_config(&payload, DEFAULT_COUNTRY).await?;
        Ok(ResolvedKey { text: config, code: Some(payload) })
    } else {
        Ok(ResolvedKey { text: decode_legacy(&payload)?, code: None })
    }
}

// --- официальный WireGuard for Windows -----------------------------------

fn wireguard_exe() -> PathBuf {
    let program_files =
        std::env::var("ProgramFiles").unwrap_or_else(|_| r"C:\Program Files".to_string());
    PathBuf::from(program_files).join("WireGuard").join("wireguard.exe")
}

#[tauri::command]
fn wireguard_installed() -> bool {
    wireguard_exe().exists()
}

async fn download_installer() -> Result<PathBuf, String> {
    let url = "https://download.wireguard.com/windows-client/wireguard-installer.exe";
    let bytes = reqwest::get(url)
        .await
        .map_err(|e| e.to_string())?
        .bytes()
        .await
        .map_err(|e| e.to_string())?;
    let path = std::env::temp_dir().join("wireguard-installer.exe");
    fs::write(&path, &bytes).map_err(|e| e.to_string())?;
    Ok(path)
}

/// Тихая (насколько это вообще возможно) установка официального клиента:
/// приложение уже само запущено от администратора (windows/app.manifest),
/// поэтому дочерний установщик наследует те же права и не спрашивает UAC
/// повторно. Собственное окно официального установщика мигнёт один раз —
/// у него нет мастера «Далее/Далее/Готово», он сам закрывается по
/// завершении. Задокументированного флага полностью безголовой установки у
/// него нет, так что это лучшее, что можно сделать без переупаковки MSI.
#[tauri::command]
async fn install_wireguard() -> Result<(), String> {
    if wireguard_installed() {
        return Ok(());
    }
    let installer = download_installer().await?;
    let status = Command::new(&installer)
        .status()
        .map_err(|e| format!("не удалось запустить установщик: {e}"))?;
    fs::remove_file(&installer).ok();
    if !status.success() {
        return Err(format!(
            "установщик WireGuard завершился с кодом {:?}",
            status.code()
        ));
    }
    if wireguard_installed() {
        Ok(())
    } else {
        Err("после установки wireguard.exe не найден — установите вручную с wireguard.com/install".to_string())
    }
}

#[tauri::command]
async fn connect(config_text: String) -> Result<(), String> {
    if !wireguard_installed() {
        install_wireguard().await?;
    }
    let conf_dir = std::env::temp_dir().join("ruvpn-windows");
    fs::create_dir_all(&conf_dir).map_err(|e| e.to_string())?;
    let conf_path = conf_dir.join(format!("{TUNNEL_NAME}.conf"));
    fs::write(&conf_path, &config_text).map_err(|e| e.to_string())?;

    let output = Command::new(wireguard_exe())
        .arg("/installtunnelservice")
        .arg(&conf_path)
        .output()
        .map_err(|e| e.to_string());

    // /installtunnelservice считывает конфиг и запоминает его сам внутри
    // сервиса — файл на диске больше не нужен, а держать там приватный
    // ключ незачем.
    fs::remove_file(&conf_path).ok();

    let output = output?;
    if !output.status.success() {
        return Err(command_error(&output.stderr, output.status.code()));
    }
    Ok(())
}

#[tauri::command]
async fn disconnect() -> Result<(), String> {
    let output = Command::new(wireguard_exe())
        .arg("/uninstalltunnelservice")
        .arg(TUNNEL_NAME)
        .output()
        .map_err(|e| e.to_string())?;
    if !output.status.success() {
        return Err(command_error(&output.stderr, output.status.code()));
    }
    Ok(())
}

fn command_error(stderr: &[u8], code: Option<i32>) -> String {
    let text = String::from_utf8_lossy(stderr).trim().to_string();
    if text.is_empty() {
        format!("wireguard.exe вернул код {code:?}")
    } else {
        text
    }
}

#[derive(Serialize)]
struct TunnelStatus {
    up: bool,
    rx: u64,
    tx: u64,
}

fn service_running(service_name: &str) -> bool {
    match Command::new("sc").arg("query").arg(service_name).output() {
        Ok(output) => String::from_utf8_lossy(&output.stdout).contains("RUNNING"),
        Err(_) => false,
    }
}

/// Если однажды не получилось прочитать статистику через именованный канал —
/// больше не пытаемся в этой сессии (см. комментарий у [read_transfer_stats_blocking]
/// про то, почему это не гарантировано).
static STATS_UNAVAILABLE: AtomicBool = AtomicBool::new(false);

#[tauri::command]
async fn tunnel_status() -> TunnelStatus {
    let up = service_running(&format!("WireGuardTunnel${TUNNEL_NAME}"));
    let (rx, tx) = if up { read_transfer_stats().await } else { (0, 0) };
    TunnelStatus { up, rx, tx }
}

async fn read_transfer_stats() -> (u64, u64) {
    if STATS_UNAVAILABLE.load(Ordering::Relaxed) {
        return (0, 0);
    }
    let result = tokio::time::timeout(
        Duration::from_millis(1200),
        tokio::task::spawn_blocking(read_transfer_stats_blocking),
    )
    .await;
    match result {
        Ok(Ok(Some(values))) => values,
        _ => {
            // Либо канала нет по этому пути, либо протокол не совпал с
            // ожидаемым — не настаиваем дальше в этом запуске приложения
            // (иначе на зависшем чтении будут копиться потоки при каждом
            // опросе раз в 2 секунды).
            STATS_UNAVAILABLE.store(true, Ordering::Relaxed);
            (0, 0)
        }
    }
}

/// ЛУЧШАЯ ПОПЫТКА, не проверено вживую на реальной Windows: WireGuard for
/// Windows должен отвечать на тот же UAPI-протокол (`get=1\n\n` →
/// `rx_bytes=…`/`tx_bytes=…` … `errno=0\n\n`), что и `wg show` на Linux, но
/// через именованный канал `\\.\pipe\WireGuard\<имя>` вместо unix-сокета.
/// Если имя канала или протокол не совпадут — просто не покажем
/// цифры трафика (см. [STATS_UNAVAILABLE]), подключение это не сломает.
fn read_transfer_stats_blocking() -> Option<(u64, u64)> {
    let pipe_path = format!(r"\\.\pipe\WireGuard\{TUNNEL_NAME}");
    let mut pipe = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(&pipe_path)
        .ok()?;
    pipe.write_all(b"get=1\n\n").ok()?;

    let mut buf = Vec::new();
    let mut chunk = [0u8; 4096];
    loop {
        let n = pipe.read(&mut chunk).ok()?;
        if n == 0 {
            break;
        }
        buf.extend_from_slice(&chunk[..n]);
        if buf.len() > 4 && buf.windows(2).any(|w| w == b"\n\n") {
            break;
        }
        if buf.len() > 1_000_000 {
            break;
        }
    }

    let text = String::from_utf8_lossy(&buf);
    let mut rx_total = 0u64;
    let mut tx_total = 0u64;
    for line in text.lines() {
        if let Some(value) = line.strip_prefix("rx_bytes=") {
            rx_total += value.trim().parse().unwrap_or(0);
        }
        if let Some(value) = line.strip_prefix("tx_bytes=") {
            tx_total += value.trim().parse().unwrap_or(0);
        }
    }
    Some((rx_total, tx_total))
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
    let json: serde_json::Value = response.json().await.map_err(|e| e.to_string())?;
    Ok(IpResult {
        ip: json.get("ip").and_then(|v| v.as_str()).unwrap_or("—").to_string(),
        country_code: json
            .get("country_code")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
    })
}

fn main() {
    tauri::Builder::default()
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
            switch_country,
            wireguard_installed,
            install_wireguard,
            connect,
            disconnect,
            tunnel_status,
            check_ip,
            clipboard_text,
        ])
        .run(tauri::generate_context!())
        .expect("ошибка запуска Ru VPN");
}
