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
  busy = value;
  el.toggle.disabled = value;
  el.connectingRing.hidden = !value;
  el.powerOff.classList.toggle("connecting", value);
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
    el.ip.textContent = `Ваш ip: ${result.ip}`;
    el.ipFlag.innerHTML = flagSvg(result.country_code);
  } catch {
    el.ip.textContent = "Ваш ip: недоступно";
    el.ipFlag.innerHTML = "";
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
  const needsInstall = !(await invoke("wireguard_installed"));
  if (needsInstall) {
    // Обычно это происходит совсем незаметно (тихий msiexec), но на
    // случай отката на видимый установщик — предупреждаем заранее, а не
    // притворяемся, что окно точно не появится.
    toast("Настраиваем WireGuard — если появится окно установки, нажмите «Установить», это один раз");
  }
  setBusy(true);
  if (needsInstall) {
    // Отдельная подпись поверх общего "Наводим связь…" — чтобы было видно,
    // на каком именно шаге зависло, если что-то пойдёт не так.
    el.status.textContent = "Ставим WireGuard…";
  }
  let connectError = null;
  try {
    await invoke("connect", { configText: key });
  } catch (err) {
    connectError = messageOf(err);
    toast(`Не удалось соединиться: ${connectError}`, true);
  }
  setBusy(false);
  // connect() на стороне Rust теперь сам ждёт, пока служба тунеля реально
  // не станет RUNNING, и только тогда отдаёт успех — поэтому отдельно
  // проверять здесь "а вдруг тихо не поднялся" больше не нужно: это было
  // ложным срабатыванием (служба ещё не успевала стартовать к моменту
  // проверки), а не настоящей ошибкой.
  const up = await refreshStatus();
  await render(up);
  checkIp();
}

async function disconnectReal() {
  setBusy(true);
  try {
    await invoke("disconnect");
  } catch (err) {
    toast(`Не удалось разъединиться: ${messageOf(err)}`, true);
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
    await invoke("save_key", { text: resolved.text, code: resolved.code });
    toast("Ключ сохранён");
    closeKeyDialog();
    const up = await refreshStatus();
    await render(up);
  } catch (err) {
    toast(`Не получилось: ${messageOf(err)}. Скопируйте ключ из бота целиком и проверьте интернет.`, true);
  }
}

// --- смена сервера (страны) ------------------------------------------------

async function openCountryDialog() {
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
  if ((await invoke("current_country")) === country) {
    closeCountryDialog();
    return;
  }
  const wasUp = (await invoke("tunnel_status")).up;
  try {
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
  }
}

async function reconnectWithSavedKey() {
  setBusy(true);
  try {
    await invoke("disconnect");
  } catch {
    // тунеля могло уже не быть — не страшно, ниже всё равно поднимаем заново
  }
  const key = await invoke("load_key");
  if (key) {
    try {
      await invoke("connect", { configText: key });
    } catch (err) {
      toast(`Не удалось соединиться: ${messageOf(err)}`, true);
    }
  }
  setBusy(false);
  const up = await refreshStatus();
  await render(up);
  checkIp();
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
  const up = await refreshStatus();
  await render(up);
  checkIp();
  setInterval(refreshStatus, 2000);
})();
