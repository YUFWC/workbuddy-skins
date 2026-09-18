import { execFile as execFileCallback } from "node:child_process";
import { access } from "node:fs/promises";
import { promisify } from "node:util";

import { APP_PATH, BUNDLE_ID } from "./constants.mjs";

const execFile = promisify(execFileCallback);
const executable = `${APP_PATH}/Contents/MacOS/Electron`;
const plist = `${APP_PATH}/Contents/Info.plist`;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function exists(path) { try { await access(path); return true; } catch { return false; } }

export async function inspectWorkBuddy() {
  if (!await exists(APP_PATH)) return { appFound: false, appPath: APP_PATH };
  let bundleId = null;
  let version = null;
  try {
    bundleId = (await execFile("/usr/bin/plutil", ["-extract", "CFBundleIdentifier", "raw", "-o", "-", plist])).stdout.trim();
    version = (await execFile("/usr/bin/plutil", ["-extract", "CFBundleShortVersionString", "raw", "-o", "-", plist])).stdout.trim();
  } catch {}
  return { appFound: true, appPath: APP_PATH, executable, bundleId, bundleMatches: bundleId === BUNDLE_ID, version };
}

export function parseBundleExecutablePids(output, appPath = APP_PATH) {
  const matches = new Set();
  let pid = null;
  let sawExecutable = false;
  for (const line of String(output || "").split(/\r?\n/)) {
    if (line.startsWith("p")) {
      const candidate = Number(line.slice(1));
      pid = Number.isInteger(candidate) && candidate > 1 ? candidate : null;
      sawExecutable = false;
    } else if (pid && line.startsWith("n") && !sawExecutable) {
      sawExecutable = true;
      if (line.slice(1).startsWith(`${appPath}/Contents/`)) matches.add(pid);
    }
  }
  return [...matches];
}

export async function bundleWorkBuddyPids(run = execFile) {
  try {
    const { stdout } = await run("/usr/sbin/lsof", ["-n", "-a", "-d", "txt", "-Fpcn"], { maxBuffer: 32 * 1024 * 1024 });
    return parseBundleExecutablePids(stdout);
  } catch (error) {
    throw new Error(`could not enumerate the WorkBuddy process family: ${error.message}`);
  }
}

export async function forceQuitWorkBuddy({ timeoutMs = 8000, settleMs = 2000, inspect = inspectWorkBuddy, findPids = bundleWorkBuddyPids, signal = process.kill, wait = sleep } = {}) {
  const info = await inspect();
  if (!info.appFound || !info.bundleMatches || info.executable !== executable) throw new Error("official WorkBuddy bundle could not be verified");
  const initial = await findPids();
  if (!initial.length) { await wait(settleMs); return { stopped: true, forced: false, pids: [] }; }
  const killed = new Set();
  const deadline = Date.now() + timeoutMs;
  let emptySince = null;
  while (Date.now() < deadline) {
    const current = await findPids();
    if (!current.length) {
      emptySince ??= Date.now();
      if (Date.now() - emptySince >= 500) {
        await wait(settleMs);
        if (!(await findPids()).length) return { stopped: true, forced: true, pids: [...killed], settledMs: settleMs };
        emptySince = null;
      }
    } else {
      emptySince = null;
      for (const pid of current) {
        try { signal(pid, "SIGKILL"); killed.add(pid); }
        catch (error) { if (error.code !== "ESRCH") throw error; }
      }
    }
    await wait(100);
  }
  throw new Error(`WorkBuddy processes did not stop: ${(await findPids()).join(", ")}`);
}

export async function launchNormally() {
  await execFile("/usr/bin/open", [APP_PATH]);
}
