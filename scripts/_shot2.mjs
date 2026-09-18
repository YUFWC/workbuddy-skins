// 截图工具（带端口）：
//   node _shot2.mjs <输出路径> [--port 9348]
// ⚠️ 原来的 _shot.mjs 端口写死 9347（国际版），两版共存时会截错应用。
import { writeFile } from "node:fs/promises";
import { fetchTargets, CdpSession } from "./lib/cdp.mjs";

const argv = process.argv.slice(2);
let PORT = 9347;
if (argv.includes("--port")) {
  const i = argv.indexOf("--port");
  PORT = Number(argv[i + 1]);
  argv.splice(i, 2);
}
const OUT = argv[0] || "now.png";

const targets = await fetchTargets(PORT);
if (!targets.length) { console.log("no target on port " + PORT); process.exit(1); }

const session = await new CdpSession(targets[0], PORT).open();
try {
  const r = await session.send("Page.captureScreenshot", { format: "png" }, 20000);
  const buf = Buffer.from(r.data, "base64");
  await writeFile(OUT, buf);
  console.log("saved:", OUT, buf.length, "bytes");
} finally {
  session.close();
}
