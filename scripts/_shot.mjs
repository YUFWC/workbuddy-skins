import { writeFile } from "node:fs/promises";
import { fetchTargets, CdpSession } from "./lib/cdp.mjs";
import { fileURLToPath } from "node:url";

const PORT = 9347;
const OUT = process.argv[2] || fileURLToPath(new URL("../now.png", import.meta.url));

const targets = await fetchTargets(PORT);
if (!targets.length) { console.log("no target"); process.exit(1); }

const session = await new CdpSession(targets[0], PORT).open();
try {
  const r = await session.send("Page.captureScreenshot", { format: "png" }, 20000);
  const buf = Buffer.from(r.data, "base64");
  await writeFile(OUT, buf);
  console.log("saved:", OUT, buf.length, "bytes");
} finally {
  session.close();
}
