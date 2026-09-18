// 原子对比：同一次运行内先拍对话页、再拍新建任务页，避免主题在两次之间被切换。
// 同时把「覆盖主内容区中心点、且带任何背景」的元素整份打出来（含伪元素）。
import { writeFile } from "node:fs/promises";
import { fetchTargets, CdpSession } from "./lib/cdp.mjs";
import { fileURLToPath } from "node:url";

const PORT = 9347;
const DIR = process.argv[2] || fileURLToPath(new URL("../_诊断截图", import.meta.url));

const targets = await fetchTargets(PORT);
if (!targets.length) { console.log("no target"); process.exit(1); }
const session = await new CdpSession(targets[0], PORT).open();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const shot = async (out) => {
  const r = await session.send("Page.captureScreenshot", { format: "png" }, 30000);
  await writeFile(out, Buffer.from(r.data, "base64"));
};

const SCAN = `(() => {
  const h = document.documentElement;
  const probe = document.createElement("canvas"); probe.width = probe.height = 1;
  const ctx = probe.getContext("2d");
  const alphaOf = (c) => { if (!c || c === "transparent" || c === "none") return 0;
    ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = "#000"; ctx.fillStyle = c; ctx.fillRect(0, 0, 1, 1);
    return ctx.getImageData(0, 0, 1, 1).data[3] / 255; };
  const PT = [700, 450];
  const cover = [];
  for (const el of document.querySelectorAll("*")) {
    const r = el.getBoundingClientRect();
    if (!(r.left <= PT[0] && r.right >= PT[0] && r.top <= PT[1] && r.bottom >= PT[1])) continue;
    const cs = getComputedStyle(el);
    const a = alphaOf(cs.backgroundColor);
    const reasons = [];
    if (a > 0.02) reasons.push("bg " + cs.backgroundColor);
    if (cs.backgroundImage !== "none") reasons.push("bgi " + cs.backgroundImage.slice(0, 60));
    if (cs.backdropFilter !== "none") reasons.push("bf " + cs.backdropFilter);
    if (cs.filter !== "none") reasons.push("filter " + cs.filter);
    if (parseFloat(cs.opacity) < 1) reasons.push("opacity " + cs.opacity);
    // 伪元素
    for (const w of ["::before", "::after"]) {
      const p = getComputedStyle(el, w);
      if (p.content === "none") continue;
      const pa = alphaOf(p.backgroundColor);
      if (pa > 0.02 || p.backgroundImage !== "none") reasons.push(w + " bg " + p.backgroundColor + " / " + String(p.backgroundImage).slice(0, 50));
    }
    if (!reasons.length) continue;
    const area = Math.round(r.width) * Math.round(r.height);
    if (area < 40000) continue;
    const chain = [];
    let n = el;
    while (n && n !== document.documentElement) {
      chain.push(n.tagName.toLowerCase() + (n.id ? "#" + n.id : "") + "." + ((n.className && n.className.toString) ? n.className.toString() : "").slice(0, 44) + "|" + (n.getAttribute ? n.getAttribute("data-view-id") : null));
      n = n.parentElement;
    }
    cover.push({ t: el.tagName.toLowerCase(),
      cls: (el.className && el.className.toString ? el.className.toString() : "").slice(0, 60),
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)], reasons,
      chain: chain.slice(0, 6) });
  }
  // 所有带背景的元素里，面积占比最大的前 8 个（不限点位）
  const all = [];
  for (const el of document.querySelectorAll("*")) {
    const cs = getComputedStyle(el);
    const a = alphaOf(cs.backgroundColor);
    if (a < 0.05 && cs.backgroundImage === "none") continue;
    const r = el.getBoundingClientRect();
    const ar = Math.round(r.width) * Math.round(r.height);
    if (ar < 100000) continue;
    all.push({ t: el.tagName.toLowerCase(),
      cls: (el.className && el.className.toString ? el.className.toString() : "").slice(0, 56),
      bg: cs.backgroundColor, bgi: cs.backgroundImage === "none" ? null : cs.backgroundImage.slice(0, 40),
      a: +a.toFixed(2), rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  }
  all.sort((p, q) => q.rect[2] * q.rect[3] - p.rect[2] * p.rect[3]);
  return JSON.stringify({
    mode: h.getAttribute("data-wbas-mode"), htmlCls: h.className,
    surface: getComputedStyle(h).getPropertyValue("--wbas-surface").trim(),
    currentOpacity: getComputedStyle(h).getPropertyValue("--wbas-current-opacity").trim(),
    hasShell: !!document.querySelector(".conversation-shell"),
    mainContent: (() => { const m = document.querySelector('[data-view-id="main-content"]'); if (!m) return null; const r = m.getBoundingClientRect(); return { tag: m.tagName.toLowerCase(), cls: (m.className || "").toString().slice(0, 50), rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)], bg: getComputedStyle(m).backgroundColor }; })(),
    cover, bigBackgrounds: all.slice(0, 8)
  }, null, 2);
})()`;

try {
  // 1) 先回到对话页（点侧栏第一条任务）
  const back = await session.evaluate(`(() => {
    const card = document.querySelector('[data-view-id="sidebar"] .conversation-agent-card, [data-view-id="sidebar"] [class*="conversation-card"]');
    const b = [...document.querySelectorAll("button")].find(x => (x.textContent || "").trim() === "新建任务");
    return JSON.stringify({ hasCard: !!card, hasNewTaskBtn: !!b });
  })()`);
  console.log("侧栏:", back);

  // 如果当前不是 home，先拍对话页
  let st = JSON.parse(await session.evaluate(SCAN));
  if (st.hasShell) {
    await shot(`${DIR}/atom_conversation.png`);
    console.log("已拍对话页 | mode=" + st.mode + " surface=" + st.surface);
  }

  // 2) 点「新建任务」
  await session.evaluate(`(() => { const b = [...document.querySelectorAll("button")].find(x => (x.textContent || "").trim() === "新建任务"); if (b) b.click(); return 1; })()`);
  await sleep(2000);
  await shot(`${DIR}/atom_home.png`);
  st = JSON.parse(await session.evaluate(SCAN));
  console.log("\n=== 新建任务页 ===");
  console.log(JSON.stringify(st, null, 2));
} finally {
  session.close();
}
