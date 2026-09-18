import { readFile } from "node:fs/promises";
import { join } from "node:path";

import { assetsRoot } from "./constants.mjs";
import { evaluateAll, fetchTargets, waitForTargets } from "./cdp.mjs";
import { buildInstallExpression, buildRemoveExpression, buildStatusExpression } from "./renderer.mjs";

function entries(themes) {
  return themes.map(({ manifest, imageDataUrl, artKey }) => ({ ...manifest, imageDataUrl, artKey }));
}

export async function applyToRenderer({ port, themes, activeId }) {
  const css = await readFile(join(assetsRoot, "ambient.css"), "utf8");
  const expression = buildInstallExpression({
    css, themes: entries(themes), activeId,
  });
  let targets = await waitForTargets(port);
  let values;
  let reconnected = false;
  try {
    values = await evaluateAll(targets, port, expression, undefined, 35000);
  } catch (error) {
    if (!/CDP WebSocket (closed|connection failed)/.test(error.message)) throw error;
    await new Promise((resolve) => setTimeout(resolve, 500));
    targets = await waitForTargets(port);
    values = await evaluateAll(targets, port, expression, undefined, 35000);
    reconnected = true;
  }
  return { applied: values.length, themeId: activeId, values, targetIds: targets.map((target) => target.id), reconnected };
}

export async function removeFromRenderer(port) {
  const targets = await fetchTargets(port);
  return { removed: (await evaluateAll(targets, port, buildRemoveExpression())).length };
}

export async function rendererStatus(port) {
  const targets = await fetchTargets(port);
  return evaluateAll(targets, port, buildStatusExpression());
}
