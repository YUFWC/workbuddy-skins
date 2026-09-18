import { execFile as execFileCallback } from "node:child_process";
import { dirname, join, win32 } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFile = promisify(execFileCallback);
const helperPath = join(dirname(fileURLToPath(import.meta.url)), "workbuddy-windows.ps1");

export function normalizeWindowsPath(value) {
  return win32.normalize(String(value || "").trim()).replace(/[\\/]+$/, "").toLowerCase();
}

export function candidateWindowsPaths({ localAppData = "", programFiles = "", programFilesX86 = "" } = {}) {
  return [
    localAppData && win32.join(localAppData, "workbuddy", "WorkBuddy.exe"),
    localAppData && win32.join(localAppData, "Programs", "workbuddy", "WorkBuddy.exe"),
    programFiles && win32.join(programFiles, "WorkBuddy", "WorkBuddy.exe"),
    programFilesX86 && win32.join(programFilesX86, "WorkBuddy", "WorkBuddy.exe"),
  ].filter(Boolean);
}

async function runPowerShell(action, values = []) {
  const args = ["-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "RemoteSigned", "-File", helperPath, "-Action", action, ...values.map(String)];
  let result;
  try { result = await execFile("powershell.exe", args, { windowsHide: true, maxBuffer: 1024 * 1024 }); }
  catch (error) {
    if (error.code !== "ENOENT") throw new Error(error.stderr?.trim() || error.message);
    try { result = await execFile("pwsh.exe", args, { windowsHide: true, maxBuffer: 1024 * 1024 }); }
    catch (fallback) { throw new Error(fallback.stderr?.trim() || "PowerShell was not found"); }
  }
  const output = result.stdout.trim();
  if (!output) throw new Error(`Windows platform helper returned no result for ${action}`);
  try { return JSON.parse(output.split(/\r?\n/).at(-1)); }
  catch { throw new Error(`Windows platform helper returned malformed JSON for ${action}`); }
}

export async function inspectWorkBuddy() { return runPowerShell("inspect"); }
export async function forceQuitWorkBuddy() { return runPowerShell("force-quit"); }

export async function launchWithCdp(port) {
  return runPowerShell("launch-cdp", ["-Port", port]);
}

export async function launchNormally() { await runPowerShell("launch-normal"); }
