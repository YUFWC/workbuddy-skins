// 用合成鼠标事件触发 :hover，找出 hover 底色到底画在哪个元素上。
// （getComputedStyle 不会应用 :hover，所以静态扫描永远看不到这类样式。）
import { fetchTargets, CdpSession } from "./lib/cdp.mjs";

const PORT = 9347;
const targets = await fetchTargets(PORT);
if (!targets.length) { console.log("no target"); process.exit(1); }
const session = await new CdpSession(targets[0], PORT).open();

// 不只看 background-color：hover 底色也可能来自 background-image 渐变、
// box-shadow、border 或 outline。这些都要一起查，否则永远找不到"那块圆角底"。
const hoverScan = `(() => {
  const probe = document.createElement("canvas"); probe.width = probe.height = 1;
  const ctx = probe.getContext("2d");
  const alphaOf = (c) => { if (!c || c === "transparent" || c === "none") return 0;
    ctx.clearRect(0,0,1,1); ctx.fillStyle = "#000"; ctx.fillStyle = c; ctx.fillRect(0,0,1,1);
    return ctx.getImageData(0,0,1,1).data[3] / 255; };
  const sb = document.querySelector('[data-view-id="sidebar"]');
  const out = [];
  const consider = (el, tag) => {
    const s = getComputedStyle(el);
    if (s.display === "none" || s.visibility === "hidden") return;
    const reasons = [];
    const a = alphaOf(s.backgroundColor);
    if (a > 0.02) reasons.push("bg-color " + s.backgroundColor);
    if (s.backgroundImage && s.backgroundImage !== "none") reasons.push("bg-image " + s.backgroundImage.slice(0, 46));
    if (s.boxShadow && s.boxShadow !== "none") reasons.push("shadow " + s.boxShadow.slice(0, 46));
    if (parseFloat(s.borderTopWidth) + parseFloat(s.borderBottomWidth) > 0) reasons.push("border " + s.borderTopWidth + " " + s.borderTopColor);
    if (parseFloat(s.outlineWidth) > 0) reasons.push("outline " + s.outlineWidth + " " + s.outlineColor);
    if (!reasons.length) return;
    const r = el.getBoundingClientRect();
    if (r.width < 6 || r.height < 6) return;
    const cls = (typeof el.className === "string" ? el.className : "").trim();
    out.push({ which: tag, cls: cls.slice(0, 58) || el.tagName.toLowerCase(),
      rect: Math.round(r.left) + "," + Math.round(r.top) + " " + Math.round(r.width) + "x" + Math.round(r.height),
      why: reasons });
  };
  for (const el of sb.querySelectorAll("*")) {
    consider(el, "el");
    // 伪元素要单独用 getComputedStyle(el, "::before") 取，不能复用上面的 consider
    const b = getComputedStyle(el, "::before");
    if (b.content !== "none") {
      const ba = alphaOf(b.backgroundColor);
      if (ba > 0.02 || (b.backgroundImage && b.backgroundImage !== "none") || (b.boxShadow && b.boxShadow !== "none")) {
        const r = el.getBoundingClientRect();
        const cls = (typeof el.className === "string" ? el.className : "").trim();
        out.push({ which: "::before", cls: cls.slice(0, 58) || el.tagName.toLowerCase(),
          rect: Math.round(r.left) + "," + Math.round(r.top) + " " + Math.round(r.width) + "x" + Math.round(r.height),
          why: ["pseudo bg " + b.backgroundColor + " / " + b.backgroundImage.slice(0, 40)] });
      }
    }
  }
  return JSON.stringify(out, null, 2);
})()`;

const targets2 = `(() => {
  const btns = [...document.querySelectorAll('[data-view-id="sidebar"] .conversation-list-tab-button')];
  return JSON.stringify(btns.map((el) => { const r = el.getBoundingClientRect();
    return { text: (el.textContent || "").trim().slice(0, 10), x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }; }));
})()`;

const move = (x, y) => session.send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y, button: "none", buttons: 0 }, 8000);

try {
  const buttons = JSON.parse(await session.evaluate(targets2));
  console.log("导航项:", buttons);

  // 先把鼠标挪到空白处，作为对照
  await move(660, 800);
  await new Promise((r) => setTimeout(r, 300));
  console.log("\n--- 鼠标在空白处 ---");
  console.log(await session.evaluate(hoverScan));

  for (const b of buttons.filter((x) => ["自动化", "更多"].includes(x.text))) {
    await move(b.x, b.y);
    await new Promise((r) => setTimeout(r, 400));
    console.log(`\n--- 鼠标悬停在「${b.text}」(${b.x},${b.y}) ---`);
    console.log(await session.evaluate(hoverScan));
  }
} finally {
  await move(660, 800).catch(() => {});
  session.close();
}
