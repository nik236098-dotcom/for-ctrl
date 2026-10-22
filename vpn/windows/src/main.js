const invoke = window.__TAURI__.core.invoke;

// Ключей нет: сервер выбирается из списка, при первом подключении
// компьютер сам регистрируется на нём (см. src-tauri/src/provision.rs).
const el = {
  ip: document.getElementById("textIp"),
  ipRow: document.getElementById("ipRow"),
  powerOff: document.getElementById("imagePowerOff"),
  powerOn: document.getElementById("imagePowerOn"),
  toggle: document.getElementById("buttonToggle"),
  status: document.getElementById("textStatus"),
  connectingRing: document.getElementById("connectingRing"),
  received: document.getElementById("textReceived"),
  sent: document.getElementById("textSent"),
  serverButton: document.getElementById("buttonServer"),
  overlayCountry: document.getElementById("overlayCountry"),
  serverList: document.getElementById("serverList"),
  countryCancel: document.getElementById("textCountryCancel"),
  toast: document.getElementById("toast"),
  ipFlag: document.getElementById("ipFlag"),
};

// Пока идёт подключение/отключение — статус показывает «Наводим связь…».
let busy = false;
let toastTimer = null;
let connectionGeneration = 0;
let togglePending = false;
let statusRefresh = null;

function checkIsCurrent(generation) {
  return !busy && generation === connectionGeneration;
}

function toast(text, persistent = false) {
  // Ошибки держим намного дольше (и по клику можно закрыть раньше) —
  // 4 секунды слишком мало, чтобы успеть прочитать текст ошибки, не то что
  // сфотографировать её для отчёта.
  el.toast.textContent = text;
  el.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.toast.hidden = true;
  }, persistent ? 30000 : 4000);
}

el.toast.addEventListener("click", () => {
  el.toast.hidden = true;
  clearTimeout(toastTimer);
});

function messageOf(err) {
  if (typeof err === "string") return err;
  if (err && err.message) return err.message;
  return String(err);
}

function formatBytes(bytes) {
  const units = ["Б", "КБ", "МБ", "ГБ"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit++;
  }
  return `${value.toFixed(1)} ${units[unit]}`;
}

// Тот же приём, что и в диалоге выбора сервера: эмодзи-флаг — это пара
// «национальных букв» Юникода, которая рисуется как флаг только на свежих
// версиях Windows со свежим шрифтом, иначе система молча показывает
// запасной вариант — просто буквы кода страны. SVG не зависит от шрифта.
function flagSvg(countryCode) {
  const code = (countryCode || "").toUpperCase();
  if (code === "RU") {
    return `<svg class="flag-icon" viewBox="0 0 24 16" aria-hidden="true">
      <rect width="24" height="16" fill="#fff" />
      <rect y="5.33" width="24" height="5.34" fill="#0039a6" />
      <rect y="10.67" width="24" height="5.33" fill="#d52b1e" />
    </svg>`;
  }
  if (code === "US") {
    return `<svg class="flag-icon" viewBox="0 0 24 16" aria-hidden="true">
      <rect width="24" height="16" fill="#b22234" />
      <rect y="1.23" width="24" height="1.23" fill="#fff" />
      <rect y="3.69" width="24" height="1.23" fill="#fff" />
      <rect y="6.15" width="24" height="1.23" fill="#fff" />
      <rect y="8.61" width="24" height="1.23" fill="#fff" />
      <rect y="11.08" width="24" height="1.23" fill="#fff" />
      <rect y="13.54" width="24" height="1.23" fill="#fff" />
      <rect width="9.6" height="8.62" fill="#3c3b6e" />
    </svg>`;
  }
  return "";
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function render(up) {
  el.powerOn.classList.toggle("up", up);
  el.toggle.setAttribute("aria-label", up ? "Разъединить" : "Соединить");
  if (!busy) {
    el.status.textContent = up ? "Впн включен" : "Впн выключен";
    el.status.classList.toggle("up", up);
  }
  if (!up) {
    el.received.textContent = "0 КБ";
    el.sent.textContent = "0 КБ";
    el.received.classList.remove("up");
    el.sent.classList.remove("up");
  }
}

async function renderServerButton() {
  try {
    const list = await invoke("list_servers");
    const current = list.servers.find((s) => s.id === list.selected);
    el.serverButton.textContent = current ? `Сервер: ${current.name}` : "Сервер";
  } catch {
    el.serverButton.textContent = "Сервер";
  }
}

function setBusy(value) {
  if (value) {
    // Ответ старой проверки не должен повлиять на новое соединение.
    connectionGeneration++;
  }
  busy = value;
  el.toggle.disabled = value || togglePending;
  el.connectingRing.hidden = !value;
  el.powerOff.classList.toggle("connecting", value);
  if (value) {
    el.status.textContent = "Наводим связь…";
    el.status.classList.remove("up");
  }
}

async function refreshStatus() {
  // Only one poll in flight. A failed query is unknown, never "VPN off".
  if (statusRefresh) return statusRefresh;
  const generation = connectionGeneration;
  statusRefresh = (async () => {
    try {
      const status = await invoke("tunnel_status");
      if (generation !== connectionGeneration || busy) return status.up;
      await render(status.up);
      if (generation !== connectionGeneration || busy) return status.up;
      el.connectingRing.hidden = true;
      if (status.up) {
        el.received.textContent = formatBytes(status.rx);
        el.sent.textContent = formatBytes(status.tx);
        el.received.classList.add("up");
        el.sent.classList.add("up");
        // Адаптер включён, но сервер ещё ни разу не ответил — трафика нет.
        if (status.handshake === false) el.status.textContent = "Ждём ответа сервера…";
      }
      return status.up;
    } catch {
      if (generation === connectionGeneration && !busy) {
        el.status.textContent = "Не удалось проверить состояние VPN";
        el.status.classList.remove("up");
        el.connectingRing.hidden = true;
      }
      return null;
    } finally {
      statusRefresh = null;
    }
  })();
  return statusRefresh;
}

// Только показывает IP. Неудачная проверка IP никогда не отключает VPN.
async function checkIp(generation = connectionGeneration) {
  try {
    const result = await invoke("check_ip");
    if (!checkIsCurrent(generation)) return false;
    el.ip.textContent = `Ваш ip: ${result.ip}`;
    el.ipFlag.innerHTML = flagSvg(result.country_code);
    return true;
  } catch {
    if (!checkIsCurrent(generation)) return false;
    el.ip.textContent = "Ваш ip: недоступно";
    el.ipFlag.innerHTML = "";
    return false;
  }
}

async function connectReal() {
  setBusy(true);
  try {
    await invoke("connect");
  } catch (err) {
    toast(`Не удалось соединиться: ${messageOf(err)}`, true);
  } finally {
    setBusy(false);
    await refreshStatus();
  }
  checkIp();
}

async function disconnectReal() {
  setBusy(true);
  try {
    await invoke("disconnect");
  } catch (err) {
    toast(`Не удалось разъединиться: ${messageOf(err)}`, true);
  } finally {
    setBusy(false);
    await refreshStatus();
  }
  checkIp();
}

async function onToggleClicked() {
  // Acquire before the first await so a double click cannot connect twice.
  if (busy || togglePending) return;
  togglePending = true;
  el.toggle.disabled = true;
  try {
    const status = await invoke("tunnel_status");
    if (status.up) {
      await disconnectReal();
    } else {
      await connectReal();
    }
  } catch (err) {
    toast(`Не удалось проверить VPN: ${messageOf(err)}`, true);
    await refreshStatus();
  } finally {
    togglePending = false;
    el.toggle.disabled = busy;
  }
}

// --- выбор сервера ---------------------------------------------------------

async function openServerDialog() {
  if (busy) return;
  try {
    const list = await invoke("list_servers");
    el.serverList.innerHTML = list.servers.map((server) => {
      const selected = server.id === list.selected ? " selected" : "";
      return `<button class="flat-button country-button${selected}" data-server="${escapeHtml(server.id)}">
        ${flagSvg(server.country)} ${escapeHtml(server.name)}
      </button>`;
    }).join("");
  } catch (err) {
    toast(`Не удалось открыть список серверов: ${messageOf(err)}`, true);
    return;
  }
  el.overlayCountry.hidden = false;
}

function closeServerDialog() {
  el.overlayCountry.hidden = true;
}

async function chooseServer(id) {
  if (busy || togglePending) return;
  setBusy(true);
  let reconnect = false;
  try {
    const list = await invoke("list_servers");
    if (list.selected === id) {
      closeServerDialog();
      return;
    }
    reconnect = (await invoke("tunnel_status")).up;
    await invoke("select_server", { id });
    closeServerDialog();
    toast("Сервер изменён");
  } catch (err) {
    reconnect = false;
    toast(`Не получилось сменить сервер: ${messageOf(err)}`, true);
  } finally {
    setBusy(false);
  }
  await renderServerButton();
  if (reconnect) {
    await reconnectToSelected();
  } else {
    await refreshStatus();
  }
}

async function reconnectToSelected() {
  setBusy(true);
  try {
    // Не подключаемся заново, если старый туннель не остановился.
    await invoke("disconnect");
    await invoke("connect");
  } catch (err) {
    toast(`Не удалось переподключиться: ${messageOf(err)}`, true);
  } finally {
    setBusy(false);
    await refreshStatus();
  }
  checkIp();
}

el.ipRow.addEventListener("click", openServerDialog);
el.serverButton.addEventListener("click", openServerDialog);
el.countryCancel.addEventListener("click", closeServerDialog);
el.serverList.addEventListener("click", (event) => {
  const target = event.target.closest("[data-server]");
  if (target) chooseServer(target.dataset.server);
});
el.toggle.addEventListener("click", onToggleClicked);

(async () => {
  await renderServerButton();
  await refreshStatus();
  checkIp();
  setInterval(refreshStatus, 2000);
  // IP обновляется раз в 30 секунд; на подключение это не влияет.
  setInterval(checkIp, 30000);
})();
