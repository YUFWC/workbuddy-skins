// 用 CDP 的真实鼠标事件点击某个坐标（有些组件不响应 JS 的 .click()）。
//   node _click.mjs <x> <y> [--port 9348]
//   node _click.mjs --text 资料库 [--port 9348]   ← 先按文字找到侧栏入口再点它的中心
import { fetchTargets, CdpSession } from "./lib/cdp.mjs";

const argv = process.argv.slice(2);
let PORT = 9347;
if (argv.includes("--port")) {
  const i = argv.indexOf("--port");
  PORT = Number(argv[i + 1]);
  argv.splice(i, 2);
}
const targets = await fetchTargets(PORT);
if (!targets.length) { console.log(`端口 ${PORT} 没有 CDP 目标`); process.exit(1); }
const session = await new CdpSession(targets[0], PORT).open();

async function send(method, params) {
  return session.send(method, params);
}

try {
  let x, y;
  if (argv[0] === "--text") {
    const text = argv[1];
    const value = await session.evaluate(`(() => {
      const want = ${JSON.stringify(text)};
      const hits = [...document.querySelectorAll('[data-view-id="sidebar"] *')]
        .filter(el => (el.textContent || '').trim() === want && el.getBoundingClientRect().width > 8);
      if (!hits.length) return null;
      const r = hits[hits.length - 1].getBoundingClientRect();
      return JSON.stringify({ x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) });
    })()`);
    if (!value) { console.log("没找到入口:", text); process.exit(1); }
    ({ x, y } = JSON.parse(value));
    console.log("入口", text, "坐标", x, y);
  } else {
    x = Number(argv[0]); y = Number(argv[1]);
  }

  const base = { x, y, button: "left", clickCount: 1 };
  await send("Input.dispatchMouseEvent", { ...base, type: "mouseMoved" });
  await send("Input.dispatchMouseEvent", { ...base, type: "mousePressed", buttons: 1 });
  await new Promise(r => setTimeout(r, 60));
  await send("Input.dispatchMouseEvent", { ...base, type: "mouseReleased", buttons: 0 });
  console.log("已派发真实鼠标点击");
} finally {
  session.close();
}
