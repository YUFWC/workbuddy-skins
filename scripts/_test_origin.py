# -*- coding: utf-8 -*-
"""验证：CDP WebSocket 拒绝带 Origin 的连接 —— 这就是渲染进程连不上的原因。

分别试三次连接：
  1) 不带 Origin            → 应成功（我们 Python 侧走的就是这条）
  2) 带 Origin: file://      → 大概率被拒
  3) 带 Origin: https://www.workbuddy.cn → 大概率被拒
如果 2/3 被拒而 1 成功，就证明「渲染进程的 WebSocket 需要 --remote-allow-origins」。
"""
import base64
import os
import socket
import sys

sys.path.insert(0, ".")
import inject as inj  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9348


def ws_handshake_with_origin(ws_url, origin):
    import re
    match = re.match(r"^wss?://([^/:]+):(\d+)(/.*)$", ws_url)
    host, port, path = match.group(1), int(match.group(2)), match.group(3)
    sock = socket.create_connection((host, port), timeout=8)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    lines = ["GET %s HTTP/1.1" % path, "Host: %s:%d" % (host, port),
             "Upgrade: websocket", "Connection: Upgrade",
             "Sec-WebSocket-Key: %s" % key, "Sec-WebSocket-Version: 13"]
    if origin:
        lines.append("Origin: %s" % origin)
    request = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")
    sock.sendall(request)
    buffer = bytearray()
    while b"\r\n\r\n" not in buffer:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buffer += chunk
    head = bytes(buffer).split(b"\r\n\r\n", 1)[0].decode("latin-1")
    sock.close()
    return head.split("\r\n")[0]


targets = inj.space_iframe_targets(PORT)
if not targets:
    print("资料库 iframe 没开（端口 %d）" % PORT)
    sys.exit(1)
ws = targets[0]["webSocketDebuggerUrl"]
print("目标:", ws[:70])
print("")

for label, origin in [("不带 Origin", None),
                      ("Origin: file://", "file://"),
                      ("Origin: https://www.workbuddy.cn", "https://www.workbuddy.cn"),
                      ("Origin: http://localhost", "http://localhost")]:
    try:
        status = ws_handshake_with_origin(ws, origin)
    except Exception as error:
        status = "EXC %s" % error
    print("  %-36s → %s" % (label, status))
