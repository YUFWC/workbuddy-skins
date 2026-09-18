// 通用求值工具：把 JS 表达式丢进 WorkBuddy 渲染进程执行。
//   node _eval.mjs "<js 表达式>"
//   node _eval.mjs --port 9348 "<js 表达式>"
//
// ⚠️ 默认端口是 9347（国际版）。两版共存时国内版在 9348/9349，
// 忘了传 --port 就会测错应用 —— 这个坑真踩过（拿国际版的结果去解释国内版的现象）。
import { fetchTargets, CdpSession } from "./lib/cdp.mjs";

const argv = process.argv.slice(2);
let PORT = 9347;
if (argv[0] === "--port") {
  PORT = Number(argv[1]);
  argv.splice(0, 2);
}
const expr = argv[0];
if (!expr) { console.log('usage: node _eval.mjs [--port N] "<js expression>"'); process.exit(1); }

const targets = await fetchTargets(PORT);
if (!targets.length) { console.log(JSON.stringify({ ok: false, error: `端口 ${PORT} 上没有 CDP 目标` })); process.exit(1); }

console.log(`// 端口 ${PORT}  ${targets[0].url ? String(targets[0].url).slice(0, 88) : ""}`);
const session = await new CdpSession(targets[0], PORT).open();
try {
  const value = await session.evaluate(`(() => { return (${expr}); })()`);
  console.log(typeof value === "string" ? value : JSON.stringify(value, null, 2));
} finally {
  session.close();
}
