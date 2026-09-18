import { homedir } from "node:os";
import { dirname, join, resolve, win32 } from "node:path";
import { fileURLToPath } from "node:url";

export const PRODUCT_ID = "workbuddy-ambient-skin";
export const VERSION = "1.5.0";
export const BUNDLE_ID = "com.workbuddy.workbuddy";
export const APP_PATH = "/Applications/WorkBuddy.app";
export const DEFAULT_PORT = 9347;
export const RENDERER_SUFFIX = "/app.asar/renderer/index.html";
export const STYLE_ID = "workbuddy-ambient-skin-style";
export const HOST_ID = "workbuddy-ambient-skin-host";
export const STATE_KEY = "__WORKBUDDY_AMBIENT_SKIN__";
export const MAX_IMAGE_BYTES = 15 * 1024 * 1024;

const libDir = dirname(fileURLToPath(import.meta.url));
export const skillRoot = resolve(libDir, "../..");
export const assetsRoot = join(skillRoot, "assets");
export const bundledThemesRoot = join(assetsRoot, "themes");

export function studioPaths(home = homedir(), platform = process.platform, localAppData = process.env.LOCALAPPDATA) {
  const pathJoin = platform === "win32" ? win32.join : join;
  const stateRoot = platform === "win32"
    ? pathJoin(localAppData || pathJoin(home, "AppData", "Local"), "WorkBuddyAmbientSkin")
    : join(home, "Library", "Application Support", "WorkBuddyAmbientSkin");
  return {
    stateRoot,
    statePath: pathJoin(stateRoot, "state.json"),
    userThemesRoot: pathJoin(stateRoot, "themes"),
    deletedThemesRoot: pathJoin(stateRoot, "deleted-themes"),
    logPath: pathJoin(stateRoot, "ambient.log"),
    launchLogPath: pathJoin(stateRoot, "workbuddy-launch.log"),
  };
}
