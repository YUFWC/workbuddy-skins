// 同帧 A/B：只切换 .cr-input-toolbar__right 的背景（白胶囊 → 透明）。
import { writeFile } from "node:fs/promises";
import { fetchTargets, CdpSession } from "./lib/cdp.mjs";
import { fileURLToPath } from "node:url";

const PORT = 9347;
const DIR = process.argv[2] || fileURLToPath(new URL("../_诊断截图", import.meta.url));

const targets = await fetchTargets(PORT);
if (!targets.length) { console.log("no target"); process.exit(1); }
const session = await new CdpSession(targets[0], PORT).open();

const shot = async (out) => {
  const r = await session.send("Page.captureScreenshot", { format: "png" }, 30000);
  await writeFile(out, Buffer.from(r.data, "base64"));
};

const OVERRIDE = ':root.workbuddy-ambient-skin .cr-input-toolbar__right{background:transparent !important;background-color:transparent !important}';

const apply = (css) => session.evaluate(`(() => {
  let s = document.getElementById("__expbar");
  if (!s) { s = document.createElement("style"); s.id = "__expbar"; document.head.appendChild(s); }
  s.textContent = ${JSON.stringify(css)};
  const el = document.querySelector(".cr-input-toolbar__right");
  const r = el.getBoundingClientRect();
  return JSON.stringify({ bg: getComputedStyle(el).backgroundColor,
    rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
})()`);

try {
  const before = JSON.parse(await apply(""));
  await shot(`${DIR}/bar_before.png`);
  const after = JSON.parse(await apply(OVERRIDE));
  await shot(`${DIR}/bar_after.png`);
  const restored = await apply("");
  const removed = await session.evaluate(`(() => { const s = document.getElementById("__expbar"); if (s) s.remove();
    return getComputedStyle(document.querySelector(".cr-input-toolbar__right")).backgroundColor; })()`);
  console.log(JSON.stringify({ before, after, restored, removed }, null, 2));
} finally {
  session.close();
}
