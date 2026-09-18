// 输入框「内层浅色面板」成因实验：逐个变体注入 CSS → 截图 → 由 _sample.py 采样比对。
// 变体：
//   base     基线
//   nobf     去掉 ._editable 的 backdrop-filter
//   noglass  去掉 .cr-input-container 的玻璃底
//   both     两者都去
//   noeditablebg 把 _editable 强制改成容器同色玻璃（候选修复）
import { writeFile } from "node:fs/promises";
import { fetchTargets, CdpSession } from "./lib/cdp.mjs";
import { fileURLToPath } from "node:url";

const PORT = 9347;
const DIR = process.argv[2] || fileURLToPath(new URL("../_诊断截图", import.meta.url));

const EDIT = ':root.workbuddy-ambient-skin .cr-input-container [contenteditable="true"]';
const VARIANTS = {
  base: "",
  nobf: `${EDIT}{backdrop-filter:none !important;-webkit-backdrop-filter:none !important}`,
  // 机制验证：保留属性但 blur 半径归零 → 若仍偏亮，说明是"嵌套 backdrop-filter"本身，
  // 而不是模糊半径把亮部糊过来。
  bfzero: `${EDIT}{backdrop-filter:blur(0px) !important}`,
  // 机制验证：用与容器完全相同的值 → 若仍偏亮，同样说明是嵌套本身。
  bf14: `${EDIT}{backdrop-filter:blur(14px) saturate(1.05) !important}`,
  // 只保留 saturate，去掉 blur
  satonly: `${EDIT}{backdrop-filter:saturate(1.06) !important}`,
  // 修复候选：只清掉模糊，其余不动（作用域限定在输入框内的 contenteditable）
  fix: `${EDIT}{backdrop-filter:none !important;-webkit-backdrop-filter:none !important}`
};

const targets = await fetchTargets(PORT);
if (!targets.length) { console.log("no target"); process.exit(1); }
const session = await new CdpSession(targets[0], PORT).open();

const shot = async (out) => {
  const r = await session.send("Page.captureScreenshot", { format: "png" }, 30000);
  await writeFile(out, Buffer.from(r.data, "base64"));
};

const apply = async (css) => {
  const expr = `(() => {
    let s = document.getElementById("__exp");
    if (!s) { s = document.createElement("style"); s.id = "__exp"; document.head.appendChild(s); }
    s.textContent = ${JSON.stringify(css)};
    const e = document.querySelector('.cr-input-container [contenteditable="true"]');
    const c = document.querySelector(".cr-input-container");
    return JSON.stringify({
      editable: e ? [getComputedStyle(e).backgroundColor, getComputedStyle(e).backdropFilter] : null,
      container: c ? [getComputedStyle(c).backgroundColor, getComputedStyle(c).backdropFilter] : null
    });
  })()`;
  return JSON.parse(await session.evaluate(expr));
};

const results = {};
try {
  for (const [name, css] of Object.entries(VARIANTS)) {
    const info = await apply(css);
    await shot(`${DIR}/exp_${name}.png`);
    results[name] = info;
  }
  await apply("");
  const after = await session.evaluate(`(() => { const s = document.getElementById("__exp"); if (s) s.remove();
    return getComputedStyle(document.querySelector('.cr-input-container [contenteditable="true"]')).backdropFilter; })()`);
  console.log(JSON.stringify({ results, restoredEditableBf: after }, null, 2));
} finally {
  session.close();
}
