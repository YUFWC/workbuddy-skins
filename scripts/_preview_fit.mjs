// 壁纸取景预览：临时覆盖 #root::before 的 background-size / background-position，
// 拍几组不同放大倍率的截图，供用户挑选。
// 注意 background-size 用百分比（宽度按容器百分比、高度 auto）可以保持比例并铺满。
import { writeFile } from "node:fs/promises";
import { fetchTargets, CdpSession } from "./lib/cdp.mjs";

const PORT = 9347;
const outDir = process.argv[2] || ".";

// 取景计算：图片 1368x766，角色（含头发）大致在 x=250~950（占 18%~69%）。
// 窗口宽 1591，侧栏 264。要让头发左缘落到容器 x=0：
//   background-size:S% 居中时，图片 f 位置映射到容器 x = -1591(S-100)/200 + 1591*S/100*f
//   代入 f=0.183、x=0 解得 S≈158。
// 所以 150% 以下放大根本没用 —— 空白边缘会等比放大，得放到 160% 以上才够。
const VARIANTS = [
  { key: "A-current", label: "现状 cover", size: "cover", pos: null },
  { key: "D-zoom160", label: "放大 160%", size: "160%", pos: "50% 45%" },
  { key: "E-zoom185", label: "放大 185%", size: "185%", pos: "50% 40%" },
];

const targets = await fetchTargets(PORT);
if (!targets.length) { console.log("no target"); process.exit(1); }
const session = await new CdpSession(targets[0], PORT).open();

const TEMP_ID = "__fit_preview__";
const apply = (size, pos) => `(() => {
  document.getElementById(${JSON.stringify(TEMP_ID)})?.remove();
  const s = document.createElement("style");
  s.id = ${JSON.stringify(TEMP_ID)};
  s.textContent = ${JSON.stringify(
    `:root.workbuddy-ambient-skin #root::before{background-size:${size} !important;` +
    (pos ? `background-position:${pos} !important;` : "") +
    `}`,
  )};
  document.head.appendChild(s);
  const cs = getComputedStyle(document.querySelector("#root"), "::before");
  return cs.backgroundSize + " | " + cs.backgroundPosition;
})()`;

const clear = `(() => { document.getElementById(${JSON.stringify(TEMP_ID)})?.remove(); return "cleared"; })()`;

try {
  for (const v of VARIANTS) {
    const applied = await session.evaluate(apply(v.size, v.pos));
    await new Promise((r) => setTimeout(r, 700));
    const shot = await session.send("Page.captureScreenshot", { format: "png" }, 20000);
    const buf = Buffer.from(shot.data, "base64");
    const path = `${outDir}/fit-${v.key}.png`;
    await writeFile(path, buf);
    console.log(`${v.key.padEnd(12)} ${v.label.padEnd(14)} -> ${applied}  (${buf.length} bytes)`);
  }
} finally {
  await session.evaluate(clear).catch(() => {});
  session.close();
}
