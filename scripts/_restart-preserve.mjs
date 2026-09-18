// 重启 WorkBuddy 并重新注入皮肤，但【保留用户当前的壁纸和 ◐ 调参】。
//
// 为什么需要它：CLI 的 `terminal-apply --theme <id>` 要求 <id> 是**磁盘上**的主题目录，
// 而用户在 ◐ 菜单里上传的自定义壁纸只存在渲染进程的 localStorage
// （键 workbuddy-ambient-skin.custom-v2，id 形如 user-xxxx），磁盘上根本没有；
// 传内置主题 id 又会把用户的图覆盖成内置壁纸。
//
// 做法：重启前先把 localStorage 里所有 workbuddy-ambient-skin.* 的键快照下来，
// 重启后原样写回（只补缺失的，不覆盖已有的），再以 activeId="" 注入 ——
// 渲染端 buildInstallExpression 里是
//   data.themes.find(t => t.id === (data.activeId || stored))
// activeId 为假值时会回落到 localStorage["workbuddy-ambient-skin.active"]，
// 也就是用户自己那张图。

import { DEFAULT_PORT, bundledThemesRoot, studioPaths } from "./lib/constants.mjs";
import { fetchTargets, waitForTargets } from "./lib/cdp.mjs";
import { applyToRenderer, rendererStatus } from "./lib/injector.mjs";
import { listThemes } from "./lib/theme.mjs";
import { forceQuitWorkBuddy, launchWithCdp } from "./lib/workbuddy.mjs";

const PREFIX = "workbuddy-ambient-skin.";
const paths = studioPaths();

const snapshotExpr = `(() => {
  const out = {};
  for (let i = 0; i < localStorage.length; i += 1) {
    const k = localStorage.key(i);
    if (k && k.indexOf(${JSON.stringify(PREFIX)}) === 0) out[k] = localStorage.getItem(k);
  }
  return JSON.stringify(out);
})()`;

const restoreExpr = (snapshotJson) => `(() => {
  const snap = ${JSON.stringify(snapshotJson)};
  let added = 0;
  for (const k of Object.keys(snap)) {
    if (localStorage.getItem(k) === null) { localStorage.setItem(k, snap[k]); added += 1; }
  }
  return added;
})()`;

async function withTarget(fn) {
  const targets = await fetchTargets(DEFAULT_PORT);
  if (!targets.length) return null;
  const { CdpSession } = await import("./lib/cdp.mjs");
  const session = await new CdpSession(targets[0], DEFAULT_PORT).open();
  try { return await fn(session); } finally { session.close(); }
}

const log = (step, detail) => console.log(`${step} ${detail}`);

// ---- 1) 重启前：快照 localStorage ----------------------------------------
let snapshot = "{}";
let activeBefore = null;
if ((await fetchTargets(DEFAULT_PORT)).length) {
  snapshot = (await withTarget((s) => s.evaluate(snapshotExpr))) ?? "{}";
  try { activeBefore = JSON.parse(snapshot)[`${PREFIX}active`] ?? null; } catch {}
  log("[1/5] 已快照 localStorage：", `${Object.keys(JSON.parse(snapshot)).length} 个键，当前主题 ${activeBefore ?? "未知"}`);
} else {
  log("[1/5] 未检测到 CDP（WorkBuddy 不是带调试端口启动的）→ 跳过快照，重启后将依赖已有 localStorage");
}

// ---- 2) 关闭 WorkBuddy ---------------------------------------------------
log("[2/5] 正在关闭 WorkBuddy ...");
const shutdown = await forceQuitWorkBuddy();
log("      ", JSON.stringify(shutdown));

// ---- 3) 带 CDP 重新启动 --------------------------------------------------
log("[3/5] 正在以 --remote-debugging-port=" + DEFAULT_PORT + " 重新启动 ...");
const launch = await launchWithCdp(DEFAULT_PORT);
log("      ", JSON.stringify(launch));

await waitForTargets(DEFAULT_PORT);
log("[4/5] 渲染进程已就绪，正在回填 localStorage ...");
const added = await withTarget((s) => s.evaluate(restoreExpr(snapshot)));
log("      ", `补写 ${added ?? 0} 个键`);

// ---- 4) 注入（activeId 留空 = 用 localStorage 里记录的那张图）------------
const themes = await listThemes([bundledThemesRoot, paths.userThemesRoot]);
const applied = await applyToRenderer({ port: DEFAULT_PORT, themes, activeId: "" });
const renderers = await rendererStatus(DEFAULT_PORT);
const themeNow = renderers[0]?.themeId ?? null;

log("[5/5] 注入完成。", `applied=${applied.applied}`);

console.log(JSON.stringify({
  ok: renderers.length > 0 && renderers.every((r) => r.pass),
  activeBefore,
  activeAfter: themeNow,
  preserved: Boolean(activeBefore) && themeNow === activeBefore,
  applied: applied.applied,
  renderers: renderers.map((r) => ({ installed: r.installed, pass: r.pass, themeId: r.themeId, mode: r.mode })),
}, null, 2));
