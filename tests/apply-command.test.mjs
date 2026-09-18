import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("macOS apply uses one synchronous restart pipeline", async () => {
  const script = await readFile(new URL("../scripts/apply.command", import.meta.url), "utf8");
  assert.match(script, /osascript.*WorkBuddy.*quit/);
  assert.match(script, /pkill -9 -f '\/Applications\/WorkBuddy/);
  assert.match(script, /nohup .*Electron.*--remote-debugging-port/);
  assert.match(script, /curl --noproxy '\*'.*json\/version/);
  assert.match(script, /json\/list/);
  assert.match(script, /inject --theme/);
  assert.match(script, /verify/);
  assert.doesNotMatch(script, /pending|apply-request|apply-result|worker/);
});

test("Agent runtime contains no automatic apply handoff", async () => {
  const source = await readFile(new URL("../scripts/ambient.mjs", import.meta.url), "utf8");
  assert.doesNotMatch(source, /command === "apply"|openApply|workbuddy-ambient-apply-|\/usr\/bin\/open|launchctl|do script/);
});

test("manual apply documentation provides complete platform commands", async () => {
  const readme = await readFile(new URL("../README.md", import.meta.url), "utf8");
  const skill = await readFile(new URL("../SKILL.md", import.meta.url), "utf8");
  assert.match(readme, /"\$HOME\/\.workbuddy\/skills\/workbuddy-ambient-skin\/scripts\/apply\.command" --theme miku-neko-maid/);
  assert.match(readme, /& "\$HOME\\\.workbuddy\\skills\\workbuddy-ambient-skin\\scripts\\workbuddy-ambient\.ps1" terminal-apply --theme miku-neko-maid --restart confirmed/);
  assert.match(skill, /Never execute a restart\/apply command from the Agent sandbox/);
  assert.match(skill, /Replace `ID` with the selected theme's real ID/);
});
