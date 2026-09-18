// 探测某个屏幕坐标上到底叠了哪些层：从命中元素一路往上，打印每层的
// 背景色 / 背景图 / backdrop-filter / 是否建立了层叠上下文。
// 用法：node _at.mjs 130 400
import { fetchTargets, CdpSession } from "./lib/cdp.mjs";

const PORT = 9347;
const x = Number(process.argv[2] ?? 130);
const y = Number(process.argv[3] ?? 400);

const targets = await fetchTargets(PORT);
if (!targets.length) { console.log(JSON.stringify({ ok: false, error: "CDP 未开启" })); process.exit(1); }

const expr = `(() => {
  const X = ${x}, Y = ${y};
  const probe = document.createElement("canvas");
  probe.width = probe.height = 1;
  const ctx = probe.getContext("2d");
  const alphaOf = (color) => {
    if (!color || color === "transparent" || color === "none") return 0;
    ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = "#000"; ctx.fillStyle = color;
    ctx.fillRect(0, 0, 1, 1);
    return ctx.getImageData(0, 0, 1, 1).data[3] / 255;
  };
  const chain = [];
  let el = document.elementFromPoint(X, Y);
  const start = el;
  while (el && el.nodeType === 1) {
    const s = getComputedStyle(el);
    const cls = (typeof el.className === "string" ? el.className : "").trim();
    chain.push({
      tag: el.tagName.toLowerCase(),
      id: el.id || undefined,
      cls: cls ? cls.slice(0, 70) : undefined,
      bg: s.backgroundColor,
      bgAlpha: Number(alphaOf(s.backgroundColor).toFixed(3)),
      bgImage: s.backgroundImage === "none" ? "none" : s.backgroundImage.slice(0, 50),
      backdrop: s.backdropFilter === "none" ? "none" : s.backdropFilter,
      filter: s.filter === "none" ? "none" : s.filter,
      opacity: s.opacity,
      isolation: s.isolation,
      position: s.position,
      zIndex: s.zIndex,
      mixBlend: s.mixBlendMode,
    });
    el = el.parentElement;
  }
  return JSON.stringify({
    point: X + "," + Y,
    hit: start ? (start.tagName.toLowerCase() + "." + (typeof start.className === "string" ? start.className : "").slice(0, 60)) : null,
    layers: chain,
  }, null, 2);
})()`;

const session = await new CdpSession(targets[0], PORT).open();
try { console.log(await session.evaluate(expr)); }
finally { session.close(); }
