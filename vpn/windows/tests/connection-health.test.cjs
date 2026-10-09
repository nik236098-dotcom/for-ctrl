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

async function app(overrides = {}) {
  const state = {
    connected: true, demo: false, ipFails: false, keyStatus: "active",
    disconnects: 0, connects: 0, keyCalls: 0, country: "ru", hooks: {}, ...overrides,
  };
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, {
      textContent: "", value: "", hidden: false, innerHTML: "",
      classList: { toggle() {}, add() {}, remove() {} },
      setAttribute() {}, addEventListener() {}, focus() {},
    });
    return elements.get(id);
  };
  const invoke = async (command, args) => {
    if (state.hooks[command]) return state.hooks[command](args);
    switch (command) {
      case "is_demo": return state.demo;
      case "tunnel_status": return { up: state.connected, rx: 10000, tx: 20000 };
      case "check_ip":
        if (state.ipFails) throw new Error("IP lookup timeout");
        return { ip: "198.51.100.1", country_code: "RU" };
      case "key_status":
        state.keyCalls++;
        if (state.keyStatus instanceof Error) throw state.keyStatus;
        return state.keyStatus;
      case "disconnect": state.disconnects++; state.connected = false; return;
      case "connect": state.connects++; state.connected = true; return;
      case "load_key": return "test config";
      case "has_switchable_code": case "wireguard_installed": return true;
      case "current_country": return state.country;
      case "switch_country": state.country = args.country; return true;
      case "is_demo_key": return false;
      case "resolve_key": return { text: "new config", code: "newcode" };
      case "save_key": return;
      case "clipboard_text": return "";
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

test("repeated IP lookup failures never disconnect an active VPN", async () => {
  const a = await app({ ipFails: true });
  for (let n = 0; n < 30; n++) await a.context.watchConnection();
  assert.equal(a.state.disconnects, 0);
  assert.equal(a.state.connected, true);
  assert.equal(a.element("textIp").textContent, "Ваш ip: недоступно");
  assert.ok(a.intervals.some(({ fn, ms }) => fn === a.context.watchConnection && ms === 30000));
});

test("unknown key status, old server and network/IPC failure preserve the tunnel", async () => {
  for (const keyStatus of ["unknown", "unmanaged", new Error("timeout"), undefined, { status: "revoked" }]) {
    const a = await app({ keyStatus, ipFails: true });
    for (let n = 0; n < 3; n++) await a.context.watchConnection();
    assert.equal(a.state.disconnects, 0);
    assert.equal(a.state.connected, true);
  }
});

test("two explicit revocations disconnect even if IP lookup succeeds", async () => {
  const a = await app({ keyStatus: "revoked" });
  await a.context.watchConnection();
  assert.equal(a.state.disconnects, 0);
  await a.context.watchConnection();
  assert.equal(a.state.disconnects, 1);
  assert.match(a.element("toast").textContent, /Сервер подтвердил/);
});

test("an inconclusive or active response resets revocation confirmation", async () => {
  for (const middle of ["active", "unknown", new Error("network")]) {
    const a = await app({ keyStatus: "revoked" });
    await a.context.watchConnection();
    a.state.keyStatus = middle;
    await a.context.watchConnection();
    a.state.keyStatus = "revoked";
    await a.context.watchConnection();
    assert.equal(a.state.disconnects, 0);
    await a.context.watchConnection();
    assert.equal(a.state.disconnects, 1);
  }
});

test("a late revocation cannot disconnect a newer connection", async () => {
  const a = await app({ keyStatus: "revoked" });
  await a.context.watchConnection();
  const reply = deferred();
  a.state.hooks.key_status = () => reply.promise;
  const pending = a.context.watchConnection();
  await flush();
  a.context.setBusy(true);
  a.context.setBusy(false);
  reply.resolve("revoked");
  await pending;
  assert.equal(a.state.disconnects, 0);
  delete a.state.hooks.key_status;
  await a.context.watchConnection();
  assert.equal(a.state.disconnects, 0); // Fresh connection needs two fresh confirmations.
});

test("saving a replacement key invalidates an in-flight check", async () => {
  const a = await app({ keyStatus: "revoked" });
  await a.context.watchConnection();
  const reply = deferred();
  a.state.hooks.key_status = () => reply.promise;
  const pending = a.context.watchConnection();
  await flush();
  a.element("inputKey").value = "newcode";
  await a.context.saveKey();
  reply.resolve("revoked");
  await pending;
  assert.equal(a.state.disconnects, 0);
});

test("country switching ignores old checks and only disconnects for reconnection", async () => {
  const a = await app({ keyStatus: "revoked" });
  await a.context.watchConnection();
  const reply = deferred();
  a.state.hooks.key_status = () => reply.promise;
  const pending = a.context.watchConnection();
  await flush();
  await a.context.chooseCountry("us");
  assert.equal(a.state.disconnects, 1);
  reply.resolve("revoked");
  await pending;
  assert.equal(a.state.disconnects, 1);
  assert.equal(a.state.connected, true);
});

test("monitor is locked before its first await and recovers after an exception", async () => {
  const a = await app();
  const gate = deferred();
  let calls = 0;
  a.state.hooks.is_demo = () => { calls++; return gate.promise; };
  const pending = a.context.watchConnection();
  await a.context.watchConnection();
  assert.equal(calls, 1);
  gate.reject(new Error("IPC failure"));
  await pending;
  delete a.state.hooks.is_demo;
  await a.context.watchConnection();
  assert.equal(a.state.keyCalls, 1);
});

test("late IP errors do not overwrite the new connection address", async () => {
  const a = await app();
  const reply = deferred();
  a.state.hooks.check_ip = () => reply.promise;
  const pending = a.context.checkIp();
  a.context.setBusy(true);
  a.context.setBusy(false);
  a.element("textIp").textContent = "Ваш ip: 203.0.113.2";
  reply.reject(new Error("old request timeout"));
  await pending;
  assert.equal(a.element("textIp").textContent, "Ваш ip: 203.0.113.2");
});

test("manual disconnect remains available", async () => {
  const a = await app();
  await a.context.disconnectReal();
  assert.equal(a.state.disconnects, 1);
  assert.equal(a.state.connected, false);
});

test("an existing running tunnel is stopped by the toggle, never installed twice", async () => {
  const a = await app();
  await a.context.onToggleClicked();
  assert.equal(a.state.connects, 0);
  assert.equal(a.state.disconnects, 1);
  assert.equal(a.element("buttonToggle").disabled, false);
});

test("double click while status query is pending starts one connection", async () => {
  const a = await app({ connected: false });
  const status = deferred();
  a.state.hooks.tunnel_status = () => status.promise;
  const first = a.context.onToggleClicked();
  await a.context.onToggleClicked();
  delete a.state.hooks.tunnel_status;
  status.resolve({ up: false });
  await first;
  assert.equal(a.state.connects, 1);
  assert.equal(a.state.disconnects, 0);
});

test("failed service query never triggers blind installation", async () => {
  const a = await app();
  a.state.hooks.tunnel_status = () => { throw new Error("Access denied"); };
  await a.context.onToggleClicked();
  assert.equal(a.state.connects, 0);
  assert.equal(a.state.disconnects, 0);
  assert.equal(a.element("buttonToggle").disabled, false);
  assert.equal(a.element("connectingRing").hidden, true);
  assert.match(a.element("textStatus").textContent, /Не удалось проверить/);
});

test("install error and failed status refresh always release busy state", async () => {
  const a = await app({ connected: false });
  a.state.hooks.connect = () => { throw new Error("Tunnel already installed and running"); };
  a.state.hooks.tunnel_status = () => { throw new Error("Query failure"); };
  await a.context.connectReal();
  assert.equal(a.element("buttonToggle").disabled, false);
  assert.equal(a.element("connectingRing").hidden, true);
  assert.doesNotMatch(a.element("textStatus").textContent, /Наводим/);
  assert.match(a.element("toast").textContent, /already installed/);
});

test("key load failure also releases busy state", async () => {
  const a = await app();
  a.state.hooks.load_key = () => { throw new Error("read failed"); };
  await a.context.connectReal();
  assert.equal(a.element("buttonToggle").disabled, false);
  assert.equal(a.element("connectingRing").hidden, true);
});

test("reconnect must stop after uninstall failure", async () => {
  const a = await app();
  a.state.hooks.disconnect = () => { throw new Error("Access denied"); };
  await a.context.reconnectWithSavedKey();
  assert.equal(a.state.connects, 0);
  assert.equal(a.element("buttonToggle").disabled, false);
  assert.match(a.element("toast").textContent, /Access denied/);
});

test("startup status error does not stop subsequent polling", async () => {
  const a = await app({ hooks: { tunnel_status: () => { throw new Error("SCM failed"); } } });
  assert.ok(a.intervals.some(({ ms }) => ms === 2000));
  delete a.state.hooks.tunnel_status;
  await a.context.refreshStatus();
  assert.equal(a.element("textStatus").textContent, "Впн включен");
});
