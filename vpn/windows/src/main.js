const invoke = window.__TAURI__.core.invoke;

const el = {
  ip: document.getElementById("textIp"),
  powerOn: document.getElementById("imagePowerOn"),
  toggle: document.getElementById("buttonToggle"),
  status: document.getElementById("textStatus"),
  progress: document.getElementById("progress"),
  received: document.getElementById("textReceived"),
  sent: document.getElementById("textSent"),
  keyButton: document.getElementById("buttonKey"),
  overlay: document.getElementById("overlay"),
  input: document.getElementById("inputKey"),
  pasteButton: document.getElementById("buttonPasteClipboard"),
  cancel: document.getElementById("textCancel"),
  done: document.getElementById("textDone"),
  toast: document.getElementById("toast"),
};

// Пока идёт подключение/отключение — статус показывает «Наводим связь…»,
// как и на Android.
let busy = false;
// Тестовый ключ (test1590): только анимация кнопки, без настоящего тунеля.
let demoUp = false;
let toastTimer = null;

function toast(text) {
  el.toast.textContent = text;
  el.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.toast.hidden = true;
  }, 4000);
}

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

function flagEmoji(countryCode) {
  if (!countryCode || countryCode.length !== 2) return "";
  const base = 0x1f1e6 - "A".charCodeAt(0);
  return countryCode
    .toUpperCase()
    .split("")
    .map((c) => String.fromCodePoint(base + c.charCodeAt(0)))
    .join("");
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

  if (!shown) {
    el.received.textContent = "0 КБ";
    el.sent.textContent = "0 КБ";
    el.received.classList.remove("up");
    el.sent.classList.remove("up");
  }
}

function setBusy(value) {
  busy = value;
  el.toggle.disabled = value;
  el.progress.hidden = !value;
  if (value) {
    el.status.textContent = "Наводим связь…";
    el.status.classList.remove("up");
  }
}

async function refreshStatus() {
  const status = await invoke("tunnel_status");
  if (!busy) await render(status.up);
  if (status.up) {
    el.received.textContent = formatBytes(status.rx);
    el.sent.textContent = formatBytes(status.tx);
    el.received.classList.add("up");
    el.sent.classList.add("up");
  }
  return status.up;
}

async function checkIp() {
  try {
    const result = await invoke("check_ip");
    const flag = flagEmoji(result.country_code);
    el.ip.textContent = `Ваш ip: ${result.ip} ${flag}`.trim();
  } catch {
    el.ip.textContent = "Ваш ip: недоступно";
  }
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
  const key = await invoke("load_key");
  if (!key) {
    toast("Сначала вставьте ключ — его выдаёт бот");
    openKeyDialog();
    return;
  }
  setBusy(true);
  try {
    await invoke("connect", { configText: key });
  } catch (err) {
    toast(`Не удалось соединиться: ${messageOf(err)}`);
  }
  setBusy(false);
  const up = await refreshStatus();
  await render(up);
  checkIp();
}

async function disconnectReal() {
  setBusy(true);
  try {
    await invoke("disconnect");
  } catch (err) {
    toast(`Не удалось разъединиться: ${messageOf(err)}`);
  }
  setBusy(false);
  const up = await refreshStatus();
  await render(up);
  checkIp();
}

async function onToggleClicked() {
  if (busy) return;
  if (await invoke("is_demo")) {
    await toggleDemo();
    return;
  }
  const status = await invoke("tunnel_status");
  if (status.up) {
    await disconnectReal();
  } else {
    await connectReal();
  }
}

async function openKeyDialog() {
  const clip = await invoke("clipboard_text");
  el.input.value = clip && (await invoke("looks_like_key", { text: clip })) ? clip.trim() : "";
  el.overlay.hidden = false;
  el.input.focus();
}

function closeKeyDialog() {
  el.overlay.hidden = true;
}

async function saveKey() {
  const text = el.input.value;
  if (!text || !text.trim()) {
    closeKeyDialog();
    return;
  }
  if (await invoke("is_demo_key", { text })) {
    await invoke("save_demo");
    toast("Ключ сохранён");
    closeKeyDialog();
    await render(false);
    return;
  }
  try {
    const resolved = await invoke("resolve_key", { text });
    await invoke("save_key", { text: resolved });
    toast("Ключ сохранён");
    closeKeyDialog();
    const up = await refreshStatus();
    await render(up);
  } catch (err) {
    toast(`Не получилось: ${messageOf(err)}. Скопируйте ключ из бота целиком и проверьте интернет.`);
  }
}

el.toggle.addEventListener("click", onToggleClicked);
el.keyButton.addEventListener("click", openKeyDialog);
el.cancel.addEventListener("click", closeKeyDialog);
el.done.addEventListener("click", saveKey);
el.pasteButton.addEventListener("click", async () => {
  const clip = await invoke("clipboard_text");
  if (clip) el.input.value = clip.trim();
});

(async () => {
  const up = await refreshStatus();
  await render(up);
  checkIp();
  setInterval(refreshStatus, 2000);
})();
