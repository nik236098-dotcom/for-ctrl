const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../src/main.js"), "utf8");
const flush = () => new Promise((resolve) => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

const SERVERS = [
  { id: "ru", name: "Россия", country: "RU" },
  { id: "us", name: "США", country: "US" },
];

async function app(overrides = {}) {
  const state = {
    connected: true, ipFails: false, selected: "ru",
    disconnects: 0, connects: 0, connectedTo: [], hooks: {}, ...overrides,
  };
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, {
      textContent: "", value: "", hidden: false, innerHTML: "", disabled: false,
      classList: { toggle() {}, add() {}, remove() {} },
      setAttribute() {}, addEventListener() {}, focus() {},
    });
    return elements.get(id);
  };
  const invoke = async (command, args) => {
    if (state.hooks[command]) return state.hooks[command](args);
    switch (command) {
      case "tunnel_status": return { up: state.connected, rx: 10000, tx: 20000, handshake: true };
      case "check_ip":
        if (state.ipFails) throw new Error("IP lookup timeout");
        return { ip: "198.51.100.1", country_code: "RU" };
      case "disconnect": state.disconnects++; state.connected = false; return;
      case "connect":
        state.connects++; state.connected = true; state.connectedTo.push(state.selected); return;
      case "list_servers": return { selected: state.selected, servers: SERVERS };
      case "select_server": state.selected = args.id; return;
      default: throw new Error(`Unexpected command: ${command}`);
    }
  };
  const intervals = [];
  const context = vm.createContext({
    window: { __TAURI__: { core: { invoke } } },
    document: { getElementById: element },
    setTimeout: () => 1, clearTimeout() {},
    setInterval: (fn, ms) => { intervals.push({ fn, ms }); },
  });
  vm.runInContext(source, context);
  await flush(); // Complete startup rendering; timers are manually driven.
  return { state, context, element, intervals };
}

test("no key UI: the bottom button shows the selected server", async () => {
  const a = await app();
  assert.equal(a.element("buttonServer").textContent, "Сервер: Россия");
  const code = source.replace(/\/\/.*$/gm, "");
  assert.ok(!/(^|[^а-яё])ключ/i.test(code), "no key wording left in the UI code");
  const html = fs.readFileSync(path.join(__dirname, "../src/index.html"), "utf8");
  assert.ok(!/(^|[^а-яё])ключ/i.test(html), "no key wording left in the page");
});

test("repeated IP lookup failures never disconnect an active VPN", async () => {
  const a = await app({ ipFails: true });
  const ipTick = a.intervals.find(({ ms }) => ms === 30000);
  assert.ok(ipTick);
  for (let n = 0; n < 30; n++) await ipTick.fn();
  assert.equal(a.state.disconnects, 0);
  assert.equal(a.state.connected, true);
  assert.equal(a.element("textIp").textContent, "Ваш ip: недоступно");
});

test("toggle connects without any key and disconnects when up", async () => {
  const a = await app({ connected: false });
  await a.context.onToggleClicked();
  assert.equal(a.state.connects, 1);
  assert.deepEqual(a.state.connectedTo, ["ru"]);
  await a.context.onToggleClicked();
  assert.equal(a.state.disconnects, 1);
  assert.equal(a.state.connected, false);
});

test("double click starts one connection", async () => {
  const a = await app({ connected: false });
  const gate = deferred();
  a.state.hooks.connect = async () => { a.state.connects++; await gate.promise; a.state.connected = true; };
  const first = a.context.onToggleClicked();
  const second = a.context.onToggleClicked();
  gate.resolve();
  await Promise.all([first, second]);
  assert.equal(a.state.connects, 1);
});

test("connection error is shown and always releases busy state", async () => {
  const a = await app({ connected: false });
  a.state.hooks.connect = () => { throw new Error("сервер «Россия» не отвечает"); };
  await a.context.onToggleClicked();
  assert.equal(a.element("buttonToggle").disabled, false);
  assert.equal(a.element("connectingRing").hidden, true);
  assert.doesNotMatch(a.element("textStatus").textContent, /Наводим/);
  assert.match(a.element("toast").textContent, /не отвечает/);
});

test("failed status query never triggers a blind connection", async () => {
  const a = await app();
  a.state.hooks.tunnel_status = () => { throw new Error("Query failure"); };
  await a.context.onToggleClicked();
  assert.equal(a.state.connects, 0);
  assert.equal(a.state.disconnects, 0);
  assert.match(a.element("textStatus").textContent, /Не удалось проверить/);
});

test("switching server while connected reconnects through the new server", async () => {
  const a = await app();
  await a.context.chooseServer("us");
  assert.equal(a.state.selected, "us");
  assert.equal(a.state.disconnects, 1);
  assert.deepEqual(a.state.connectedTo, ["us"]);
  assert.equal(a.element("buttonServer").textContent, "Сервер: США");
  await a.context.chooseServer("us");
  assert.equal(a.state.connects, 1, "choosing the current server changes nothing");
});

test("switching server while disconnected only changes the selection", async () => {
  const a = await app({ connected: false });
  await a.context.chooseServer("us");
  assert.equal(a.state.selected, "us");
  assert.equal(a.state.connects, 0);
});

test("reconnect stops after a failed disconnect", async () => {
  const a = await app();
  a.state.hooks.disconnect = () => { throw new Error("Access denied"); };
  await a.context.chooseServer("us");
  assert.equal(a.state.connects, 0);
  assert.equal(a.element("buttonToggle").disabled, false);
  assert.match(a.element("toast").textContent, /Access denied/);
});

test("waiting for the server is visible when there is no handshake", async () => {
  const a = await app();
  a.state.hooks.tunnel_status = () => ({ up: true, rx: 0, tx: 148, handshake: false });
  await a.context.refreshStatus();
  assert.equal(a.element("textStatus").textContent, "Ждём ответа сервера…");
});
