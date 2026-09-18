import { RENDERER_SUFFIX } from "./constants.mjs";

function assertPort(port) {
  if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("invalid CDP port");
}

export function validDebuggerUrl(value, port) {
  assertPort(port);
  const url = new URL(value);
  if (url.protocol !== "ws:" || url.hostname !== "127.0.0.1" || Number(url.port) !== port
    || url.username || url.password || url.search || url.hash
    || !/^\/devtools\/page\/[A-Za-z0-9._-]{1,200}$/.test(url.pathname)) {
    throw new Error("rejected non-loopback or malformed CDP WebSocket URL");
  }
  return url.href;
}

export function validBrowserDebuggerUrl(value, port) {
  assertPort(port);
  const url = new URL(value);
  if (url.protocol !== "ws:" || url.hostname !== "127.0.0.1" || Number(url.port) !== port
    || url.username || url.password || url.search || url.hash
    || !/^\/devtools\/browser\/[A-Za-z0-9._-]{1,200}$/.test(url.pathname)) {
    throw new Error("rejected non-loopback or malformed browser WebSocket URL");
  }
  return { url: url.href, browserId: url.pathname.slice("/devtools/browser/".length) };
}

export async function fetchBrowserIdentity(port, { timeoutMs = 2000, fetchImpl = globalThis.fetch } = {}) {
  assertPort(port);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(`http://127.0.0.1:${port}/json/version`, { redirect: "error", signal: controller.signal });
    if (!response.ok) throw new Error(`CDP version discovery returned HTTP ${response.status}`);
    const payload = await response.json();
    if (!payload || typeof payload !== "object" || typeof payload.Browser !== "string" || !payload.Browser.trim()) {
      throw new Error("malformed CDP browser identity");
    }
    const identity = validBrowserDebuggerUrl(payload.webSocketDebuggerUrl, port);
    return { ...identity, product: payload.Browser };
  } finally { clearTimeout(timer); }
}

// 渲染页 URL 会带查询参数，例如：
//   file:///D:/.../app.asar/renderer/index.html?locale=zh-CN&accountSnapshot=%7B...
// 直接对整条 URL 做 endsWith 永远匹配不上，必须只比较 pathname。
function matchesRendererUrl(value) {
  try {
    return new URL(value).pathname.endsWith(RENDERER_SUFFIX);
  } catch {
    return value.endsWith(RENDERER_SUFFIX);
  }
}

export function filterWorkBuddyTargets(targets, port) {
  if (!Array.isArray(targets)) throw new Error("malformed CDP target list");
  return targets.filter((target) => {
    if (target?.type !== "page" || typeof target.url !== "string" || !matchesRendererUrl(target.url)) return false;
    try { validDebuggerUrl(target.webSocketDebuggerUrl, port); return true; } catch { return false; }
  });
}

export async function fetchTargets(port, { timeoutMs = 2000, fetchImpl = globalThis.fetch } = {}) {
  assertPort(port);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(`http://127.0.0.1:${port}/json/list`, { redirect: "error", signal: controller.signal });
    if (!response.ok) throw new Error(`CDP discovery returned HTTP ${response.status}`);
    return filterWorkBuddyTargets(await response.json(), port);
  } finally { clearTimeout(timer); }
}

export async function waitForTargets(port, { timeoutMs = 30000, pollMs = 250 } = {}) {
  const deadline = Date.now() + timeoutMs;
  let lastError = new Error("no WorkBuddy renderer target");
  while (Date.now() < deadline) {
    try {
      const targets = await fetchTargets(port);
      if (targets.length) return targets;
      lastError = new Error("CDP is ready but WorkBuddy renderer is not available");
    } catch (error) { lastError = error; }
    await new Promise((resolve) => setTimeout(resolve, pollMs));
  }
  throw new Error(`timed out waiting for WorkBuddy renderer: ${lastError.message}`);
}

export class CdpSession {
  constructor(target, port, { WebSocketImpl = globalThis.WebSocket } = {}) {
    this.url = validDebuggerUrl(target.webSocketDebuggerUrl, port);
    this.WebSocketImpl = WebSocketImpl;
    this.socket = null;
    this.nextId = 1;
    this.pending = new Map();
  }

  async open() {
    if (typeof this.WebSocketImpl !== "function") throw new Error("Node.js 22 or newer is required");
    this.socket = new this.WebSocketImpl(this.url);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("CDP WebSocket connection timed out")), 5000);
      this.socket.addEventListener("open", () => { clearTimeout(timer); resolve(); }, { once: true });
      this.socket.addEventListener("error", () => { clearTimeout(timer); reject(new Error("CDP WebSocket connection failed")); }, { once: true });
    });
    this.socket.addEventListener("message", (event) => this.#message(event));
    this.socket.addEventListener("close", () => this.#rejectAll(new Error("CDP WebSocket closed")));
    await Promise.all([this.send("Runtime.enable"), this.send("Page.enable")]);
    return this;
  }

  #message(event) {
    let message;
    try { message = JSON.parse(String(event.data)); } catch { this.close(); return; }
    if (!Number.isInteger(message?.id)) return;
    const pending = this.pending.get(message.id);
    if (!pending) return;
    clearTimeout(pending.timer); this.pending.delete(message.id);
    if (message.error) pending.reject(new Error(`CDP ${pending.method} failed: ${message.error.message}`));
    else pending.resolve(message.result);
  }

  #rejectAll(error) {
    for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(error); }
    this.pending.clear();
  }

  send(method, params = {}, timeoutMs = 10000) {
    if (!this.socket || this.socket.readyState !== this.WebSocketImpl.OPEN) return Promise.reject(new Error("CDP session is not open"));
    return new Promise((resolve, reject) => {
      const id = this.nextId++;
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`CDP ${method} timed out`)); }, timeoutMs);
      this.pending.set(id, { method, resolve, reject, timer });
      try { this.socket.send(JSON.stringify({ id, method, params })); }
      catch (error) { clearTimeout(timer); this.pending.delete(id); reject(error); }
    });
  }

  async evaluate(expression, timeoutMs = 10000) {
    const result = await this.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, timeoutMs);
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text || "renderer evaluation failed");
    return result.result?.value;
  }

  close() {
    this.#rejectAll(new Error("CDP session closed"));
    try { this.socket?.close(); } catch {}
  }
}

export async function evaluateAll(targets, port, expression, Session = CdpSession, timeoutMs = 10000) {
  const values = [];
  for (const target of targets) {
    const session = await new Session(target, port).open();
    try { values.push(await session.evaluate(expression, timeoutMs)); }
    finally { session.close(); }
  }
  return values;
}
