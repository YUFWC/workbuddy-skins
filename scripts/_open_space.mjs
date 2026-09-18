// 确定性打开「资料库」面板：不点入口，直接改 grid pane 宽度。
// 主布局是 5 个绝对定位 ._gridViewItem_*，pane2 = detail-panel-container（资料库），
// 折叠时 width:0。把它和 pane1 一起撑开，React 侧就会去加载 iframe。
//   node _open_space.mjs [--port 9347] [--close]
import { fetchTargets, CdpSession } from "./lib/cdp.mjs";

const argv = process.argv.slice(2);
let PORT = 9347;
if (argv.includes("--port")) {
  const i = argv.indexOf("--port");
  PORT = Number(argv[i + 1]);
  argv.splice(i, 2);
}
const closing = argv.includes("--close");

const targets = await fetchTargets(PORT);
if (!targets.length) { console.log(`端口 ${PORT} 没有 CDP 目标`); process.exit(1); }
const page = targets.find((t) => t.type === "page") || targets[0];
const session = await new CdpSession(page, PORT).open();

try {
  const report = await session.evaluate(`(() => {
    const panes = [...document.querySelectorAll('[class*="_gridViewItem"]')];
    const info = panes.map((el, i) => {
      const cs = getComputedStyle(el);
      return { i, cls: el.className, w: el.getBoundingClientRect().width,
               l: el.getBoundingClientRect().left, style: el.getAttribute('style') };
    });
    if (!${closing}) {
      // pane1 = 主内容留 400，pane2 = 资料库撑到剩余
      const total = window.innerWidth;
      for (const p of info) {
        const el = panes[p.i];
        let w = null;
        if (p.i === 1) w = Math.round(total - 400 - 300);
        else if (p.i === 2) w = 300;
        if (w !== null)
          el.setAttribute('style', el.getAttribute('style').replace(/width:\\s*[\\d.]+px/, 'width: ' + w + 'px'));
      }
    }
    const after = panes.map(el => Math.round(el.getBoundingClientRect().width));
    return JSON.stringify({ before: info.map(p => p.w), after,
      hasIframe: !!document.querySelector('iframe.space-panel-iframe, iframe[src*="/space/"]'),
      src: (document.querySelector('iframe.space-panel-iframe, iframe[src*="/space/"]') || {}).src || null });
  })()`);
  console.log(report);
} finally {
  session.close();
}
