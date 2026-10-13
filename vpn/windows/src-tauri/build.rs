fn main() {
    #[cfg(target_os = "windows")]
    {
        let windows = tauri_build::WindowsAttributes::new()
            .app_manifest(include_str!("windows/app.manifest"));
        tauri_build::try_build(tauri_build::Attributes::new().windows_attributes(windows))
            .expect("не удалось встроить манифест Windows");
    }
    #[cfg(not(target_os = "windows"))]
    {
        tauri_build::build();
    }
}
