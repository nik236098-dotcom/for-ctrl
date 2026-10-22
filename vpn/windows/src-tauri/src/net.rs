//! Адреса, маршруты и DNS на сетевом адаптере туннеля — то же, что делает
//! официальный WireGuard for Windows (tunnel/addressconfig.go): маршруты на
//! адаптер без шлюза, метрика интерфейса 0, чтобы туннель был главнее
//! обычной сети, DNS прямо на адаптере. Петли маршрутизации (пакеты к самому
//! серверу VPN внутри VPN) драйвер WireGuardNT исключает сам: свои UDP-пакеты
//! он всегда отправляет через лучший НЕ туннельный интерфейс.
use ipnet::IpNet;
use std::net::IpAddr;
use windows_sys::Win32::Foundation::{ERROR_NOT_FOUND, ERROR_OBJECT_ALREADY_EXISTS, NO_ERROR, WIN32_ERROR};
use windows_sys::Win32::NetworkManagement::IpHelper::{
    ConvertInterfaceLuidToGuid, CreateIpForwardEntry2, CreateUnicastIpAddressEntry, GetIpInterfaceEntry,
    InitializeIpForwardEntry, InitializeIpInterfaceEntry, InitializeUnicastIpAddressEntry, SetIpInterfaceEntry,
    DNS_INTERFACE_SETTINGS, DNS_INTERFACE_SETTINGS_VERSION1, DNS_SETTING_IPV6, DNS_SETTING_NAMESERVER,
    MIB_IPFORWARD_ROW2, MIB_IPINTERFACE_ROW, MIB_UNICASTIPADDRESS_ROW,
};
use windows_sys::Win32::NetworkManagement::Ndis::NET_LUID_LH;
use windows_sys::Win32::Networking::WinSock::{IpDadStatePreferred, ADDRESS_FAMILY, AF_INET, AF_INET6, SOCKADDR_INET};

const DEFAULT_MTU: u32 = 1420;

fn luid(value: u64) -> NET_LUID_LH {
    NET_LUID_LH { Value: value }
}

fn family(net: &IpNet) -> ADDRESS_FAMILY {
    if net.addr().is_ipv4() { AF_INET } else { AF_INET6 }
}

fn sockaddr(ip: IpAddr) -> SOCKADDR_INET {
    // Обнулённая структура + семейство + адрес; порт и прочее не нужны.
    let mut out: SOCKADDR_INET = unsafe { std::mem::zeroed() };
    match ip {
        IpAddr::V4(v4) => {
            out.Ipv4.sin_family = AF_INET;
            out.Ipv4.sin_addr.S_un.S_addr = u32::from_ne_bytes(v4.octets());
        }
        IpAddr::V6(v6) => {
            out.Ipv6.sin6_family = AF_INET6;
            out.Ipv6.sin6_addr.u.Byte = v6.octets();
        }
    }
    out
}

fn check(context: &str, code: WIN32_ERROR) -> Result<(), String> {
    if code == NO_ERROR || code == ERROR_OBJECT_ALREADY_EXISTS {
        Ok(())
    } else {
        Err(format!("{context}: {}", std::io::Error::from_raw_os_error(code as i32)))
    }
}

fn add_address(luid_value: u64, net: &IpNet) -> Result<(), String> {
    unsafe {
        let mut row: MIB_UNICASTIPADDRESS_ROW = std::mem::zeroed();
        InitializeUnicastIpAddressEntry(&mut row);
        row.InterfaceLuid = luid(luid_value);
        row.Address = sockaddr(net.addr());
        row.OnLinkPrefixLength = net.prefix_len();
        row.DadState = IpDadStatePreferred;
        check("адрес туннеля", CreateUnicastIpAddressEntry(&row))
    }
}

fn add_route(luid_value: u64, net: &IpNet) -> Result<(), String> {
    unsafe {
        let mut row: MIB_IPFORWARD_ROW2 = std::mem::zeroed();
        InitializeIpForwardEntry(&mut row);
        row.InterfaceLuid = luid(luid_value);
        row.DestinationPrefix.Prefix = sockaddr(net.network());
        row.DestinationPrefix.PrefixLength = net.prefix_len();
        // Шлюз 0.0.0.0 / :: — «прямо в этот адаптер», как у официального клиента.
        row.NextHop.si_family = family(net);
        row.Metric = 0;
        check("маршрут туннеля", CreateIpForwardEntry2(&row))
    }
}

/// Метрика 0 и MTU на интерфейсе одного семейства адресов. ERROR_NOT_FOUND —
/// это семейство (обычно IPv6) на адаптере выключено.
fn tune_interface(luid_value: u64, family: ADDRESS_FAMILY, mtu: u32) -> Result<bool, String> {
    unsafe {
        let mut row: MIB_IPINTERFACE_ROW = std::mem::zeroed();
        InitializeIpInterfaceEntry(&mut row);
        row.InterfaceLuid = luid(luid_value);
        row.Family = family;
        let code = GetIpInterfaceEntry(&mut row);
        if code == ERROR_NOT_FOUND {
            return Ok(false);
        }
        check("параметры адаптера", code)?;
        row.UseAutomaticMetric = 0;
        row.Metric = 0;
        row.NlMtu = if family == AF_INET6 { mtu.max(1280) } else { mtu };
        row.SitePrefixLength = 0;
        check("параметры адаптера", SetIpInterfaceEntry(&mut row))?;
        Ok(true)
    }
}

type SetDnsFn = unsafe extern "system" fn(windows_sys::core::GUID, *const DNS_INTERFACE_SETTINGS) -> WIN32_ERROR;

/// DNS на адаптере туннеля. SetInterfaceDnsSettings есть только в Windows 10
/// 2004 и новее — ищем её в iphlpapi.dll при вызове, а не статическим
/// импортом, иначе на старой Windows приложение не запустилось бы вовсе.
/// Без неё — netsh, так же умеющий записать DNS на конкретный адаптер.
fn set_dns(luid_value: u64, alias: &str, servers: &[IpAddr]) -> Result<(), String> {
    if servers.is_empty() {
        return Ok(());
    }
    let mut guid: windows_sys::core::GUID = unsafe { std::mem::zeroed() };
    let found = unsafe {
        ConvertInterfaceLuidToGuid(&luid(luid_value), &mut guid) == NO_ERROR
    };
    let api = unsafe { libloading::Library::new("iphlpapi.dll") }.ok();
    let set_dns = api.as_ref().and_then(|lib| unsafe { lib.get::<SetDnsFn>(b"SetInterfaceDnsSettings\0").ok() });
    if let (true, Some(set_dns)) = (found, set_dns) {
        for (flag, list) in [
            (0, servers.iter().filter(|ip| ip.is_ipv4()).collect::<Vec<_>>()),
            (DNS_SETTING_IPV6, servers.iter().filter(|ip| ip.is_ipv6()).collect::<Vec<_>>()),
        ] {
            if list.is_empty() {
                continue;
            }
            let joined = list.iter().map(|ip| ip.to_string()).collect::<Vec<_>>().join(",");
            let mut wide: Vec<u16> = joined.encode_utf16().chain(Some(0)).collect();
            let mut settings: DNS_INTERFACE_SETTINGS = unsafe { std::mem::zeroed() };
            settings.Version = DNS_INTERFACE_SETTINGS_VERSION1;
            settings.Flags = (DNS_SETTING_NAMESERVER | flag) as u64;
            settings.NameServer = wide.as_mut_ptr();
            check("DNS туннеля", unsafe { set_dns(guid, &settings) })?;
        }
        return Ok(());
    }
    for (index, ip) in servers.iter().enumerate() {
        let family = if ip.is_ipv4() { "ipv4" } else { "ipv6" };
        let mut command = crate::new_command("netsh");
        command.args(["interface", family]);
        if index == 0 || !servers[..index].iter().any(|seen| seen.is_ipv4() == ip.is_ipv4()) {
            command.args(["set", "dnsservers", &format!("name={alias}"), "source=static",
                &format!("address={ip}"), "register=none", "validate=no"]);
        } else {
            command.args(["add", "dnsservers", &format!("name={alias}"), &format!("address={ip}"), "validate=no"]);
        }
        let status = command.status().map_err(|e| format!("DNS туннеля: {e}"))?;
        if !status.success() {
            return Err("DNS туннеля: netsh не смог записать адрес".to_string());
        }
    }
    Ok(())
}

/// Всё сразу. Ошибка по IPv4 — настоящая ошибка подключения. Если IPv6 на
/// адаптере выключен, маршруты IPv6 пропускаем: без адреса IPv6 этот трафик
/// всё равно не уйдёт в туннель, а подключение по IPv4 работает.
pub fn configure(luid_value: u64, alias: &str, addresses: &[IpNet], routes: &[IpNet], mtu: Option<u32>, dns: &[IpAddr]) -> Result<(), String> {
    let mtu = mtu.unwrap_or(DEFAULT_MTU);
    let v4 = tune_interface(luid_value, AF_INET, mtu)?;
    let v6 = tune_interface(luid_value, AF_INET6, mtu)?;
    for net in addresses {
        let enabled = if net.addr().is_ipv4() { v4 } else { v6 };
        if enabled {
            add_address(luid_value, net)?;
        }
    }
    for net in routes {
        if net.addr().is_ipv4() {
            if !v4 {
                return Err("на адаптере туннеля выключен IPv4".to_string());
            }
            add_route(luid_value, net)?;
        } else if v6 {
            // IPv6-маршрут может не встать, если IPv6 выключен в системе
            // частично; это не мешает IPv4-туннелю.
            add_route(luid_value, net).ok();
        }
    }
    // DNS не критичен для самого туннеля: при ошибке остаётся системный DNS.
    set_dns(luid_value, alias, dns).ok();
    Ok(())
}
