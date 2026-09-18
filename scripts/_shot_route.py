# -*- coding: utf-8 -*-
"""点侧栏某个入口 → 等渲染 → 截图。

用法：
    python scripts/_shot_route.py 助理 [自动化 ...]
    python scripts/_shot_route.py --port 9348 助理
截图落在当前工作目录（或 WORKBUDDY_SHOT_DIR）。
"""
import base64
import json
import os
import sys
import time

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL, "scripts"))

import importlib.util
spec = importlib.util.spec_from_file_location("inj", os.path.join(SKILL, "scripts", "inject.py"))
inj = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inj)

argv = sys.argv[1:]
PORT = 9347
if "--port" in argv:
    index = argv.index("--port")
    PORT = int(argv[index + 1])
    del argv[index:index + 2]
NAMES = argv or ["助理"]
OUT_DIR = os.environ.get("WORKBUDDY_SHOT_DIR") or os.getcwd()

CLICK = """(() => {
  const want = %s;
  const hits = [];
  for (const el of document.querySelectorAll('[data-view-id="sidebar"] *')) {
    if ((el.textContent || '').trim() !== want) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) continue;
    hits.push(el);
  }
  if (!hits.length) return JSON.stringify({ clicked: false });
  const clickable = hits[hits.length - 1].closest('button, [role="button"], a') || hits[hits.length - 1];
  clickable.click();
  return JSON.stringify({ clicked: true, cls: String(clickable.className).slice(0, 50) });
})()"""


def main():
    targets = inj.renderer_targets(PORT)
    if not targets:
        print("端口 %d 上没有渲染目标" % PORT)
        return 1
    ws = targets[0]["webSocketDebuggerUrl"]
    for name in NAMES:
        result = json.loads(inj.cdp_evaluate(ws, CLICK % json.dumps(name), timeout=15))
        time.sleep(2.2)
        # 截图走 CDP 的 Page.captureScreenshot（inject.py 的 cdp_evaluate 只做 Runtime.evaluate）
        raw = capture(ws)
        path = os.path.join(OUT_DIR, "route_%s.png" % name.replace("·", "_").replace("/", "_"))
        with open(path, "wb") as handle:
            handle.write(raw)
        print("%-22s %s -> %s (%d 字节)" % (name, result, path, len(raw)))
    return 0


def capture(ws_url):
    """发一条 Page.captureScreenshot 并取回 base64。"""
    import re
    import socket
    import struct

    match = re.match(r"^wss?://([^/:]+):(\d+)(/.*)$", ws_url)
    host, port, path = match.group(1), int(match.group(2)), match.group(3)
    sock = socket.create_connection((host, port), timeout=60)
    sock.settimeout(60)
    try:
        inj._ws_handshake(sock, host, port, path)
        inj._ws_send_frame(sock, 0x1, json.dumps({
            "id": 1, "method": "Page.captureScreenshot", "params": {"format": "png"}}).encode("utf-8"))
        while True:
            op, payload = inj._ws_recv_message(sock)
            if op != 0x1:
                continue
            message = json.loads(payload.decode("utf-8", "replace"))
            if message.get("id") != 1:
                continue
            return base64.b64decode((message.get("result") or {}).get("data") or "")
    finally:
        try:
            sock.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
