/* 资料库 iframe 自愈注入器 —— 住进**主渲染进程**，不依赖任何常驻外部进程。
 *
 * 背景（2026-09-17 实测确认）：
 *   * 资料库是跨域 OOPIF（<iframe class="space-panel-iframe" src="https://www.workbuddy.cn/space/...">）
 *     父文档读不到它的 DOM（SecurityError），父文档 CSS 也进不去；
 *   * iframe target 上 Page.addScriptToEvaluateOnNewDocument **返回 identifier 却永不触发**
 *     （Chromium 对 OOPIF 的限制），所以没法"原生持久化"；
 *   * 父文档**能**监听到 iframe 的 load 事件（实测 ticks 0→2）；
 *   * 渲染进程内的 fetch('http://127.0.0.1:PORT/json/list') **能成功**（不受同源策略限制）；
 *   * 但渲染进程的 WebSocket 会自动带 Origin 头，被 CDP 以 403 拒绝 ——
 *     除非应用启动时带了 --remote-allow-origins=*（inject.py 的 launch_with_cdp 已加）。
 *
 * 于是方案就是：
 *   主渲染进程监听到 iframe load → fetch /json/list 找到它的 target →
 *   WebSocket 连上去 → Runtime.evaluate 注入 <style>。
 *   全部发生在渲染进程内部，WorkBuddy 一开着就永远有效，不需要 watchdog 进程。
 */
(() => {
  const STYLE_ID = "wbas-space-glass";
  const HTML_CLASS = "wbas-space";
  const CSS_URL_HINT = "space-glass.css";
  const MARK = "__wbasSpaceSelfHeal";
  const LOG = (...args) => { try { console.log("[wbas-selfheal]", ...args); } catch (e) {} };

  if (window[MARK] && window[MARK].version >= 2) {
    window[MARK].poke();
    return "already-installed";
  }

  // 端口：从当前页面的 CDP 探测（应用可能跑在 9347/9348/…）。
  // 主文档是 file://…app.asar/renderer/index.html，本身不带端口信息，
  // 所以扫一遍常见端口，谁能列出 target 就用谁。
  const PORTS = [
    Number(new URLSearchParams(location.search).get("__cdpPort")) || 0,
    9347, 9348, 9349, 9350, 9351, 9352,
  ].filter(Boolean);

  let cssText = null;      // 由外部（Python）注入时带进来；见 install(payload)
  let activePort = null;

  const state = {
    version: 2,
    installedAt: Date.now(),
    repairs: 0,
    lastRepair: 0,
    port: null,
    iframeSrc: null,
    poke: () => scheduleVerify(0),
  };
  window[MARK] = state;

  // ---- 1) 找端口 + 找资料库 iframe 的 target ------------------------------
  async function discover() {
    if (activePort) {
      const hit = await findIframeTarget(activePort);
      if (hit) return hit;
    }
    for (const port of PORTS) {
      const hit = await findIframeTarget(port);
      if (hit) { activePort = port; state.port = port; return hit; }
    }
    return null;
  }

  async function findIframeTarget(port) {
    try {
      const response = await fetch("http://127.0.0.1:" + port + "/json/list", { cache: "no-store" });
      if (!response.ok) return null;
      const list = await response.json();
      const domIframe = document.querySelector("iframe.space-panel-iframe, iframe[src*='/space/']");
      const domSrc = domIframe ? domIframe.src : "";
      // 优先挑 url 跟页面上那个 iframe 对得上的
      const candidates = list.filter((t) => t.type === "iframe" &&
                                     /workbuddy\.(cn|com)\//.test(t.url || ""));
      if (!candidates.length) return null;
      let best = candidates[0];
      if (domSrc) {
        const hit = candidates.find((t) => {
          try { return new URL(t.url).origin === new URL(domSrc).origin; }
          catch (e) { return false; }
        });
        if (hit) best = hit;
      }
      state.iframeSrc = (best.url || "").slice(0, 120);
      return { port, ws: best.webSocketDebuggerUrl, url: best.url };
    } catch (error) {
      return null;
    }
  }

  // ---- 2) 往 iframe 注入样式 ---------------------------------------------
  function injectViaCdp(wsUrl, text) {
    return new Promise((resolve) => {
      let settled = false;
      let socket;
      const finish = (value) => {
        if (settled) return;
        settled = true;
        try { socket && socket.close(); } catch (e) {}
        resolve(value);
      };
      try {
        socket = new WebSocket(wsUrl);
      } catch (error) {
        return finish(false);
      }
      const timer = setTimeout(() => finish(false), 6000);
      socket.onopen = () => {
        const expression = `(() => {
          document.documentElement.classList.add(${JSON.stringify(HTML_CLASS)});
          let style = document.getElementById(${JSON.stringify(STYLE_ID)});
          if (!style) {
            style = document.createElement('style');
            style.id = ${JSON.stringify(STYLE_ID)};
            (document.head || document.documentElement).appendChild(style);
          }
          style.textContent = ${JSON.stringify(text)};
          return JSON.stringify({
            ok: true, bytes: style.textContent.length,
            cls: document.documentElement.className,
            bg: getComputedStyle(document.body).backgroundColor,
          });
        })()`;
        socket.send(JSON.stringify({
          id: 1, method: "Runtime.evaluate",
          params: { expression, returnByValue: true, allowUnsafeEvalBlockedByCSP: true },
        }));
      };
      socket.onmessage = (event) => {
        clearTimeout(timer);
        let ok = false;
        try { ok = /"ok":true/.test(String(event.data)); } catch (e) {}
        finish(ok);
      };
      socket.onerror = () => { clearTimeout(timer); finish(false); };
    });
  }

  // ---- 3) 校验 + 自愈 ----------------------------------------------------
  function checkStyle(wsUrl) {
    return new Promise((resolve) => {
      let settled = false;
      let socket;
      const finish = (value) => {
        if (settled) return;
        settled = true;
        try { socket && socket.close(); } catch (e) {}
        resolve(value);
      };
      try { socket = new WebSocket(wsUrl); } catch (error) { return finish(false); }
      const timer = setTimeout(() => finish(true), 3000);  // 超时别乱补，免得打架
      socket.onopen = () => {
        socket.send(JSON.stringify({
          id: 1, method: "Runtime.evaluate",
          params: { expression: `!!document.getElementById(${JSON.stringify(STYLE_ID)})`,
                    returnByValue: true },
        }));
      };
      socket.onmessage = (event) => {
        clearTimeout(timer);
        try {
          const parsed = JSON.parse(String(event.data));
          finish(parsed.result && parsed.result.result && parsed.result.result.value === true);
        } catch (e) { finish(true); }
      };
      socket.onerror = () => { clearTimeout(timer); finish(false); };
    });
  }

  async function verifyAndRepair(reason) {
    if (!cssText) return false;
    const target = await discover();
    if (!target) return false;
    const alive = await checkStyle(target.ws);
    if (alive) return true;
    const ok = await injectViaCdp(target.ws, cssText);
    if (ok) {
      state.repairs++;
      state.lastRepair = Date.now();
      LOG("已补注入（第 " + state.repairs + " 次，原因：" + reason + "）");
    }
    return ok;
  }

  // ---- 4) 触发时机 -------------------------------------------------------
  let scheduled = null;
  function scheduleVerify(delay) {
    if (scheduled) clearTimeout(scheduled);
    scheduled = setTimeout(() => { scheduled = null; verifyAndRepair("scheduled"); },
                           typeof delay === "number" ? delay : 250);
  }

  // 4a) 给页面上已有的 / 将来出现的 iframe 挂 load 监听
  const hookIframe = (frame) => {
    if (!frame || frame.__wbasHooked) return;
    frame.__wbasHooked = true;
    frame.addEventListener("load", () => scheduleVerify(300));
    LOG("已给 iframe 挂上 load 监听");
  };
  document.querySelectorAll("iframe").forEach(hookIframe);

  // 4b) MutationObserver：资料库面板是新插入的 iframe 时也能第一时间挂上
  try {
    const observer = new MutationObserver((records) => {
      for (const record of records) {
        for (const node of record.addedNodes) {
          if (node && node.tagName === "IFRAME") { hookIframe(node); scheduleVerify(500); }
          else if (node && node.querySelectorAll) {
            node.querySelectorAll("iframe").forEach((f) => { hookIframe(f); scheduleVerify(500); });
          }
        }
      }
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
    state.observer = true;
  } catch (error) { LOG("MutationObserver 装不上", error); }

  // 4c) 兜底轮询：极少数情况下 load 不冒泡（比如 iframe 只是导航而非替换）
  state.timer = setInterval(() => {
    const frame = document.querySelector("iframe.space-panel-iframe, iframe[src*='/space/']");
    if (frame) scheduleVerify(0);
  }, 5000);

  // ---- 5) 首次：等外部把 CSS 文本送进来 ----------------------------------
  state.install = async (text) => {
    cssText = text;
    const ok = await verifyAndRepair("install");
    return { ok, repairs: state.repairs, port: activePort };
  };

  window.dispatchEvent(new CustomEvent("wbas-space-selfheal-ready"));
  return "installed";
})()
