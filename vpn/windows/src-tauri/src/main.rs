// Ru VPN для Windows — тот же дизайн, что в Android-приложении, но
// туннель поднимает не своя реализация WireGuard, а официальный
// wireguard.exe (WireGuard for Windows), которым это приложение просто
// управляет из-под капота. Причина — см. vpn/windows/README.md: подписанный
// ядерный драйвер (Wintun) с нуля своими силами не собрать и не подписать,
// а официальный уже есть и работает.
#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

use serde::Serialize;
use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::time::Duration;
use tauri::Manager;

/// Обычный `Command::new` из консольного приложения на Windows на долю
/// секунды показывает мелькающее окно консоли — заметно, когда его дёргают
/// раз в 2 секунды (опрос статуса тунеля). CREATE_NO_WINDOW убирает это
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

const WIREGUARD_INDEX_URL: &str = "https://download.wireguard.com/windows-client/";

/// Имена вида `wireguard-amd64-0.5.3.msi` — любые из встречающихся в HTML
/// как отдельные строки в кавычках (атрибут ссылки), без предположений о
/// точной разметке страницы.
fn extract_msi_filenames(html: &str) -> Vec<&str> {
    html.split(['"', '\''])
        .filter(|token| token.starts_with("wireguard-amd64-") && token.ends_with(".msi"))
        .collect()
}

fn parse_msi_version(name: &str) -> Option<(u32, u32, u32)> {
    let stem = name.strip_prefix("wireguard-amd64-")?.strip_suffix(".msi")?;
    let mut parts = stem.split('.');
    let major = parts.next()?.parse().ok()?;
    let minor = parts.next()?.parse().ok()?;
    let patch = parts.next().unwrap_or("0").parse().unwrap_or(0);
    Some((major, minor, patch))
}

/// Прямая ссылка на MSI-пакет последней версии (не на bootstrapper .exe) —
/// нужна, чтобы поставить его по-настоящему без единого окна через
/// `msiexec /quiet`, в отличие от .exe-обёртки (см. [install_wireguard]).
/// ЛУЧШАЯ ПОПЫТКА, не проверено вживую: если формат страницы окажется
/// другим и разбор не найдёт ни одного файла — [install_wireguard] сам
/// откатится на прежний (видимый) способ через .exe.
async fn find_latest_msi_url() -> Option<String> {
    let html = reqwest::get(WIREGUARD_INDEX_URL).await.ok()?.text().await.ok()?;
    extract_msi_filenames(&html)
        .into_iter()
        .filter_map(|name| parse_msi_version(name).map(|version| (version, name)))
        .max_by_key(|(version, _)| *version)
        .map(|(_, name)| format!("{WIREGUARD_INDEX_URL}{name}"))
}

/// Тихая установка через прямой MSI-пакет — по-настоящему без единого
/// окна (`msiexec /quiet` — не предположение, а стандартное, всегда
/// безголовое поведение Windows Installer).
async fn install_via_msi(url: &str) -> Result<(), String> {
    let bytes = reqwest::get(url)
        .await
        .map_err(|e| e.to_string())?
        .bytes()
        .await
        .map_err(|e| e.to_string())?;
    let path = std::env::temp_dir().join("ruvpn-wireguard-latest.msi");
    fs::write(&path, &bytes).map_err(|e| e.to_string())?;
    let status = new_command("msiexec")
        .arg("/i")
        .arg(&path)
        .arg("/quiet")
        .arg("/qn")
        .arg("/norestart")
        .status()
        .map_err(|e| e.to_string());
    fs::remove_file(&path).ok();
    if !status?.success() {
        return Err("msiexec не смог поставить пакет".to_string());
    }
    Ok(())
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

/// Установка официального клиента: приложение уже само запущено от
/// администратора (windows/app.manifest), поэтому дочерний установщик
/// наследует те же права и не спрашивает UAC повторно.
///
/// Сначала пробуем по-настоящему тихий путь — сам MSI-пакет через
/// `msiexec /quiet` (см. [install_via_msi]), без единого окна. Если он по
/// любой причине не сработал (страница со ссылками на MSI изменила
/// формат, сеть подвела и т.п.) — откатываемся на официальный .exe-
/// установщик; на практике (по отзыву с реального устройства) его окно
/// само не закрывается, там нужно нажать «Установить» руками — фронтенд
/// предупреждает об этом тостом на случай, если дойдёт до этого шага.
#[tauri::command]
async fn install_wireguard() -> Result<(), String> {
    if wireguard_installed() {
        return Ok(());
    }

    if let Some(msi_url) = find_latest_msi_url().await {
        if install_via_msi(&msi_url).await.is_ok() && wireguard_installed() {
            return Ok(());
        }
    }

    let installer = download_installer().await?;
    let status = new_command(&installer)
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

/// Официальный WireGuardManager сам открывает своё окно менеджера для
/// вошедшего пользователя, когда служба менеджера впервые стартует после
/// установки (см. его собственный журнал: "Starting UI process for
/// user…") — это его поведение, не наш вызов, поэтому подавить это можно
/// только закрыв уже появившееся окно самим. ЛУЧШАЯ ПОПЫТКА, не проверено
/// вживую: несколько попыток с паузой, пока окно не появится (или не
/// истечёт время) — если не найдём, просто ничего не делаем, окно
/// останется открытым, как сейчас.
#[cfg(windows)]
async fn close_wireguard_manager_window() {
    // HWND (сырой указатель внутри) не Send — держать его через .await
    // нельзя (именно на этом упала первая версия: "future returned by
    // `connect` is not Send"). Весь поиск и ожидание — в отдельном потоке
    // синхронно (std::thread::sleep, не tokio::time::sleep), наружу
    // уходит только пустой результат.
    let _ = tokio::task::spawn_blocking(|| {
        use windows::core::PCWSTR;
        use windows::Win32::Foundation::{LPARAM, WPARAM};
        use windows::Win32::UI::WindowsAndMessaging::{FindWindowW, PostMessageW, WM_CLOSE};

        let title: Vec<u16> = "WireGuard\0".encode_utf16().collect();
        for _ in 0..10 {
            if let Ok(hwnd) = unsafe { FindWindowW(PCWSTR::null(), PCWSTR(title.as_ptr())) } {
                if !hwnd.is_invalid() {
                    unsafe {
                        let _ = PostMessageW(hwnd, WM_CLOSE, WPARAM(0), LPARAM(0));
                    }
                    return;
                }
            }
            std::thread::sleep(Duration::from_millis(500));
        }
    })
    .await;
}

#[tauri::command]
async fn connect(config_text: String) -> Result<(), String> {
    // Если тунель уже поднят (например, наше приложение на секунду не
    // успело обновить свой статус и второй клик пришёл раньше) —
    // wireguard.exe откажет с "Tunnel already installed and running".
    // Это не настоящая ошибка, тунель и так уже работает.
    if service_running(&format!("WireGuardTunnel${TUNNEL_NAME}")) {
        return Ok(());
    }

    let freshly_installed = !wireguard_installed();
    if freshly_installed {
        install_wireguard().await?;
        #[cfg(windows)]
        close_wireguard_manager_window().await;
        // Сразу после установки службе/драйверу WireGuard иногда нужно
        // немного времени, чтобы "осесть" — первая попытка поднять тунель
        // в ту же секунду не всегда проходит (не проверено на реальной
        // Windows — это лучшая попытка объяснить жалобу "после установки
        // ничего не работает", а не подтверждённая причина).
        tokio::time::sleep(Duration::from_secs(2)).await;
    }

    let conf_path = tunnel_conf_path();
    fs::create_dir_all(conf_path.parent().unwrap()).map_err(|e| e.to_string())?;
    fs::write(&conf_path, &config_text).map_err(|e| e.to_string())?;

    let mut result = install_tunnel_service(&conf_path).await;
    if result.is_err() && freshly_installed {
        tokio::time::sleep(Duration::from_secs(3)).await;
        result = install_tunnel_service(&conf_path).await;
    }

    // НЕ удаляем файл здесь: /installtunnelservice возвращает успех уже
    // после регистрации службы в SCM, а сама служба открывает файл конфига
    // чуть позже, асинхронно — реальная причина бага "wireguard.exe
    // отчитался об успехе, а тунель не поднялся" (см. журнал WireGuard:
    // "Unable to load configuration from path: ...The system cannot find
    // the file specified" — служба стартовала уже после того, как этот же
    // код успевал удалить файл). Файл остаётся лежать (перезаписывается на
    // каждый connect) и удаляется только в disconnect(), когда служба уже
    // снята и файл ей больше не понадобится.
    if result.is_ok() {
        // По той же причине, что и с файлом конфига: /installtunnelservice
        // возвращает успех сразу после регистрации службы в SCM, а реально
        // она встаёт в RUNNING чуть позже. Раньше мы отдавали "успех" в
        // интерфейс немедленно — кнопка разблокировалась и статус на долю
        // секунды показывал "Впн выключен" (настоящий статус ещё не
        // подтянулся), из-за чего хотелось нажать ещё раз. Теперь ждём
        // здесь, пока служба не станет реально RUNNING (до 10 секунд), и
        // только тогда отдаём успех — всё это время кнопка на фронтенде
        // остаётся заблокированной с надписью "Наводим связь…".
        let service_name = format!("WireGuardTunnel${TUNNEL_NAME}");
        if !wait_for_service_running(&service_name, Duration::from_secs(10)).await {
            result = Err("Служба тунеля зарегистрирована, но не запустилась вовремя".to_string());
        }
    }
    result
}

/// Ждёт, пока служба тунеля реально не перейдёт в состояние RUNNING (или не
/// истечёт таймаут). Используется только для того, чтобы не отдавать
/// "успех" наружу раньше, чем тунель по-настоящему поднялся.
async fn wait_for_service_running(service_name: &str, timeout: Duration) -> bool {
    let deadline = std::time::Instant::now() + timeout;
    loop {
        if service_running(service_name) {
            return true;
        }
        if std::time::Instant::now() >= deadline {
            return false;
        }
        tokio::time::sleep(Duration::from_millis(400)).await;
    }
}

fn tunnel_conf_path() -> PathBuf {
    std::env::temp_dir()
        .join("ruvpn-windows")
        .join(format!("{TUNNEL_NAME}.conf"))
}

async fn install_tunnel_service(conf_path: &std::path::Path) -> Result<(), String> {
    let output = new_command(wireguard_exe())
        .arg("/installtunnelservice")
        .arg(conf_path)
        .output()
        .map_err(|e| e.to_string())?;
    if !output.status.success() {
        return Err(command_error(&output.stderr, output.status.code()));
    }
    Ok(())
}

#[tauri::command]
async fn disconnect() -> Result<(), String> {
    let output = new_command(wireguard_exe())
        .arg("/uninstalltunnelservice")
        .arg(TUNNEL_NAME)
        .output()
        .map_err(|e| e.to_string())?;
    if !output.status.success() {
        return Err(command_error(&output.stderr, output.status.code()));
    }
    // Служба снята — файл конфига (см. connect()) ей больше не нужен.
    fs::remove_file(tunnel_conf_path()).ok();
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
    match new_command("sc").arg("query").arg(service_name).output() {
        Ok(output) => String::from_utf8_lossy(&output.stdout).contains("RUNNING"),
        Err(_) => false,
    }
}

#[tauri::command]
async fn tunnel_status() -> TunnelStatus {
    let up = service_running(&format!("WireGuardTunnel${TUNNEL_NAME}"));
    let (rx, tx) = if up {
        read_interface_bytes(TUNNEL_NAME).unwrap_or((0, 0))
    } else {
        (0, 0)
    };
    TunnelStatus { up, rx, tx }
}

/// Счётчики трафика — тем же способом, что и любой другой сетевой адаптер
/// в диспетчере задач Windows: тунель WireGuard виден системе как обычный
/// сетевой интерфейс (см. вывод `ipconfig` — "Неизвестный адаптер ruvpn",
/// описание "WireGuard Tunnel"), у него есть стандартные счётчики байт
/// через IP Helper API. Это не самопальный протокол — задокументированный
/// Win32 API, тот же MIB_IF_ROW2, что использует сама Windows.
#[cfg(windows)]
fn read_interface_bytes(alias: &str) -> Option<(u64, u64)> {
    use windows::Win32::NetworkManagement::IpHelper::{FreeMibTable, GetIfTable2, MIB_IF_TABLE2};

    unsafe {
        let mut table_ptr: *mut MIB_IF_TABLE2 = std::ptr::null_mut();
        if GetIfTable2(&mut table_ptr).is_err() || table_ptr.is_null() {
            return None;
        }
        let table = &*table_ptr;
        let count = table.NumEntries as usize;
        let rows = std::slice::from_raw_parts(table.Table.as_ptr(), count);

        let result = rows.iter().find_map(|row| {
            let row_alias = String::from_utf16_lossy(&row.Alias);
            let row_alias = row_alias.trim_end_matches('\u{0}');
            if row_alias.eq_ignore_ascii_case(alias) {
                Some((row.InOctets, row.OutOctets))
            } else {
                None
            }
        });

        FreeMibTable(table_ptr as *const core::ffi::c_void);
        result
    }
}

#[cfg(not(windows))]
fn read_interface_bytes(_alias: &str) -> Option<(u64, u64)> {
    None
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
