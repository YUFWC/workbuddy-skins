import * as macos from "./workbuddy-macos.mjs";
import * as windows from "./workbuddy-windows.mjs";

function platform() {
  if (process.platform === "darwin") return macos;
  if (process.platform === "win32") return windows;
  throw new Error(`unsupported platform: ${process.platform}`);
}

export const inspectWorkBuddy = (...args) => platform().inspectWorkBuddy(...args);
export const forceQuitWorkBuddy = (...args) => platform().forceQuitWorkBuddy(...args);
export const launchNormally = (...args) => platform().launchNormally(...args);
export const launchWithCdp = (...args) => platform().launchWithCdp(...args);
