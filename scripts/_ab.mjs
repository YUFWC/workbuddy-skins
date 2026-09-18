// A/B 对比取证：在同一屏、同一壁纸下，分别截"关闭玻璃补丁"和"开启玻璃补丁"两张图。
// 做法：临时插入一个覆盖用 <style>，把规则 6–9 改回上游的实心样式，截一张；
// 移除后再截一张。这样对比的是同一画面，不受路由/滚动位置影响。
import { writeFile } from "node:fs/promises";
import { fetchTargets, CdpSession } from "./lib/cdp.mjs";

const PORT = 9347;
const [outBefore, outAfter] = process.argv.slice(2);
if (!outBefore || !outAfter) {
  console.log("usage: node _ab.mjs <before.png> <after.png>");
  process.exit(1);
}

const targets = await fetchTargets(PORT);
if (!targets.length) { console.log("no target"); process.exit(1); }
const session = await new CdpSession(targets[0], PORT).open();

const TEMP_ID = "__ab_override__";
// ⚠️ 选择器必须与原补丁**同等或更高特异性**，否则 !important 也会输。
// 原补丁里顶栏/侧栏是 :root.workbuddy-ambient-skin[data-wbas-material="studio"] .xxx
// （特异性 0,4,0），只写 .xxx（0,1,0）会被静默压过，截图就"看起来没差别"。
// 这里逐个镜像原选择器，并依赖"后插入的 <style> 在文档顺序上更靠后"来取胜。
const SEL = ':root.workbuddy-ambient-skin';
const rules = [
  `${SEL}[data-wbas-material="studio"] .workbuddy-topbar{background:var(--wbas-surface)!important;backdrop-filter:none!important;-webkit-backdrop-filter:none!important;border-bottom:1px solid color-mix(in srgb, var(--wbas-text) 8%, transparent)!important}`,
  `${SEL} #workbuddy-menubar-container{background:rgb(242,242,242)!important;box-shadow:none!important}`,
  `${SEL} .cr-self-bubble,${SEL} .cr-code-like-box,${SEL} .cr-code-like-box__header,${SEL} .cr-tool-exp__content,${SEL} .artifact-slot-panel__card{background-color:var(--wbas-surface)!important;backdrop-filter:none!important;-webkit-backdrop-filter:none!important}`,
  `${SEL} .cr-input-container{background:var(--wbas-surface)!important;backdrop-filter:none!important;-webkit-backdrop-filter:none!important}`,
  `${SEL}[data-wbas-material="studio"] [data-view-id="sidebar"]{background:var(--wbas-surface)!important}`,
].join("\n");

const setOverride = `(() => {
  const id = ${JSON.stringify(TEMP_ID)};
  document.getElementById(id)?.remove();
  const s = document.createElement("style");
  s.id = id;
  s.textContent = ${JSON.stringify(rules)};
  document.head.appendChild(s);
  return "override on";
})()`;

const clearOverride = `(() => { document.getElementById(${JSON.stringify(TEMP_ID)})?.remove(); return "override off"; })()`;

// 自检：确认覆盖真的生效了，否则截图会"看起来没差别"却查不出原因
const probe = `(() => {
  const bg = (sel) => { const el = document.querySelector(sel); return el ? getComputedStyle(el).backgroundColor : "NOT FOUND"; };
  const bd = (sel) => { const el = document.querySelector(sel); return el ? getComputedStyle(el).backdropFilter : "NOT FOUND"; };
  return JSON.stringify({
    topbar: bg(".workbuddy-topbar"), topbarBlur: bd(".workbuddy-topbar"),
    menubar: bg("#workbuddy-menubar-container"),
    card: bg(".cr-self-bubble"), input: bg(".cr-input-container"),
  });
})()`;

const shoot = async (path) => {
  const r = await session.send("Page.captureScreenshot", { format: "png" }, 20000);
  const buf = Buffer.from(r.data, "base64");
  await writeFile(path, buf);
  return buf.length;
};

try {
  console.log(await session.evaluate(setOverride));
  console.log("probe(before):", await session.evaluate(probe));
  await new Promise((res) => setTimeout(res, 600));
  console.log("before:", outBefore, await shoot(outBefore), "bytes");

  console.log(await session.evaluate(clearOverride));
  console.log("probe(after) :", await session.evaluate(probe));
  await new Promise((res) => setTimeout(res, 600));
  console.log("after :", outAfter, await shoot(outAfter), "bytes");
} finally {
  await session.evaluate(clearOverride).catch(() => {});
  session.close();
}
