const invoke = window.__TAURI__.core.invoke;

const el = {
  ip: document.getElementById("textIp"),
  ipRow: document.getElementById("ipRow"),
  changeServerHint: document.getElementById("textChangeServerHint"),
  powerOff: document.getElementById("imagePowerOff"),
  powerOn: document.getElementById("imagePowerOn"),
  toggle: document.getElementById("buttonToggle"),
  status: document.getElementById("textStatus"),
  connectingRing: document.getElementById("connectingRing"),
  received: document.getElementById("textReceived"),
  sent: document.getElementById("textSent"),
  keyButton: document.getElementById("buttonKey"),
  overlay: document.getElementById("overlay"),
  input: document.getElementById("inputKey"),
  pasteButton: document.getElementById("buttonPasteClipboard"),
  cancel: document.getElementById("textCancel"),
  done: document.getElementById("textDone"),
  overlayCountry: document.getElementById("overlayCountry"),
  countryRu: document.getElementById("buttonCountryRu"),
  countryUs: document.getElementById("buttonCountryUs"),
  countryCancel: document.getElementById("textCountryCancel"),
  mainServer: document.getElementById("buttonMainServer"),
  extraServers: document.getElementById("extraServers"),
  addServer: document.getElementById("buttonAddServer"),
  keyDialogTitle: document.getElementById("keyDialogTitle"),
  keyDialogHelp: document.getElementById("keyDialogHelp"),
  toast: document.getElementById("toast"),
  ipFlag: document.getElementById("ipFlag"),
};

// Пока идёт подключение/отключение — статус показывает «Наводим связь…»,
// как и на Android.
let busy = false;
// Тестовый ключ (test1590): только анимация кнопки, без настоящего тунеля.
let demoUp = false;
let toastTimer = null;
let connectionGeneration = 0;
let revokedKeyStreak = 0;
let watchingConnection = false;
let togglePending = false;
let statusRefresh = null;
// Окно ключа открыто для основного ключа ("main") или для нового сервера ("add").
let keyDialogMode = "main";

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

async function hasAnyKey() {
  const key = await invoke("load_key");
  return key != null && key !== "";
}

async function render(up) {
  const isDemo = await invoke("is_demo");
  const shown = isDemo ? demoUp : up;

  el.powerOn.classList.toggle("up", shown);
  el.toggle.setAttribute("aria-label", shown ? "Разъединить" : "Соединить");

  if (!busy) {
    el.status.textContent = shown ? "Впн включен" : "Впн выключен";
    el.status.classList.toggle("up", shown);
  }

  el.keyButton.textContent = (await hasAnyKey()) ? "Заменить ключ" : "Вставить ключ";
  // Окно «Сервер» доступно всегда: в нём же добавляются свои серверы.
  el.changeServerHint.hidden = false;

  if (!shown) {
    el.received.textContent = "0 КБ";
    el.sent.textContent = "0 КБ";
    el.received.classList.remove("up");
    el.sent.classList.remove("up");
  }
}

function setBusy(value) {
  if (value) {
    // Ответ старой проверки не должен отключить новое соединение/ключ.
    connectionGeneration++;
    revokedKeyStreak = 0;
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
      el.connectingRing.hidden = !status.transitioning;
      if (status.transitioning) {
        el.status.textContent = status.state === "starting" ? "Туннель запускается…" : "Туннель останавливается…";
        el.toggle.setAttribute("aria-label", "Остановить туннель");
      }
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

// IP-сервис показывает адрес, но ничего не знает о действительности ключа.
// Отключение разрешено только после двух явных ответов собственного сервера
// ключей. Таймаут, ошибка DNS, HTTP 404 старого сервера и HTTP 5xx — unknown.
async function watchConnection() {
  if (busy || watchingConnection) return;
  watchingConnection = true;
  const generation = connectionGeneration;
  try {
    if (await invoke("is_demo")) return;
    const status = await invoke("tunnel_status");
    if (!checkIsCurrent(generation)) return;
    if (!status.up) {
      revokedKeyStreak = 0;
      return;
    }
    const [, keyResult] = await Promise.allSettled([
      checkIp(generation),
      invoke("key_status"),
    ]);
    if (!checkIsCurrent(generation)) return;
    const state = keyResult.status === "fulfilled" ? keyResult.value : "unknown";
    if (state !== "revoked") {
      revokedKeyStreak = 0;
      return;
    }
    revokedKeyStreak++;
    if (revokedKeyStreak >= 2) await handleRevokedKey(generation);
  } catch {
    // Ошибка опроса/IPC не является отзывом ключа. Следующий тик повторит.
    if (checkIsCurrent(generation)) revokedKeyStreak = 0;
  } finally {
    watchingConnection = false;
  }
}

async function handleRevokedKey(generation) {
  if (!checkIsCurrent(generation)) return;
  setBusy(true);
  try {
    await invoke("disconnect");
    toast("Сервер подтвердил: доступ по ключу отключён. Проверьте подписку или получите ключ в боте.", true);
  } catch (err) {
    toast(`Ключ отключён на сервере. Не удалось остановить туннель: ${messageOf(err)}`, true);
  } finally {
    setBusy(false);
  }
  await refreshStatus();
  checkIp();
}

async function toggleDemo() {
  if (demoUp) {
    demoUp = false;
    await render(false);
    return;
  }
  setBusy(true);
  setTimeout(async () => {
    demoUp = true;
    setBusy(false);
    await render(false);
  }, 900);
}

async function connectReal() {
  setBusy(true);
  let missingKey = false;
  try {
    const key = await invoke("active_config");
    if (!key) {
      missingKey = true;
      toast("Сначала вставьте ключ — его выдаёт бот");
      return;
    }
    await invoke("connect", { configText: key });
  } catch (err) {
    missingKey = messageOf(err).startsWith("Неверная конфигурация VPN:");
    toast(`Не удалось соединиться: ${messageOf(err)}`, true);
  } finally {
    setBusy(false);
    await refreshStatus();
    if (missingKey) await openKeyDialog();
  }
  watchConnection();
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
  // Acquire before the first await so a double click cannot issue two installs.
  if (busy || togglePending) return;
  togglePending = true;
  el.toggle.disabled = true;
  try {
    if (await invoke("is_demo")) {
      await toggleDemo();
      return;
    }
    const status = await invoke("tunnel_status");
    if (status.up || status.transitioning) {
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

async function openKeyDialog(mode = "main") {
  if (busy) return;
  keyDialogMode = mode === "add" ? "add" : "main";
  el.keyDialogTitle.textContent = keyDialogMode === "add" ? "Новый сервер" : "Ключ доступа";
  el.keyDialogHelp.textContent = keyDialogMode === "add"
    ? "Вставьте ключ ещё одного сервера. Основной ключ останется в списке"
    : "Вставьте ключ, который прислал бот";
  el.input.value = "";
  try {
    const clip = await invoke("clipboard_text");
    el.input.value = clip && (await invoke("looks_like_key", { text: clip })) ? clip.trim() : "";
  } catch (_) {
    // A busy clipboard must not prevent replacing a damaged saved key.
  }
  el.overlay.hidden = false;
  el.input.focus();
}

function closeKeyDialog() {
  el.overlay.hidden = true;
}

async function saveKey() {
  if (busy || togglePending) return;
  const text = el.input.value;
  if (!text || !text.trim()) {
    closeKeyDialog();
    return;
  }
  setBusy(true);
  let reconnect = false;
  try {
    if (keyDialogMode === "add") {
      reconnect = (await invoke("tunnel_status")).up;
      const entry = await invoke("add_server", { text });
      toast(`Сервер добавлен и выбран: ${entry.name}`);
    } else if (await invoke("is_demo_key", { text })) {
      await invoke("save_demo");
      await invoke("select_server", { id: null });
      toast("Ключ сохранён");
    } else {
      const resolved = await invoke("resolve_key", { text });
      await invoke("save_key", { text: resolved.text, code: resolved.code });
      // Новый основной ключ — значит, подключаться им, а не своим сервером.
      await invoke("select_server", { id: null });
      toast("Ключ сохранён");
    }
    closeKeyDialog();
  } catch (err) {
    reconnect = false;
    toast(`Не получилось: ${messageOf(err)}. Скопируйте ключ целиком и проверьте интернет.`, true);
  } finally {
    setBusy(false);
  }
  if (reconnect) {
    await reconnectWithSavedKey();
  } else {
    await refreshStatus();
  }
}

// --- смена сервера (страны) ------------------------------------------------

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Окно «Сервер»: основной ключ (Россия/США от бота или вставленный конфиг)
// и свои дополнительные серверы, выбранный подсвечен.
async function renderServerList() {
  const list = await invoke("list_servers");
  const mainSelected = list.selected == null;
  el.countryRu.hidden = !list.main_switchable;
  el.countryUs.hidden = !list.main_switchable;
  el.mainServer.hidden = !list.has_main || list.main_switchable;
  el.mainServer.textContent = list.main_name || "Основной ключ";
  el.mainServer.classList.toggle("selected", mainSelected);
  await highlightCountry(mainSelected && list.main_switchable ? await invoke("current_country") : null);
  el.extraServers.innerHTML = list.servers.map((server) => {
    const id = escapeHtml(server.id);
    const selected = server.id === list.selected ? " selected" : "";
    return `<div class="server-row">
      <button class="flat-button country-button${selected}" data-server="${id}">${escapeHtml(server.name)}</button>
      <button class="server-remove" data-remove="${id}" aria-label="Удалить сервер" title="Удалить сервер">✕</button>
    </div>`;
  }).join("");
  return list;
}

async function openCountryDialog() {
  if (busy) return;
  try {
    await renderServerList();
  } catch (err) {
    toast(`Не удалось открыть список серверов: ${messageOf(err)}`, true);
    return;
  }
  el.overlayCountry.hidden = false;
}

function closeCountryDialog() {
  el.overlayCountry.hidden = true;
}

async function highlightCountry(selected) {
  el.countryRu.classList.toggle("selected", selected === "ru");
  el.countryUs.classList.toggle("selected", selected === "us");
}

async function chooseCountry(country) {
  if (busy || togglePending) return;
  setBusy(true);
  try {
    const list = await invoke("list_servers");
    if (list.selected == null && (await invoke("current_country")) === country) {
      closeCountryDialog();
      return;
    }
    const wasUp = (await invoke("tunnel_status")).up;
    const changed = await invoke("switch_country", { country });
    if (!changed) {
      toast("Смена сервера доступна только для ключа, который выдал бот");
      return;
    }
    await highlightCountry(country);
    toast("Сервер изменён");
    closeCountryDialog();
    if (wasUp) await reconnectWithSavedKey();
  } catch (err) {
    toast(`Не получилось сменить сервер: ${messageOf(err)}`, true);
  } finally {
    setBusy(false);
    await refreshStatus();
  }
}

// Свой сервер или основной ключ (id = null).
async function chooseServer(id) {
  if (busy || togglePending) return;
  setBusy(true);
  let reconnect = false;
  try {
    const list = await invoke("list_servers");
    if ((list.selected ?? null) === id) {
      closeCountryDialog();
      return;
    }
    reconnect = (await invoke("tunnel_status")).up;
    await invoke("select_server", { id });
    toast("Сервер изменён");
    closeCountryDialog();
  } catch (err) {
    reconnect = false;
    toast(`Не получилось сменить сервер: ${messageOf(err)}`, true);
  } finally {
    setBusy(false);
  }
  if (reconnect) {
    await reconnectWithSavedKey();
  } else {
    await refreshStatus();
  }
}

async function removeServer(id) {
  if (busy || togglePending) return;
  setBusy(true);
  let reconnect = false;
  try {
    const wasSelected = await invoke("remove_server", { id });
    reconnect = wasSelected && (await invoke("tunnel_status")).up;
    toast("Сервер удалён из списка");
    await renderServerList();
  } catch (err) {
    toast(`Не получилось удалить сервер: ${messageOf(err)}`, true);
  } finally {
    setBusy(false);
  }
  // Удалили сервер, через который были подключены, — переходим на основной ключ.
  if (reconnect) {
    await reconnectWithSavedKey();
  } else {
    await refreshStatus();
  }
}

async function reconnectWithSavedKey() {
  setBusy(true);
  try {
    // Do not start a new install if stopping the previous tunnel failed.
    await invoke("disconnect");
    const key = await invoke("active_config");
    if (key) await invoke("connect", { configText: key });
  } catch (err) {
    toast(`Не удалось переподключиться: ${messageOf(err)}`, true);
  } finally {
    setBusy(false);
    await refreshStatus();
  }
  watchConnection();
}

el.ipRow.addEventListener("click", openCountryDialog);
el.countryRu.addEventListener("click", () => chooseCountry("ru"));
el.countryUs.addEventListener("click", () => chooseCountry("us"));
el.countryCancel.addEventListener("click", closeCountryDialog);
el.mainServer.addEventListener("click", () => chooseServer(null));
el.addServer.addEventListener("click", () => {
  if (busy || togglePending) return;
  closeCountryDialog();
  openKeyDialog("add");
});
el.extraServers.addEventListener("click", (event) => {
  const target = event.target.closest("[data-server], [data-remove]");
  if (!target) return;
  if (target.dataset.remove) {
    removeServer(target.dataset.remove);
  } else {
    chooseServer(target.dataset.server);
  }
});

el.toggle.addEventListener("click", onToggleClicked);
el.keyButton.addEventListener("click", () => openKeyDialog("main"));
el.cancel.addEventListener("click", closeKeyDialog);
el.done.addEventListener("click", saveKey);
el.pasteButton.addEventListener("click", async () => {
  const clip = await invoke("clipboard_text");
  if (clip) el.input.value = clip.trim();
});

(async () => {
  await refreshStatus();
  checkIp();
  setInterval(refreshStatus, 2000);
  // IP и доступ по ключу проверяются независимо; частый опрос не нужен.
  setInterval(watchConnection, 30000);
})();

