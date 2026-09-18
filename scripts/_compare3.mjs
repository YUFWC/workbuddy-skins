// 三段对比取证：临时把壁纸换成指定图片，分别拍
//   1) 还原成修复前的 CSS（gridViewItem 22% + 强遮罩 + 标签 56.8%），壁纸 54%
//   2) 当前 CSS（本地补丁生效），壁纸 54%
//   3) 当前 CSS，壁纸 100%
// 拍完全部还原，并逐项断言还原成功（上一版脚本因为没 JSON.parse 把壁纸卡在 100% 过）。
import { readFile, writeFile } from "node:fs/promises";
import { extname } from "node:path";
import { fetchTargets, CdpSession } from "./lib/cdp.mjs";

const PORT = 9347;
const [imagePath, outA, outB, outC] = process.argv.slice(2);
if (!imagePath || !outA || !outB || !outC) {
  console.log("usage: node _compare3.mjs <wallpaper.webp> <a.png> <b.png> <c.png>");
  process.exit(1);
}

const mime = extname(imagePath).toLowerCase() === ".png" ? "image/png" : "image/webp";
const dataUrl = `data:${mime};base64,${(await readFile(imagePath)).toString("base64")}`;

const targets = await fetchTargets(PORT);
if (!targets.length) { console.log("no target"); process.exit(1); }
const session = await new CdpSession(targets[0], PORT).open();

const ID = "__cmp3__";
const OPACITY_VARS = ["--wbas-home-opacity", "--wbas-work-opacity", "--wbas-detail-opacity"];

// 修复前的样式（用来做"before"面板）
const REVERT_CSS = `
:root.workbuddy-ambient-skin [class*="gridViewItem"] {
  background-color: color-mix(in srgb, var(--wbas-surface) 22%, transparent) !important;
}
:root.workbuddy-ambient-skin .conversation-section-label {
  background: color-mix(in srgb, var(--wbas-surface) 86%, transparent) !important;
  backdrop-filter: blur(14px) saturate(1.04) !important;
}
:root.workbuddy-ambient-skin #root::after {
  background: linear-gradient(180deg, transparent 0 42%, color-mix(in srgb, var(--wbas-surface) 72%, transparent) 100%) !important;
  opacity: .72 !important;
}
:root.workbuddy-ambient-skin[data-wbas-safe="right"] #root::after {
  background: linear-gradient(270deg, color-mix(in srgb, var(--wbas-surface) 90%, transparent) 0 22%, transparent 52%),
              linear-gradient(180deg, transparent 0 42%, color-mix(in srgb, var(--wbas-surface) 72%, transparent) 100%) !important;
}
`;

const setup = `(() => {
  document.getElementById(${JSON.stringify(ID)})?.remove();
  const s = document.createElement("style");
  s.id = ${JSON.stringify(ID)};
  s.textContent = ${JSON.stringify(
    `:root.workbuddy-ambient-skin #root::before{background-image:url(${JSON.stringify(dataUrl)}) !important;background-size:cover !important;background-position:50% 40% !important;}`,
  )};
  document.head.appendChild(s);
  return "wallpaper swapped";
})()`;

const revertOn = `(() => {
  const s = document.createElement("style"); s.id = ${JSON.stringify(ID + "-rev")};
  s.textContent = ${JSON.stringify(REVERT_CSS)};
  document.head.appendChild(s); return "revert on";
})()`;
const revertOff = `(() => { document.getElementById(${JSON.stringify(ID + "-rev")})?.remove(); return "revert off"; })()`;

const readState = `(() => {
  const de = document.documentElement, s = de.style;
  return JSON.stringify({
    vars: Object.fromEntries(${JSON.stringify(OPACITY_VARS)}.map((v) => [v, s.getPropertyValue(v)])),
    inlineAttr: de.getAttribute("style") || "",
    current: getComputedStyle(de).getPropertyValue("--wbas-current-opacity").trim(),
  });
})()`;

const restore = (snap) => `(() => {
  const s = document.documentElement.style;
  const snap = ${JSON.stringify(snap)};
  for (const k of Object.keys(snap.vars)) { if (snap.vars[k]) s.setProperty(k, snap.vars[k]); }
  document.getElementById(${JSON.stringify(ID)})?.remove();
  document.getElementById(${JSON.stringify(ID + "-rev")})?.remove();
  return getComputedStyle(document.documentElement).getPropertyValue("--wbas-current-opacity").trim();
})()`;

const bump = `(() => { const s = document.documentElement.style; ${JSON.stringify(OPACITY_VARS)}.forEach((v) => s.setProperty(v, "1")); return getComputedStyle(document.documentElement).getPropertyValue("--wbas-current-opacity").trim(); })()`;

const shoot = async (path) => {
  const r = await session.send("Page.captureScreenshot", { format: "png" }, 20000);
  const buf = Buffer.from(r.data, "base64");
  await writeFile(path, buf);
  return buf.length;
};

const snapshot = JSON.parse(await session.evaluate(readState));
console.log("原始:", snapshot.vars, "current =", snapshot.current);
try {
  await session.evaluate(setup);

  await session.evaluate(revertOn);
  await new Promise((r) => setTimeout(r, 700));
  console.log("A (修复前 / 54%):", await shoot(outA), "bytes");

  await session.evaluate(revertOff);
  await new Promise((r) => setTimeout(r, 700));
  console.log("B (修复后 / 54%):", await shoot(outB), "bytes");

  console.log("  拉到 100% ->", await session.evaluate(bump));
  await new Promise((r) => setTimeout(r, 700));
  console.log("C (修复后 / 100%):", await shoot(outC), "bytes");
} finally {
  const back = await session.evaluate(restore(snapshot)).catch((e) => "restore failed: " + e.message);
  const after = JSON.parse(await session.evaluate(readState).catch(() => "{}"));
  console.log("还原后 current =", back, "| 变量 =", after.vars, "| 临时 style =", after.inlineAttr.includes("__cmp3__"));
  session.close();
}
