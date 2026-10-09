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
  el.changeServerHint.hidden = !(await invoke("has_switchable_code"));

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
    const key = await invoke("load_key");
    if (!key) {
      missingKey = true;
      toast("Сначала вставьте ключ — его выдаёт бот");
      return;
    }
    const needsInstall = !(await invoke("wireguard_installed"));
    if (needsInstall) {
      el.status.textContent = "Ставим WireGuard…";
      toast("Настраиваем WireGuard — если появится окно установки, нажмите «Установить», это один раз");
    }
    await invoke("connect", { configText: key });
  } catch (err) {
    missingKey = messageOf(err).startsWith("Неверная конфигурация VPN:") || messageOf(err).includes("(1066/2)");
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

async function openKeyDialog() {
  if (busy) return;
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
  try {
    if (await invoke("is_demo_key", { text })) {
      await invoke("save_demo");
    } else {
      const resolved = await invoke("resolve_key", { text });
      await invoke("save_key", { text: resolved.text, code: resolved.code });
    }
    toast("Ключ сохранён");
    closeKeyDialog();
  } catch (err) {
    toast(`Не получилось: ${messageOf(err)}. Скопируйте ключ из бота целиком и проверьте интернет.`, true);
  } finally {
    setBusy(false);
  }
  await refreshStatus();
}

// --- смена сервера (страны) ------------------------------------------------

async function openCountryDialog() {
  if (busy) return;
  if (!(await invoke("has_switchable_code"))) {
    toast("Смена сервера доступна только для ключа, который выдал бот");
    return;
  }
  await highlightCountry(await invoke("current_country"));
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
    if ((await invoke("current_country")) === country) {
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

async function reconnectWithSavedKey() {
  setBusy(true);
  try {
    // Do not start a new install if stopping the previous tunnel failed.
    await invoke("disconnect");
    const key = await invoke("load_key");
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

el.toggle.addEventListener("click", onToggleClicked);
el.keyButton.addEventListener("click", openKeyDialog);
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

