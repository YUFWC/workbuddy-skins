import assert from "node:assert/strict";
import { mkdir, mkdtemp, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { createTheme, deleteUserTheme, listThemes, loadTheme, renameUserTheme, validateTheme } from "../scripts/lib/theme.mjs";

test("validates a gradient theme", () => {
  const theme = validateTheme({ schemaVersion: 1, id: "quiet-test", name: "Quiet", background: "linear-gradient(#000, #222)", appearance: "dark" });
  assert.equal(theme.id, "quiet-test");
  assert.equal(theme.art.safeArea, "auto");
  assert.equal(theme.modes.workOpacity, 1);
  assert.equal(theme.modes.detailOpacity, 1);
  assert.equal(theme.material.style, "ambient");
  assert.equal(theme.material.blur, 18);
});

test("validates bounded studio material settings", () => {
  const theme = validateTheme({ schemaVersion: 1, id: "studio", name: "Studio", material: { style: "studio", panelOpacity: 0.84, blur: 28, radius: 20 } });
  assert.equal(theme.material.style, "studio");
  assert.equal(theme.material.panelOpacity, 0.84);
  assert.equal(theme.material.blur, 28);
  assert.throws(() => validateTheme({ schemaVersion: 1, id: "bad-material", name: "Bad", material: { style: "maximal" } }), /material.style/);
  assert.throws(() => validateTheme({ schemaVersion: 1, id: "bad-blur", name: "Bad", material: { blur: 80 } }), /between 0 and 40/);
});

test("validates the optional theme card description", () => {
  const theme = validateTheme({ schemaVersion: 1, id: "card", name: "Card", description: "A quiet workspace." });
  assert.equal(theme.description, "A quiet workspace.");
  assert.throws(() => validateTheme({ schemaVersion: 1, id: "long-card", name: "Long", description: "x".repeat(121) }), /up to 120/);
});

test("rejects unsafe ids and image traversal", () => {
  assert.throws(() => validateTheme({ schemaVersion: 1, id: "../bad", name: "Bad" }), /theme id/);
  assert.throws(() => validateTheme({ schemaVersion: 1, id: "bad", name: "Bad", image: "../x.png" }), /relative path/);
});

test("rejects malformed palette and unsafe background", () => {
  assert.throws(() => validateTheme({ schemaVersion: 1, id: "bad", name: "Bad", palette: { accent: "red" } }), /six-digit/);
  assert.throws(() => validateTheme({ schemaVersion: 1, id: "bad", name: "Bad", background: "url(x); color:red" }), /safe CSS/);
});

test("validates appearance-aware light and dark palettes", () => {
  const theme = validateTheme({
    schemaVersion: 1, id: "dual", name: "Dual",
    palette: {
      light: { accent: "#5676c8", secondary: "#7f65b8", surface: "#f7f8fb", text: "#20242c" },
      dark: { accent: "#8eafff", secondary: "#b49af2", surface: "#11151f", text: "#f2f5fa" },
    },
  });
  assert.equal(theme.palette.light.surface, "#F7F8FB");
  assert.equal(theme.palette.dark.text, "#F2F5FA");
  assert.throws(() => validateTheme({
    schemaVersion: 1, id: "partial-dual", name: "Partial",
    palette: { light: { accent: "#5676c8" } },
  }), /palette.dark/);
});

test("loads and lists a theme", async () => {
  const root = await mkdtemp(join(tmpdir(), "wbas-theme-"));
  const folder = join(root, "one"); await mkdir(folder);
  await writeFile(join(folder, "theme.json"), JSON.stringify({ schemaVersion: 1, id: "one", name: "One", background: "linear-gradient(#000,#111)" }));
  assert.equal((await loadTheme(folder)).manifest.id, "one");
  assert.equal((await listThemes([root])).length, 1);
});

test("loads bundled themes", async () => {
  const root = fileURLToPath(new URL("../assets/themes", import.meta.url));
  const themes = await listThemes([root]);
  assert.deepEqual(themes.map((theme) => theme.manifest.id).sort(), ["doraemon-snow-fortune", "genshin-raiden-shogun", "miku-neko-maid", "paper-aurora"]);
  const miku = themes.find((theme) => theme.manifest.id === "miku-neko-maid");
  assert.equal(miku.manifest.palette, "auto");
  assert.equal(miku.manifest.modes.workOpacity, 0.98);
  assert.equal(miku.manifest.modes.detailOpacity, 0.98);
  assert.ok(miku.imageDataUrl.startsWith("data:image/webp;base64,"));
  const raiden = themes.find((theme) => theme.manifest.id === "genshin-raiden-shogun");
  assert.equal(raiden.manifest.name, "雷神 · 雷寂永恒");
  assert.equal(raiden.manifest.appearance, "light");
  assert.deepEqual(raiden.manifest.palette, {
    accent: "#7047C8", secondary: "#A67BE8", surface: "#F5F1FC", text: "#29203B",
  });
  assert.equal(raiden.manifest.modes.workOpacity, 0.96);
  assert.equal(raiden.manifest.modes.detailOpacity, 0.96);
  assert.ok(raiden.imageDataUrl.startsWith("data:image/webp;base64,"));
});

test("creates a custom image theme atomically", async () => {
  const root = await mkdtemp(join(tmpdir(), "wbas-create-"));
  const image = join(root, "source.png");
  const pngHeader = Buffer.alloc(24);
  Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(pngHeader, 0);
  pngHeader.writeUInt32BE(13, 8); Buffer.from("IHDR").copy(pngHeader, 12);
  pngHeader.writeUInt32BE(16, 16); pngHeader.writeUInt32BE(16, 20);
  await writeFile(image, pngHeader);
  const created = await createTheme({ imagePath: image, name: "My Image", storeRoot: join(root, "themes") });
  assert.match(created.manifest.id, /^my-image-/);
  assert.equal(created.manifest.palette, "auto");
  assert.equal(created.manifest.material.style, "studio");
  assert.ok(created.imageDataUrl.startsWith("data:image/png;base64,"));
});

test("rejects unsupported or oversized image dimensions", async () => {
  const root = await mkdtemp(join(tmpdir(), "wbas-large-"));
  const image = join(root, "large.png");
  const header = Buffer.alloc(24); Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(header);
  header.writeUInt32BE(13, 8); Buffer.from("IHDR").copy(header, 12);
  header.writeUInt32BE(20000, 16); header.writeUInt32BE(20000, 20); await writeFile(image, header);
  await assert.rejects(() => createTheme({ imagePath: image, name: "Large", storeRoot: join(root, "themes") }), /dimensions/);
});

test("renames and recoverably deletes only a stored user theme", async () => {
  const root = await mkdtemp(join(tmpdir(), "wbas-manage-"));
  const image = join(root, "source.png");
  const header = Buffer.alloc(24); Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(header);
  header.writeUInt32BE(13, 8); Buffer.from("IHDR").copy(header, 12);
  header.writeUInt32BE(16, 16); header.writeUInt32BE(16, 20); await writeFile(image, header);
  const storeRoot = join(root, "themes");
  const created = await createTheme({ imagePath: image, name: "Before", storeRoot });
  const renamed = await renameUserTheme({ id: created.manifest.id, name: "After", storeRoot });
  assert.equal(renamed.manifest.name, "After");
  const deleted = await deleteUserTheme({ id: created.manifest.id, storeRoot, deletedRoot: join(root, "deleted") });
  assert.equal(deleted.deleted, true);
  assert.equal((await listThemes([storeRoot])).length, 0);
  assert.equal((await stat(deleted.recoverableFrom)).isDirectory(), true);
});
