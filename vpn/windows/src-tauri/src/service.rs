//! Numeric Service Control Manager state; never parse localized sc.exe output.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    Missing,
    Stopped,
    Starting,
    Stopping,
    Running,
    Deleting,
    Other(u32),
}

impl State {
    pub fn from_raw(value: u32) -> Self {
        match value {
            1 => Self::Stopped,
            2 => Self::Starting,
            3 => Self::Stopping,
            4 => Self::Running,
            value => Self::Other(value),
        }
    }

    pub fn transitioning(self) -> bool {
        matches!(self, Self::Starting | Self::Stopping | Self::Deleting)
    }

    pub fn label(self) -> &'static str {
        match self {
            Self::Missing => "missing",
            Self::Stopped => "stopped",
            Self::Starting => "starting",
            Self::Stopping => "stopping",
            Self::Running => "running",
            Self::Deleting => "deleting",
            Self::Other(_) => "other",
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub struct Snapshot {
    pub state: State,
    pub exit_code: u32,
    pub service_exit_code: u32,
}

impl Snapshot {
    pub fn failure_message(&self) -> String {
        if self.exit_code == 1066 {
            // WireGuard Windows services/errors.go: ErrorLoadConfiguration = 2.
            if self.service_exit_code == 2 {
                return "WireGuard не смог прочитать конфигурацию (1066/2). Заново вставьте полный ключ из бота.".to_string();
            }
            format!("Служба WireGuard остановилась: код Windows 1066, код WireGuard {}", self.service_exit_code)
        } else {
            format!("Служба WireGuard остановилась с кодом Windows {}", self.exit_code)
        }
    }
}

#[cfg(windows)]
pub fn query(name: &str) -> Result<Snapshot, String> {
    use windows::core::{HRESULT, PCWSTR};
    use windows::Win32::Foundation::{ERROR_SERVICE_DOES_NOT_EXIST, ERROR_SERVICE_MARKED_FOR_DELETE};
    use windows::Win32::System::Services::{
        CloseServiceHandle, OpenSCManagerW, OpenServiceW, QueryServiceStatus,
        SC_HANDLE, SC_MANAGER_CONNECT, SERVICE_QUERY_STATUS, SERVICE_STATUS,
    };

    struct Handle(SC_HANDLE);
    impl Drop for Handle {
        fn drop(&mut self) {
            unsafe { let _ = CloseServiceHandle(self.0); }
        }
    }

    unsafe {
        let manager = Handle(OpenSCManagerW(PCWSTR::null(), PCWSTR::null(), SC_MANAGER_CONNECT)
            .map_err(|e| format!("Не удалось открыть диспетчер служб Windows: {e}"))?);
        let name: Vec<u16> = name.encode_utf16().chain(Some(0)).collect();
        let service = match OpenServiceW(manager.0, PCWSTR(name.as_ptr()), SERVICE_QUERY_STATUS) {
            Ok(handle) => Handle(handle),
            Err(e) if e.code() == HRESULT::from_win32(ERROR_SERVICE_DOES_NOT_EXIST.0) => {
                return Ok(Snapshot { state: State::Missing, exit_code: 0, service_exit_code: 0 });
            }
            Err(e) if e.code() == HRESULT::from_win32(ERROR_SERVICE_MARKED_FOR_DELETE.0) => {
                return Ok(Snapshot { state: State::Deleting, exit_code: 0, service_exit_code: 0 });
            }
            Err(e) => return Err(format!("Не удалось проверить службу WireGuard: {e}")),
        };
        let mut status = SERVICE_STATUS::default();
        QueryServiceStatus(service.0, &mut status)
            .map_err(|e| format!("Не удалось прочитать состояние WireGuard: {e}"))?;
        Ok(Snapshot { state: State::from_raw(status.dwCurrentState.0), exit_code: status.dwWin32ExitCode,
            service_exit_code: status.dwServiceSpecificExitCode })
    }
}

#[cfg(not(windows))]
pub fn query(_name: &str) -> Result<Snapshot, String> {
    Err("Управление службой WireGuard доступно только в Windows".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn service_specific_failure_keeps_the_actual_cause() {
        let status = Snapshot { state: State::Stopped, exit_code: 1066, service_exit_code: 2 };
        assert!(status.failure_message().contains("конфигурацию (1066/2)"));
        let other = Snapshot { service_exit_code: 3, ..status };
        assert!(other.failure_message().contains("код WireGuard 3"));
        let win32 = Snapshot { exit_code: 5, ..status };
        assert!(!win32.failure_message().contains("1066/2"));
    }

    #[test]
    fn numeric_states_do_not_depend_on_windows_language() {
        assert_eq!(State::from_raw(4), State::Running);
        assert_eq!(State::from_raw(1), State::Stopped);
        assert_eq!(State::from_raw(2), State::Starting);
        assert_eq!(State::from_raw(3), State::Stopping);
        assert_eq!(State::from_raw(7), State::Other(7));
    }

    #[cfg(windows)]
    #[test]
    fn real_scm_missing_service_is_distinct_from_query_error() {
        let status = query("RuVpnRegressionTest-Nonexistent-87f941d2").unwrap();
        assert_eq!(status.state, State::Missing);
    }

    #[cfg(windows)]
    #[test]
    fn real_scm_detects_a_running_service() {
        // Read-only check against the standard Windows Event Log service.
        assert_eq!(query("EventLog").unwrap().state, State::Running);
    }
}
