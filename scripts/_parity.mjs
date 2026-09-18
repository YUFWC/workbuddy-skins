// 一致性校验：用 Node 原生实现产出「主题表 + CSS 摘要 + 注入表达式拆解」，
// 供 Python 端口逐字段比对。
// 用法：node _parity.mjs <out.json>
import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { HOST_ID, STATE_KEY, STYLE_ID, VERSION, bundledThemesRoot, studioPaths } from "./lib/constants.mjs";
import { buildInstallExpression } from "./lib/renderer.mjs";
import { listThemes } from "./lib/theme.mjs";

const out = process.argv[2];
if (!out) { console.log("usage: node _parity.mjs <out.json>"); process.exit(1); }

const paths = studioPaths();
const themes = await listThemes([bundledThemesRoot, paths.userThemesRoot]);
const entries = themes.map(({ manifest, imageDataUrl, artKey }) => ({ ...manifest, imageDataUrl, artKey }));
const css = await readFile(join(bundledThemesRoot, "..", "ambient.css"), "utf8");

// 官方注入表达式
const expression = buildInstallExpression({ css, themes: entries, activeId: "" });

// 用与 Python 完全相同的规则重建一次，验证「首个 marker → EOF」这条切片规则成立
const marker = "async function installInRenderer(data) {";
const rendererSource = await readFile(join(dirname(fileURLToPath(import.meta.url)), "lib", "renderer.mjs"), "utf8");
const index = rendererSource.indexOf(marker);
const fnSource = rendererSource.slice(index).replace(/\s+$/, "");
const payloadText = JSON.stringify({
  css, themes: entries, activeId: "", force: true, STYLE_ID, HOST_ID, STATE_KEY, VERSION,
});
const rebuilt = "(" + fnSource + ")(" + payloadText + ")";

await writeFile(out, JSON.stringify({
  userThemesRoot: paths.userThemesRoot,
  cssSha256: createHash("sha256").update(css).digest("hex"),
  cssLength: css.length,
  count: entries.length,
  themes: entries,
  fnSourceSha256: createHash("sha256").update(fnSource, "utf8").digest("hex"),
  fnSourceLength: fnSource.length,
  payload: JSON.parse(payloadText),
  rebuiltMatchesOfficial: rebuilt === expression,
  expressionLength: expression.length,
  expressionSha256: createHash("sha256").update(expression, "utf8").digest("hex"),
}, null, 1), "utf8");

// 把表达式原文单独落盘，供 Python 做逐字符 diff
await writeFile(out.replace(/\.json$/, "") + ".expr.js", expression, "utf8");

console.log("ok themes=" + entries.length + " css=" + css.length
  + " fn=" + fnSource.length + " sliceRuleOK=" + (rebuilt === expression));
