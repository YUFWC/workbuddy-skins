// 扫描"仍在画不透明背景"的元素，定位壁纸被挡住的地方。
// 用法：
//   node _opaque.mjs                         全页扫描
//   node _opaque.mjs '[data-view-id="sidebar"]'   只看某个子树
import { fetchTargets, CdpSession } from "./lib/cdp.mjs";

const PORT = 9347;
const scope = process.argv[2] || "body";
// 阈值可调：侧栏导航项那种很淡的 hover 底色只有 ~20%，
// 用默认 0.3 会整片漏掉，排查时要往下压到 0.05。
const minAlpha = Number(process.argv[3] ?? 0.3);

const targets = await fetchTargets(PORT);
if (!targets.length) { console.log(JSON.stringify({ ok: false, error: "CDP 未开启" })); process.exit(1); }

const expr = `(() => {
  const SCOPE = ${JSON.stringify(scope)};
  const MIN_ALPHA = ${minAlpha};
  const root = document.querySelector(SCOPE);
  if (!root) return JSON.stringify({ ok: false, error: "scope not found: " + SCOPE });
  const total = innerWidth * innerHeight;
  const path = (el) => {
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && parts.length < 6) {
      let seg = cur.tagName.toLowerCase();
      if (cur.id) seg += "#" + cur.id;
      const cls = (typeof cur.className === "string" ? cur.className : "").trim();
      if (cls) seg += "." + cls.split(/\\s+/).slice(0, 3).join(".");
      parts.unshift(seg);
      cur = cur.parentElement;
    }
    return parts.join(" > ");
  };
  // 用 canvas 解析颜色 alpha：比正则稳，能同时吃 rgba(...) 和
  // color(srgb r g b / a) 这类新语法。（在模板字符串里写正则很容易被
  // 反斜杠转义坑到，别再回去用正则。）
  const probe = document.createElement("canvas");
  probe.width = probe.height = 1;
  const ctx = probe.getContext("2d");
  const alphaOf = (color) => {
    if (!color || color === "transparent" || color === "none") return 0;
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = "#000";
    ctx.fillStyle = color;
    ctx.fillRect(0, 0, 1, 1);
    return ctx.getImageData(0, 0, 1, 1).data[3] / 255;
  };
  const out = [];
  for (const el of root.querySelectorAll("*")) {
    if (el.id && el.id.indexOf("workbuddy-ambient-skin") === 0) continue;
    const s = getComputedStyle(el);
    if (s.display === "none" || s.visibility === "hidden") continue;
    const bg = s.backgroundColor;
    const alpha = alphaOf(bg);
    if (alpha <= MIN_ALPHA) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) continue;
    out.push({
      path: path(el),
      bg,
      alpha,
      covPct: Number(((r.width * r.height) / total * 100).toFixed(2)),
      rect: Math.round(r.left) + "," + Math.round(r.top) + " " + Math.round(r.width) + "x" + Math.round(r.height),
    });
  }
  out.sort((a, b) => b.covPct - a.covPct);
  return JSON.stringify({
    scope: SCOPE,
    scopeRect: (() => { const r = root.getBoundingClientRect(); return Math.round(r.left) + "," + Math.round(r.top) + " " + Math.round(r.width) + "x" + Math.round(r.height); })(),
    opaqueCount: out.length,
    opaque: out.slice(0, 20),
  }, null, 2);
})()`;

const session = await new CdpSession(targets[0], PORT).open();
try { console.log(await session.evaluate(expr)); }
finally { session.close(); }
