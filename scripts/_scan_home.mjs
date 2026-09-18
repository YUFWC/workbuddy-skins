// 原子扫描：点「新建任务」→ 等渲染 → 列出 home 页里所有带背景的元素（含伪元素、含渐变），按面积排序。
import { writeFile } from "node:fs/promises";
import { fetchTargets, CdpSession } from "./lib/cdp.mjs";
import { fileURLToPath } from "node:url";

const PORT = 9347;
const DIR = process.argv[2] || fileURLToPath(new URL("../_诊断截图", import.meta.url));

const targets = await fetchTargets(PORT);
if (!targets.length) { console.log("no target"); process.exit(1); }
const session = await new CdpSession(targets[0], PORT).open();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const SCAN = `(() => {
  const probe = document.createElement("canvas"); probe.width = probe.height = 1;
  const ctx = probe.getContext("2d");
  const alphaOf = (c) => { if (!c || c === "transparent" || c === "none") return 0;
    ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = "#000"; ctx.fillStyle = c; ctx.fillRect(0, 0, 1, 1);
    return ctx.getImageData(0, 0, 1, 1).data[3] / 255; };
  const root = document.querySelector("main.wb-home-route");
  if (!root) return JSON.stringify({ error: "no main.wb-home-route", hasShell: !!document.querySelector(".conversation-shell") });
  const items = [];
  for (const el of root.querySelectorAll("*")) {
    const cs = getComputedStyle(el);
    const a = alphaOf(cs.backgroundColor);
    const hasImg = cs.backgroundImage !== "none";
    const pseudo = [];
    for (const w of ["::before", "::after"]) {
      const p = getComputedStyle(el, w);
      if (p.content === "none") continue;
      const pa = alphaOf(p.backgroundColor);
      if (pa > 0.1 || p.backgroundImage !== "none") pseudo.push(w + ":" + p.backgroundColor + "|" + String(p.backgroundImage).slice(0, 60));
    }
    if (a < 0.1 && !hasImg && !pseudo.length) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) continue;
    items.push({
      t: el.tagName.toLowerCase(),
      cls: (el.className && el.className.toString ? el.className.toString() : "").slice(0, 62),
      bg: cs.backgroundColor, a: +a.toFixed(2),
      bgi: hasImg ? cs.backgroundImage.slice(0, 80) : null,
      bf: cs.backdropFilter === "none" ? null : cs.backdropFilter,
      pseudo,
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      area: Math.round(r.width * r.height)
    });
  }
  items.sort((p, q) => q.area - p.area);
  return JSON.stringify({
    rootRect: (() => { const r = root.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; })(),
    count: items.length,
    items: items.slice(0, 30)
  }, null, 2);
})()`;

try {
  await session.evaluate(`(() => { const b = [...document.querySelectorAll("button")].find(x => (x.textContent || "").trim() === "新建任务"); if (b) b.click(); return 1; })()`);
  await sleep(2200);
  const r = await session.send("Page.captureScreenshot", { format: "png" }, 30000);
  await writeFile(`${DIR}/home_scan.png`, Buffer.from(r.data, "base64"));
  console.log(await session.evaluate(SCAN));
} finally {
  session.close();
}
