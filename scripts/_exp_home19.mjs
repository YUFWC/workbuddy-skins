// 新建任务页：同帧 A/B。先拍"已修复"，再临时把白底还原回来拍"修复前"。
import { writeFile } from "node:fs/promises";
import { fetchTargets, CdpSession } from "./lib/cdp.mjs";
import { fileURLToPath } from "node:url";

const PORT = 9347;
const DIR = process.argv[2] || fileURLToPath(new URL("../_诊断截图", import.meta.url));
const R = ":root.workbuddy-ambient-skin";

// 把规则 19 压掉的几层白底还原回去（用同等特异性，才能压过补丁的 !important）
const RESTORE = `
${R} [class~="wb-home-route"]{background:rgb(255,255,255) !important;background-image:none !important}
${R} .cr-input-box__main{background-image:linear-gradient(rgb(235,235,235) 0%,rgb(245,245,245) 100%) !important}
${R} .wb-scene-tabs{background:rgb(235,235,235) !important}
${R} .quick-actions__item,${R} .quick-actions__arrow{background:rgb(255,255,255) !important;backdrop-filter:none !important;-webkit-backdrop-filter:none !important}
${R} .quick-actions::after{background-image:linear-gradient(270deg,rgb(255,255,255) 50%,rgba(255,255,255,0) 100%) !important}
`;

const targets = await fetchTargets(PORT);
if (!targets.length) { console.log("no target"); process.exit(1); }
const session = await new CdpSession(targets[0], PORT).open();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const shot = async (out) => {
  const r = await session.send("Page.captureScreenshot", { format: "png" }, 30000);
  await writeFile(out, Buffer.from(r.data, "base64"));
};
const apply = (css) => session.evaluate(`(() => {
  let s = document.getElementById("__exp19");
  if (!s) { s = document.createElement("style"); s.id = "__exp19"; document.head.appendChild(s); }
  s.textContent = ${JSON.stringify(css)};
  return JSON.stringify({ home: getComputedStyle(document.querySelector("main.wb-home-route")).backgroundColor });
})()`);

try {
  const onHome = await session.evaluate(`!!document.querySelector("main.wb-home-route")`);
  if (!onHome) {
    await session.evaluate(`(() => { const b = [...document.querySelectorAll("button")].find(x => (x.textContent || "").trim() === "新建任务"); if (b) b.click(); return 1; })()`);
    await sleep(2200);
  }
  await apply("");
  await sleep(200);
  await shot(`${DIR}/home19_after.png`);
  const before = JSON.parse(await apply(RESTORE));
  await sleep(250);
  await shot(`${DIR}/home19_before.png`);
  await apply("");
  const removed = await session.evaluate(`(() => { const s = document.getElementById("__exp19"); if (s) s.remove();
    return getComputedStyle(document.querySelector("main.wb-home-route")).backgroundColor; })()`);
  console.log(JSON.stringify({ before, afterRestore: removed }, null, 2));
} finally {
  session.close();
}
