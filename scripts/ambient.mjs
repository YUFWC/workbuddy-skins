#!/usr/bin/env node
import { access, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { bundledThemesRoot, DEFAULT_PORT, studioPaths, VERSION } from "./lib/constants.mjs";
import { fetchTargets, waitForTargets } from "./lib/cdp.mjs";
import { applyToRenderer, removeFromRenderer, rendererStatus } from "./lib/injector.mjs";
import { createTheme, deleteUserTheme, listThemes, renameUserTheme } from "./lib/theme.mjs";
import { forceQuitWorkBuddy, inspectWorkBuddy, launchNormally, launchWithCdp } from "./lib/workbuddy.mjs";

const paths = studioPaths();

function parse(argv) {
  const command = argv[0] || "help";
  const options = {};
  for (let index = 1; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || !value || value.startsWith("--")) throw new Error(`invalid option near ${key || "end"}`);
    options[key.slice(2)] = value;
  }
  return { command, options };
}

async function writeJson(path, value) {
  await mkdir(paths.stateRoot, { recursive: true, mode: 0o700 });
  const temporary = `${path}.${process.pid}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  await rename(temporary, path);
}

async function readJson(path) {
  try { return JSON.parse(await readFile(path, "utf8")); }
  catch (error) { if (error.code === "ENOENT") return null; throw error; }
}

async function themes() {
  return listThemes([bundledThemesRoot, paths.userThemesRoot]);
}

async function selectedTheme(id) {
  const allThemes = await themes();
  const activeId = id || "paper-aurora";
  if (!allThemes.some(({ manifest }) => manifest.id === activeId)) throw new Error(`theme not found: ${activeId}`);
  return { allThemes, activeId };
}

async function inject(themeId) {
  const { allThemes, activeId } = await selectedTheme(themeId);
  await waitForTargets(DEFAULT_PORT);
  const applied = await applyToRenderer({ port: DEFAULT_PORT, themes: allThemes, activeId });
  const renderers = await rendererStatus(DEFAULT_PORT);
  const verified = renderers.length > 0 && renderers.every((renderer) => renderer.pass && renderer.themeId === activeId);
  if (!verified) throw new Error(`skin verification failed for theme ${activeId}`);
  const state = { version: VERSION, status: "active", themeId: activeId, port: DEFAULT_PORT, updatedAt: new Date().toISOString() };
  await writeJson(paths.statePath, state);
  return { ok: true, themeId: activeId, port: DEFAULT_PORT, applied: applied.applied, mode: renderers[0]?.mode ?? null, renderers };
}

async function windowsTerminalApply(themeId) {
  if (process.platform !== "win32") throw new Error("on macOS run scripts/apply.command --theme ID");
  const { activeId } = await selectedTheme(themeId);
  const shutdown = await forceQuitWorkBuddy();
  const launch = await launchWithCdp(DEFAULT_PORT);
  await waitForTargets(DEFAULT_PORT);
  return { ...(await inject(activeId)), shutdown, launch };
}

async function status() {
  const state = await readJson(paths.statePath);
  try {
    const renderers = await rendererStatus(DEFAULT_PORT);
    return { ok: renderers.length > 0 && renderers.every((renderer) => renderer.pass), state, port: DEFAULT_PORT, renderers };
  } catch (error) {
    return { ok: false, state, port: DEFAULT_PORT, renderers: [], error: error.message };
  }
}

export async function run(argv) {
  const { command, options } = parse(argv);
  if (command === "help") return {
    version: VERSION,
    commands: ["doctor", "list", "create --image PATH --name NAME", "rename --theme ID --name NAME", "delete --theme ID --confirm yes", "terminal-apply --theme ID --restart confirmed (Windows terminal only)", "switch --theme ID", "status", "verify", "pause", "restore --restart confirmed"],
  };
  if (command === "doctor") {
    const app = await inspectWorkBuddy();
    const identityValid = app.identityValid ?? app.bundleMatches;
    return { ok: app.appFound && identityValid, ...app, identityValid, node: process.versions.node, port: DEFAULT_PORT };
  }
  if (command === "list") return (await themes()).map(({ manifest, root }) => ({ id: manifest.id, name: manifest.name, appearance: manifest.appearance, root }));
  if (command === "create") {
    if (!options.image || !options.name) throw new Error("create requires --image and --name");
    await access(options.image);
    const created = await createTheme({ imagePath: options.image, name: options.name, storeRoot: paths.userThemesRoot });
    return { ok: true, id: created.manifest.id, name: created.manifest.name, root: created.root };
  }
  if (command === "rename") {
    if (!options.theme || !options.name) throw new Error("rename requires --theme and --name");
    const renamed = await renameUserTheme({ id: options.theme, name: options.name, storeRoot: paths.userThemesRoot });
    return { ok: true, id: renamed.manifest.id, name: renamed.manifest.name };
  }
  if (command === "delete") {
    if (!options.theme || options.confirm !== "yes") throw new Error("delete requires --theme ID --confirm yes");
    return { ok: true, ...(await deleteUserTheme({ id: options.theme, storeRoot: paths.userThemesRoot, deletedRoot: paths.deletedThemesRoot })) };
  }
  if (command === "terminal-apply") {
    if (options.restart !== "confirmed") throw new Error("terminal-apply requires --restart confirmed");
    return windowsTerminalApply(options.theme);
  }
  if (command === "stop") {
    if (options.restart !== "confirmed") throw new Error("stop requires --restart confirmed");
    return { ok: true, ...(await forceQuitWorkBuddy()) };
  }
  if (command === "inject") return inject(options.theme);
  if (command === "switch") {
    if (!(await fetchTargets(DEFAULT_PORT)).length) throw new Error("CDP is not active; run apply.command first");
    return inject(options.theme);
  }
  if (command === "pause") {
    const removed = await removeFromRenderer(DEFAULT_PORT).catch(() => ({ removed: 0 }));
    await writeJson(paths.statePath, { version: VERSION, status: "paused", port: DEFAULT_PORT, updatedAt: new Date().toISOString() });
    return { ok: true, ...removed };
  }
  if (command === "restore") {
    if (options.restart !== "confirmed") throw new Error("restore requires --restart confirmed");
    await removeFromRenderer(DEFAULT_PORT).catch(() => {});
    const shutdown = await forceQuitWorkBuddy();
    await launchNormally();
    await writeJson(paths.statePath, { version: VERSION, status: "restored", updatedAt: new Date().toISOString() });
    return { ok: true, restored: true, shutdown };
  }
  if (command === "status" || command === "verify") return status();
  throw new Error(`unknown command: ${command}`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === fileURLToPath(new URL(`file://${process.argv[1]}`).href)) {
  run(process.argv.slice(2)).then((result) => process.stdout.write(`${JSON.stringify(result, null, 2)}\n`)).catch((error) => {
    process.stderr.write(`${JSON.stringify({ ok: false, error: error.message })}\n`);
    process.exitCode = 1;
  });
}
