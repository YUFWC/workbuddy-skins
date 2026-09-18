// 输入框右下角（模型选择器 / 发送键）的 hover 底色探测。
// getComputedStyle 不应用 :hover，所以必须用合成鼠标事件逼出来。
import { fetchTargets, CdpSession } from "./lib/cdp.mjs";

const PORT = 9347;
const targets = await fetchTargets(PORT);
if (!targets.length) { console.log("no target"); process.exit(1); }
const session = await new CdpSession(targets[0], PORT).open();

const scan = `(() => {
  const probe = document.createElement("canvas"); probe.width = probe.height = 1;
  const ctx = probe.getContext("2d");
  const alphaOf = (c) => { if (!c || c === "transparent" || c === "none") return 0;
    ctx.clearRect(0,0,1,1); ctx.fillStyle = "#000"; ctx.fillStyle = c; ctx.fillRect(0,0,1,1);
    return ctx.getImageData(0,0,1,1).data[3] / 255; };
  const root = document.querySelector(".cr-input-toolbar");
  if (!root) return "[]";
  const out = [];
  const consider = (el, tag) => {
    const s = getComputedStyle(el);
    if (s.display === "none" || s.visibility === "hidden") return;
    const reasons = [];
    const a = alphaOf(s.backgroundColor);
    if (a > 0.02) reasons.push("bg-color " + s.backgroundColor);
    if (s.backgroundImage && s.backgroundImage !== "none") reasons.push("bg-image " + s.backgroundImage.slice(0, 46));
    if (s.boxShadow && s.boxShadow !== "none") reasons.push("shadow " + s.boxShadow.slice(0, 46));
    if (s.backdropFilter && s.backdropFilter !== "none") reasons.push("backdrop " + s.backdropFilter);
    if (parseFloat(s.borderTopWidth) + parseFloat(s.borderBottomWidth) > 0) reasons.push("border " + s.borderTopWidth + " " + s.borderTopColor);
    if (parseFloat(s.outlineWidth) > 0) reasons.push("outline " + s.outlineWidth + " " + s.outlineColor);
    if (!reasons.length) return;
    const r = el.getBoundingClientRect();
    if (r.width < 6 || r.height < 6) return;
    const cls = (typeof el.className === "string" ? el.className : "").trim();
    out.push({ which: tag, cls: cls.slice(0, 60) || el.tagName.toLowerCase(),
      rect: Math.round(r.left) + "," + Math.round(r.top) + " " + Math.round(r.width) + "x" + Math.round(r.height),
      why: reasons });
  };
  for (const el of root.querySelectorAll("*")) {
    consider(el, "el");
    for (const w of ["::before", "::after"]) {
      const b = getComputedStyle(el, w);
      if (b.content === "none") continue;
      const ba = alphaOf(b.backgroundColor);
      if (ba > 0.02 || (b.backgroundImage && b.backgroundImage !== "none") || (b.boxShadow && b.boxShadow !== "none")) {
        const r = el.getBoundingClientRect();
        const cls = (typeof el.className === "string" ? el.className : "").trim();
        out.push({ which: w, cls: cls.slice(0, 60) || el.tagName.toLowerCase(),
          rect: Math.round(r.left) + "," + Math.round(r.top) + " " + Math.round(r.width) + "x" + Math.round(r.height),
          why: ["pseudo bg " + b.backgroundColor + " / " + String(b.backgroundImage).slice(0, 40)] });
      }
    }
  }
  return JSON.stringify(out, null, 2);
})()`;

const targetsExpr = `(() => {
  const pick = (sel) => { const el = document.querySelector(sel); if (!el) return null;
    const r = el.getBoundingClientRect();
    return { sel, x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)] }; };
  return JSON.stringify([pick(".cr-input-toolbar__right"), pick(".cr-model-selector__trigger"), pick(".cr-context-usage"), pick(".cr-send-button")].filter(Boolean));
})()`;

const move = (x, y) => session.send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y, button: "none", buttons: 0 }, 8000);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

try {
  const spots = JSON.parse(await session.evaluate(targetsExpr));
  console.log("目标:", JSON.stringify(spots));

  await move(660, 800);
  await sleep(300);
  console.log("\n--- 鼠标移开（对照）---");
  console.log(await session.evaluate(scan));

  for (const s of spots) {
    await move(s.x, s.y);
    await sleep(400);
    console.log(`\n--- 悬停 ${s.sel} (${s.x},${s.y}) ---`);
    console.log(await session.evaluate(scan));
  }
} finally {
  await move(660, 800).catch(() => {});
  session.close();
}
