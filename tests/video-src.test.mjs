// 视频背景路径规范化（normalizeVideoSrc）的单元测试。
//
// 为什么要单独测：这个函数踩过两个坑，都是"看起来能跑但其实错"的类型——
//   1) 不编码：Chromium 把 `file:///D:/下载.mp4` 解析成 `file:///D:/%E4%B8%8B%E8%BD%BD.mp4`，
//      于是 `videoLayer.src !== videoState.src` 永远为真 → 每次 repaint 都重新 load()
//      → 视频反复从头播。
//   2) 不幂等：已编码的 `%E4` 被二次编码成 `%25E4`，用户点两次「应用」就写坏路径。
// 所以这里同时锁住"编码正确"和"幂等"两件事。
//
// 运行：node tests/video-src.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = readFileSync(path.join(here, "..", "scripts", "lib", "renderer.mjs"), "utf8");

// 从真身里切出这两个函数，避免测试里维护一份复刻副本（复刻会和实现漂移）
const extract = (name) => {
  const start = source.indexOf(`const ${name} = `);
  assert.ok(start >= 0, `renderer.mjs 里找不到 ${name}`);
  // 从起始位置往后按花括号配平，切出完整函数体
  let depth = 0;
  let seen = false;
  for (let i = start; i < source.length; i += 1) {
    if (source[i] === "{") { depth += 1; seen = true; }
    else if (source[i] === "}") {
      depth -= 1;
      if (seen && depth === 0) return source.slice(start, i + 1) + ";";
    }
  }
  throw new Error(`${name} 花括号不配平`);
};

const module = new Function(`
  ${extract("encodeFilePath")}
  ${extract("normalizeVideoSrc")}
  return { encodeFilePath, normalizeVideoSrc };
`)();
const { normalizeVideoSrc } = module;

const cases = [
  // [输入, 期望]
  ["D:/downloads/下载.mp4", "file:///D:/downloads/%E4%B8%8B%E8%BD%BD.mp4", "盘符 + 中文 → 编码"],
  ["D:\\videos\\my clip.mp4", "file:///D:/videos/my%20clip.mp4", "反斜杠 + 空格 → 编码"],
  ["d:/a/b.mp4", "file:///d:/a/b.mp4", "小写盘符保留"],
  ["file:///D:/downloads/下载.mp4", "file:///D:/downloads/%E4%B8%8B%E8%BD%BD.mp4", "已是 file:// 的中文 → 编码"],
  ["file:///D:/downloads/%E4%B8%8B%E8%BD%BD.mp4", "file:///D:/downloads/%E4%B8%8B%E8%BD%BD.mp4", "已编码 → 幂等不变"],
  ["file:///D:/videos/my clip.mp4", "file:///D:/videos/my%20clip.mp4", "file:// + 空格 → 编码"],
  ["https://example.com/中文.mp4", "https://example.com/中文.mp4", "http(s) 原样透传"],
  ["data:video/mp4;base64,AAAA", "data:video/mp4;base64,AAAA", "data: 原样透传"],
  ["blob:file:///abc-def", "blob:file:///abc-def", "blob: 原样透传"],
  ["\\\\server\\share\\视频.mp4", "file://server/share/%E8%A7%86%E9%A2%91.mp4", "UNC 路径"],
  ["", "", "空串"],
  ["   ", "", "纯空白 → 空串"],
];

let failed = 0;
for (const [input, want, label] of cases) {
  let got;
  try { got = normalizeVideoSrc(input); }
  catch (error) { console.log(`  [FAIL] ${label}: 抛异常 ${error.message}`); failed += 1; continue; }
  if (got === want) console.log(`  [OK]   ${label}`);
  else { console.log(`  [FAIL] ${label}\n         输入 ${JSON.stringify(input)}\n         得到 ${JSON.stringify(got)}\n         期望 ${JSON.stringify(want)}`); failed += 1; }
}

// 幂等性：连过两遍必须完全一样（这是"点两次应用写坏路径"的那个 bug）
for (const [input] of cases) {
  const once = normalizeVideoSrc(input);
  const twice = normalizeVideoSrc(once);
  if (once !== twice) {
    console.log(`  [FAIL] 幂等性：${JSON.stringify(input)} → ${JSON.stringify(once)} → ${JSON.stringify(twice)}`);
    failed += 1;
  }
}
if (failed === 0) console.log("  [OK]   幂等性（全部用例连过两遍不变）");

console.log("");
if (failed) { console.log(`${failed} 个用例失败`); process.exit(1); }
console.log("全部通过 ✅ 路径规范化既正确又幂等");
