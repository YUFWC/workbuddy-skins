// 预览「壁纸不透明度」滑块的影响：把三个 *-opacity 变量临时拉到 1（100%），
// 截图后原样还原。（applyTuning 是往 <html> 写内联样式，所以这里也写内联即可覆盖。）
import { writeFile } from "node:fs/promises";
import { fetchTargets, CdpSession } from "./lib/cdp.mjs";

const PORT = 9347;
const [outBefore, outAfter] = process.argv.slice(2);
if (!outBefore || !outAfter) { console.log("usage: node _preview_opacity.mjs <before.png> <after.png>"); process.exit(1); }

const VARS = ["--wbas-home-opacity", "--wbas-work-opacity", "--wbas-detail-opacity"];

const targets = await fetchTargets(PORT);
if (!targets.length) { console.log("no target"); process.exit(1); }
const session = await new CdpSession(targets[0], PORT).open();

const readVars = `(() => { const s = document.documentElement.style; return JSON.stringify(Object.fromEntries(${JSON.stringify(VARS)}.map((v) => [v, s.getPropertyValue(v)]))); })()`;
const bump = `(() => { const s = document.documentElement.style; ${JSON.stringify(VARS)}.forEach((v) => s.setProperty(v, "1")); return getComputedStyle(document.documentElement).getPropertyValue("--wbas-current-opacity").trim(); })()`;
const restore = (snapshotJson) => `(() => { const s = document.documentElement.style; const snap = ${JSON.stringify(snapshotJson)}; for (const k of Object.keys(snap)) { if (snap[k]) s.setProperty(k, snap[k]); } return getComputedStyle(document.documentElement).getPropertyValue("--wbas-current-opacity").trim(); })()`;

const shoot = async (path) => {
  const r = await session.send("Page.captureScreenshot", { format: "png" }, 20000);
  const buf = Buffer.from(r.data, "base64");
  await writeFile(path, buf);
  return buf.length;
};

// ⚠️ 必须 JSON.parse：readVars 表达式返回的是 JSON **字符串**，
// 直接把它丢给 Object.keys 会遍历字符索引（"0","1",...），
// 结果是设了一堆垃圾属性、真正的变量没还原，壁纸会卡在 100%。
const snapshot = JSON.parse(await session.evaluate(readVars));
try {
  console.log("原始值:", snapshot);
  await new Promise((r) => setTimeout(r, 400));
  console.log("before:", outBefore, await shoot(outBefore), "bytes");

  const now = await session.evaluate(bump);
  console.log("拉到 100% 后 --wbas-current-opacity =", now);
  await new Promise((r) => setTimeout(r, 700));
  console.log("after :", outAfter, await shoot(outAfter), "bytes");
} finally {
  const back = await session.evaluate(restore(snapshot)).catch(() => "restore failed");
  console.log("已还原 --wbas-current-opacity =", back);
  session.close();
}
