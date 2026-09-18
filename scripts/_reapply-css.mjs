// 热更新已注入的 CSS：只替换 <style id="workbuddy-ambient-skin-style"> 的内容，
// 不重新 install，因此不会重置当前主题 / 自定义壁纸 / ◐ 菜单里的调参。
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { fetchTargets, CdpSession } from "./lib/cdp.mjs";
import { assetsRoot, STYLE_ID } from "./lib/constants.mjs";

const PORT = 9347;

const css = await readFile(join(assetsRoot, "ambient.css"), "utf8");
const targets = await fetchTargets(PORT);
if (!targets.length) {
  console.log(JSON.stringify({ ok: false, error: "CDP 未开启，WorkBuddy 可能是普通启动的" }));
  process.exit(1);
}

const expr = `(() => {
  const el = document.getElementById(${JSON.stringify(STYLE_ID)});
  if (!el) return JSON.stringify({ ok: false, error: "style 元素不存在，说明皮肤尚未注入" });
  el.textContent = ${JSON.stringify(css)};
  return JSON.stringify({ ok: true, bytes: el.textContent.length });
})()`;

const results = [];
for (const target of targets) {
  const session = await new CdpSession(target, PORT).open();
  try {
    results.push(await session.evaluate(expr));
  } catch (error) {
    results.push(JSON.stringify({ ok: false, error: error.message }));
  } finally {
    session.close();
  }
}
console.log(JSON.stringify({ ok: true, cssBytes: css.length, targets: targets.length, results }, null, 2));
