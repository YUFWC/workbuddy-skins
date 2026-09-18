// 纯壁纸参考层对比法 —— 判断「某块发白」到底是【被遮挡】还是【壁纸本身就这样】。
//
// 做法：
//   1. 截一张真实界面
//   2. 临时插一个 position:fixed;inset:0;z-index:2147483000;pointer-events:none 的层，
//      背景属性完全照抄 #root::before 用的那几个变量（图/位置/滤镜）
//   3. 再截一张 → 这就是「理想壁纸」
//   4. 移除临时层（并断言移除成功）
//   5. 在页面里用 OffscreenCanvas 逐像素相减，按 DOM 实测矩形分区出表
//
// 判读：
//   mean 小（<6）且 pct>8 小（<10%）  → 该区域已经是纯壁纸，那里的「灰」是壁纸本身的颜色
//   mean 大 / pct>8 大                 → 确实有东西盖在上面，继续用 _at.mjs 定位
//
// ⚠️ 注意：区域是按 DOM 矩形切的大块，里面**本来就有正文、卡片、图标**，
//    所以绝对值天然偏高。真正的用法是**同一区域改前/改后对比**，
//    或者看 max 与 pct>8 的关系：
//      max 很小(≤15) 但 pct>8 很高  → 一整层很淡的均匀蒙版（最典型的"洗白"）
//      max 很大 但 pct>8 很低       → 只是零星文字/图标，不是蒙版
//
// 用法：
//   node _refdiff.mjs                       # 出分区表
//   node _refdiff.mjs out_ref.png           # 顺便把「理想壁纸」存盘
import { writeFile } from "node:fs/promises";
import { fetchTargets, CdpSession } from "./lib/cdp.mjs";

const PORT = 9347;
const OUT_REF = process.argv[2] || "";

const targets = await fetchTargets(PORT);
if (!targets.length) { console.log(JSON.stringify({ ok: false, error: "CDP 未开启" })); process.exit(1); }

const session = await new CdpSession(targets[0], PORT).open();

// 参考层：变量与 #root::before 保持一致，这样两张图的壁纸是逐像素对齐的。
const REF_ID = "__wbas_refdiff_layer";
const addRef = `(() => {
  let d = document.getElementById(${JSON.stringify(REF_ID)});
  if (!d) { d = document.createElement("div"); d.id = ${JSON.stringify(REF_ID)}; document.body.appendChild(d); }
  d.style.cssText = "position:fixed;inset:0;z-index:2147483000;pointer-events:none;"
    + "background-image:var(--wbas-background-image);"
    + "background-position:var(--wbas-focus-x) var(--wbas-focus-y);"
    + "background-size:cover;background-repeat:no-repeat;"
    + "filter:brightness(var(--wbas-bg-brightness)) saturate(var(--wbas-bg-saturation));";
  return "ref added";
})()`;
const delRef = `(() => {
  const d = document.getElementById(${JSON.stringify(REF_ID)});
  if (d) d.remove();
  return document.getElementById(${JSON.stringify(REF_ID)}) ? "STILL THERE" : "ref removed";
})()`;

const shot = async () => {
  const r = await session.send("Page.captureScreenshot", { format: "png" }, 30000);
  return r.data;
};

try {
  const actualB64 = await shot();

  // DOM 实测矩形（不用猜布局，窗口尺寸/缩放变了也准）
  const rectsRaw = await session.evaluate(`(() => {
    const q = (s) => { const e = document.querySelector(s); if (!e) return null;
      const r = e.getBoundingClientRect();
      return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; };
    return JSON.stringify({
      viewport: [innerWidth, innerHeight],
      menubar: q("#workbuddy-menubar-container"),
      topbar: q(".workbuddy-topbar"),
      sidebar: q('[data-view-id="sidebar"]'),
      main: q('[data-view-id="main-content"]'),
      detail: q('[data-view-id="detail-panel"]'),
      mode: document.documentElement.getAttribute("data-wbas-mode"),
      wallpaperOpacity: getComputedStyle(document.documentElement).getPropertyValue("--wbas-current-opacity").trim(),
      brightness: getComputedStyle(document.documentElement).getPropertyValue("--wbas-bg-brightness").trim(),
    });
  })()`);
  const info = JSON.parse(rectsRaw);

  await session.evaluate(addRef);
  const refB64 = await shot();
  const removed = await session.evaluate(delRef);
  if (removed !== "ref removed") throw new Error("参考层未移除，已中止：" + removed);

  if (OUT_REF) { await writeFile(OUT_REF, Buffer.from(refB64, "base64")); }

  // 在渲染进程里做逐像素相减，免掉 npm 依赖（没有 sharp / pngjs）
  const diffExpr = `(async () => {
    const A = ${JSON.stringify(actualB64)};
    const B = ${JSON.stringify(refB64)};
    const info = ${JSON.stringify(info)};
    const load = (b64) => new Promise((res, rej) => {
      const i = new Image();
      i.onload = () => res(i); i.onerror = () => rej(new Error("decode fail"));
      i.src = "data:image/png;base64," + b64;
    });
    const [ia, ib] = await Promise.all([load(A), load(B)]);
    const w = ia.naturalWidth, h = ia.naturalHeight;
    const grab = (img) => {
      const c = new OffscreenCanvas(w, h);
      const x = c.getContext("2d", { willReadFrequently: true });
      x.drawImage(img, 0, 0);
      return x.getImageData(0, 0, w, h).data;
    };
    const da = grab(ia), db = grab(ib);
    const [vw, vh] = info.viewport;
    const sx = w / vw, sy = h / vh;   // CSS px -> 截图 px

    const region = (x, y, ww, hh) => {
      let sum = 0, n = 0, over = 0, mx = 0;
      const x0 = Math.max(0, Math.round(x * sx)), x1 = Math.min(w, Math.round((x + ww) * sx));
      const y0 = Math.max(0, Math.round(y * sy)), y1 = Math.min(h, Math.round((y + hh) * sy));
      for (let py = y0; py < y1; py++) {
        let o = (py * w + x0) * 4;
        for (let px = x0; px < x1; px++, o += 4) {
          const d0 = Math.abs(da[o] - db[o]), d1 = Math.abs(da[o+1] - db[o+1]), d2 = Math.abs(da[o+2] - db[o+2]);
          const d = Math.max(d0, d1, d2);
          sum += d; n++; if (d > 8) over++; if (d > mx) mx = d;
        }
      }
      return n ? { mean: +(sum / n).toFixed(2), pctOver8: +((over / n) * 100).toFixed(1), max: mx, px: n } : null;
    };

    const out = {};
    const add = (label, r, label2) => {
      if (!r) { out[label] = "not found"; return; }
      const v = region(r[0], r[1], r[2], r[3]);
      out[label2 || label] = v;
    };
    add("menubar", info.menubar);
    add("topbar", info.topbar);
    add("sidebar", info.sidebar);
    add("mainContent", info.main);
    add("detailPanel", info.detail);
    if (info.topbar) {
      const t = info.topbar;
      out["topbar_left"]  = region(t[0], t[1], Math.min(300, t[2]), t[3]);
      out["topbar_right"] = region(t[0] + Math.max(0, t[2] - 300), t[1], Math.min(300, t[2]), t[3]);
    }
    out.__meta = { screenshot: [w, h], viewport: [vw, vh], mode: info.mode,
                   wallpaperOpacity: info.wallpaperOpacity, brightness: info.brightness };
    return JSON.stringify(out);
  })()`;

  const res = await session.evaluate(diffExpr, 60000);
  const table = JSON.parse(res);

  console.log("=== 真实界面 vs 纯壁纸参考层 ===");
  console.log("mode=" + table.__meta.mode
    + "  壁纸不透明度=" + table.__meta.wallpaperOpacity
    + "  亮度=" + table.__meta.brightness
    + "  截图=" + table.__meta.screenshot.join("x")
    + "  视口=" + table.__meta.viewport.join("x"));
  console.log("");
  console.log("区域             mean   pct>8    max    判读");
  for (const [k, v] of Object.entries(table)) {
    if (k === "__meta") continue;
    if (typeof v === "string") { console.log(k.padEnd(16) + "  " + v); continue; }
    const verdict = v.mean < 6 && v.pctOver8 < 10 ? "已是纯壁纸（灰=壁纸本色）"
      : v.mean < 16 ? "轻微覆盖" : "有明显覆盖";
    console.log(k.padEnd(16) + String(v.mean).padStart(6) + String(v.pctOver8).padStart(8)
      + "%" + String(v.max).padStart(6) + "   " + verdict);
  }
  if (OUT_REF) console.log("\n参考层截图已存: " + OUT_REF);
} finally {
  session.close();
}
