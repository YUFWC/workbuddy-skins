# -*- coding: utf-8 -*-
"""inject.py 的 CDP 层回归测试（不依赖真实 WorkBuddy）。

起一个假的 Chromium 调试端点（HTTP + WebSocket 同端口），验证：
  * http_get_json / cdp_up / renderer_targets 的目标筛选
  * _ws_handshake 握手（含"不发 Origin"）
  * cdp_evaluate 的请求/响应往返
  * 大表达式（数 MB）分片发送后被完整接收
  * 服务端分片响应、以及响应前的 ping 帧都能正确处理

用法：python tests/test_inject_cdp.py
"""
import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import socket
import struct
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
INJECT = os.path.join(HERE, "..", "scripts", "inject.py")

spec = importlib.util.spec_from_file_location("inj", INJECT)
inj = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inj)

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

failures = []


def check(label, condition, detail=""):
    if condition:
        print("  [OK]   %s" % label)
    else:
        print("  [失败] %s %s" % (label, detail))
        failures.append(label)


def send_unmasked(conn, opcode, payload, fin=True):
    header = bytearray([(0x80 if fin else 0x00) | opcode])
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length < 65536:
        header.append(126)
        header += struct.pack(">H", length)
    else:
        header.append(127)
        header += struct.pack(">Q", length)
    conn.sendall(bytes(header) + payload)


def recv_exact(conn, count):
    buffer = bytearray()
    while len(buffer) < count:
        chunk = conn.recv(count - len(buffer))
        if not chunk:
            raise ConnectionError("EOF")
        buffer += chunk
    return bytes(buffer)


def read_raw_frame(conn):
    """裸读一帧，返回 (opcode, fin, masked, payload)。

    不能用 inject.py 的 _ws_recv_message 来观察 pong ——
    那个函数把 pong 当作"与我无关的控制帧"直接跳过、继续等下一帧，
    所以在服务端用它去读 pong 只会一直阻塞到超时（一开始就是这么误判的）。
    """
    b1 = recv_exact(conn, 1)[0]
    b2 = recv_exact(conn, 1)[0]
    fin = bool(b1 & 0x80)
    opcode = b1 & 0x0F
    masked = bool(b2 & 0x80)
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack(">H", recv_exact(conn, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", recv_exact(conn, 8))[0]
    mask = recv_exact(conn, 4) if masked else None
    payload = recv_exact(conn, length) if length else b""
    if mask:
        payload = bytes(payload[i] ^ mask[i & 3] for i in range(len(payload)))
    return opcode, fin, masked, payload


class MockCdp:
    """最小可用的假 CDP 端点。"""

    def __init__(self, fragment_response=False, ping_first=False, keep_alive=True):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.fragment_response = fragment_response
        self.ping_first = ping_first
        # 默认模拟真实 Chromium：**不理会 Connection: close，保持连接不关**。
        # 这样一旦客户端退化成"读到 EOF 为止"，测试就会卡到超时而不是假通过。
        self.keep_alive = keep_alive
        self.received = []
        self.seen_ops = []
        self.saw_origin = None
        self.got_pong = False
        self.post_error = None
        self.pong_detail = None
        self._stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    def stop(self):
        self._stop = True
        try:
            self.sock.close()
        except Exception:
            pass

    def _serve(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        try:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            head = data.split(b"\r\n\r\n", 1)[0].decode("latin-1")
            request_line = head.split("\r\n")[0]
            if "upgrade: websocket" in head.lower():
                self.saw_origin = re.search(r"(?im)^Origin:\s*(\S+)", head)
                self._ws_loop(conn, head)
            else:
                path = request_line.split()[1]
                if path == "/json/version":
                    body = json.dumps({"Browser": "MockChromium/1", "webSocketDebuggerUrl": ""}).encode()
                elif path == "/json/list":
                    body = json.dumps([
                        {"type": "page", "title": "other", "url": "file:///D:/x/index.html",
                         "webSocketDebuggerUrl": "ws://127.0.0.1:%d/devtools/page/9" % self.port},
                        {"type": "page", "title": "WorkBuddy",
                         "url": "file:///D:/Users/x/resources/app.asar/renderer/index.html?locale=zh-CN&accountSnapshot=%7B%7D",
                         "webSocketDebuggerUrl": "ws://127.0.0.1:%d/devtools/page/1" % self.port},
                        # 「资料库」跨域 iframe —— 独立 target，type 是 iframe 不是 page。
                        # 默认 renderer_targets() 必须滤掉它；all_types=True 才拿得到。
                        {"type": "iframe", "title": "https://www.workbuddy.cn/space/home",
                         "url": "https://www.workbuddy.cn/space/home?theme=light&locale=zh-CN",
                         "webSocketDebuggerUrl": "ws://127.0.0.1:%d/devtools/page/7" % self.port},
                    ]).encode()
                else:
                    body = b"[]"
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n"
                             b"Connection: keep-alive\r\n\r\n" % len(body) + body)
                if self.keep_alive:
                    time.sleep(1.2)     # 挂着不关，逼客户端按 Content-Length 判断读完
                conn.close()
        except Exception:
            pass

    def _ws_loop(self, conn, head):
        key = re.search(r"(?im)^Sec-WebSocket-Key:\s*(\S+)", head).group(1)
        accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                      "Connection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n" % accept).encode())
        while not self._stop:
            op, payload = inj._ws_recv_message(conn)
            self.seen_ops.append(op)
            if op == 0xA:
                self.got_pong = True
                continue
            if op != 0x1:
                continue
            self.received.append(payload)
            try:
                message = json.loads(payload.decode("utf-8"))
            except Exception:
                message = {}
            expression = ((message.get("params") or {}).get("expression")) or ""
            if self.ping_first:
                # 先发 ping，再**裸读**客户端回的帧（pong 必须用裸读才看得到）。
                send_unmasked(conn, 0x9, b"keepalive")
                conn.settimeout(2.0)
                try:
                    op3, fin3, masked3, payload3 = read_raw_frame(conn)
                    self.seen_ops.append(op3)
                    self.pong_detail = "opcode=0x%X fin=%d masked=%s payload=%r" % (op3, fin3, masked3, payload3)
                    if op3 == 0xA and payload3 == b"keepalive":
                        self.got_pong = True
                except Exception as error:
                    self.post_error = "%s: %s" % (type(error).__name__, error)
            reply = json.dumps({
                "id": message.get("id", 1),
                "result": {"result": {"type": "string", "value": json.dumps({
                    "installed": True, "exprLength": len(expression),
                    "exprTail": expression[-24:],
                }, ensure_ascii=False)}},
            }).encode("utf-8")
            if self.fragment_response:
                third = max(1, len(reply) // 3)
                send_unmasked(conn, 0x1, reply[:third], fin=False)
                send_unmasked(conn, 0x0, reply[third:2 * third], fin=False)
                send_unmasked(conn, 0x0, reply[2 * third:], fin=True)
            else:
                send_unmasked(conn, 0x1, reply)
            return   # 每次连接只处理一个请求，和真实 CDP 的用法一致


def main():
    print("=" * 66)
    print(" inject.py CDP 层回归测试")
    print("=" * 66)

    mock = MockCdp()
    try:
        print("\n[1] HTTP 探测")
        version = inj.http_get_json("127.0.0.1", mock.port, "/json/version", timeout=5)
        check("http_get_json 返回解析后的 JSON", version.get("Browser") == "MockChromium/1", repr(version))
        check("cdp_up() 为真", inj.cdp_up(mock.port) is True)

        print("\n[2] 目标筛选（只认 renderer/index.html）")
        targets = inj.renderer_targets(mock.port)
        check("只挑出 1 个渲染目标", len(targets) == 1, "实际 %d" % len(targets))
        check("挑中的是 WorkBuddy 渲染页",
              bool(targets) and "renderer/index.html" in targets[0]["url"])
        check("默认滤掉了 iframe target",
              all(t.get("type") == "page" for t in targets))

        print("\n[2b] 跨域 iframe target（all_types=True）")
        everything = inj.renderer_targets(mock.port, all_types=True)
        check("all_types 拿到全部 3 个 target", len(everything) == 3,
              "实际 %d" % len(everything))
        frames = inj.space_iframe_targets(mock.port)
        check("space_iframe_targets 只挑出 iframe", len(frames) == 1,
              "实际 %d" % len(frames))
        check("挑中的是 workbuddy.cn/space",
              bool(frames) and "workbuddy.cn/space" in frames[0]["url"])
        other_port = inj.renderer_targets(mock.port, all_types=True)
        check("page 目标在 all_types 里也还在",
              any("renderer/index.html" in t["url"] for t in other_port))

        print("\n[3] WebSocket 往返")
        ws_url = targets[0]["webSocketDebuggerUrl"]
        small = "(() => 42)()"
        value = inj.cdp_evaluate(ws_url, small, timeout=10)
        info = json.loads(value)
        check("握手未发送 Origin 头", mock.saw_origin is None, repr(mock.saw_origin))
        check("服务端收到了完整表达式", info.get("exprLength") == len(small), repr(info))
        check("表达式尾部一致", info.get("exprTail") == small[-24:], repr(info))

        print("\n[4] 大表达式（4 MB）分片发送")
        big = "/*" + ("A" * (4 * 1024 * 1024)) + "*/0"
        value = inj.cdp_evaluate(ws_url, big, timeout=30)
        info = json.loads(value)
        check("4 MB 表达式被完整接收", info.get("exprLength") == len(big),
              "收到 %s / 期望 %s" % (info.get("exprLength"), len(big)))
    finally:
        mock.stop()

    print("\n[5] 服务端分片响应 + 响应前 ping")
    mock2 = MockCdp(fragment_response=True, ping_first=True)
    try:
        targets = inj.renderer_targets(mock2.port)
        ws_url = targets[0]["webSocketDebuggerUrl"]
        value = inj.cdp_evaluate(ws_url, "ping-test", timeout=10)
        info = json.loads(value)
        check("分片响应被正确重组", info.get("exprLength") == len("ping-test"), repr(info))
        check("收到 ping 后回了 pong", mock2.got_pong is True,
              "pong 帧 = %s，post_error = %s" % (mock2.pong_detail, mock2.post_error))
    finally:
        mock2.stop()

    print("\n[6] 自愈器装载（install_space_selfheal）")
    # 这里只验证「能读文件、能拼出带端口的脚本、端口写进去了」——
    # 真正的注入效果需要真实渲染进程，见 _parent_load_test.py。
    mock3 = MockCdp()
    try:
        css = inj.read_space_css()
        check("space-glass.css 读得到", len(css) > 1000, "%d 字节" % len(css))
        script = inj.read_space_selfheal()
        check("space-selfheal.js 读得到", len(script) > 1000, "%d 字节" % len(script))
        check("自愈器带 install() 出口", ".install =" in script or ".install=" in script)
        check("自愈器会监听 iframe load", "addEventListener(\"load\"" in script)
        check("自愈器会 fetch /json/list", "/json/list" in script)
        check("自愈器用 WebSocket 连 CDP", "new WebSocket" in script)

        # 端口必须被钉进脚本里（主文档是 file://，推不出端口）
        result = inj.install_space_selfheal(mock3.port, log=None, quiet=True)
        check("install_space_selfheal 返回 dict",
              isinstance(result, dict) and "installed" in result and "healed" in result,
              repr(result))
        check("自愈器装上了", result.get("installed") is True, repr(result))
        # mock 回的是 {"installed": True, "exprLength": ...}，没有 ok 字段 → healed=False
        check("mock 下 healed 为 False（缺 ok 字段）", result.get("healed") is False,
              repr(result))
        # 检查真的把端口写进了表达式
        sent = b"".join(mock3.received).decode("utf-8", "replace")
        check("端口被钉进注入脚本", str(mock3.port) in sent)
    finally:
        mock3.stop()

    print("\n[7] 「灵感」页（discover）规则")
    # 用户 2026-09-17 红框圈了标题行白带。元凶是整页外壳 .discover-panel-page。
    try:
        css = inj.read_css()
        import re as _re

        def strip_c(text):
            return _re.sub(r"/\*.*?\*/", "", text, flags=_re.S)

        def has_rule(text, selector, must_contain=None):
            """找选择器列表里含 selector 的规则，可选校验声明体含某串。"""
            for m in _re.finditer(r"([^{}]+)\{([^}]*)\}", text):
                if selector in m.group(1):
                    if must_contain is None:
                        return True
                    if must_contain in m.group(2):
                        return True
            return False

        clean = strip_c(css)

        check("ambient.css 有 .discover-panel-page 规则",
              has_rule(clean, ".discover-panel-page"))
        check("  → 压成 transparent",
              has_rule(clean, ".discover-panel-page", "transparent"))
        check("有 .dc-title / .dc-header 规则（防止上游加底色）",
              has_rule(clean, ".dc-title") and has_rule(clean, ".dc-header"))
        check("有 .dc-playbook-card 玻璃规则",
              has_rule(clean, ".dc-playbook-card", "backdrop-filter"))
        check("有 .dc-card-cover 规则（封面占位块）",
              has_rule(clean, ".dc-card-cover"))
        check("有 .dc-search-input 规则", has_rule(clean, ".dc-search-input"))
        check("有 .dc-fav-filter-btn 规则", has_rule(clean, ".dc-fav-filter-btn"))

        # ⚠️ 关键回归：外层滚动容器必须透明，不能和药丸按钮共用子串匹配
        check("外层 .dc-category-tabs 是 transparent",
              has_rule(clean, ".dc-category-tabs", "transparent"))
        check("内层 .dc-category-tab 是玻璃（带 backdrop-filter）",
              has_rule(clean, ".dc-category-tab", "backdrop-filter"))
        # 不能出现「一个子串选择器同时命中内外两层」的写法
        check("没有 [class*=\"dc-category-tab\"] 子串匹配（会连外层容器一起玻璃化）",
              'class*="dc-category-tab"' not in clean)
    except Exception as error:
        check("灵感页规则检查未抛异常", False, repr(error))

    print("\n[8] 「乐享知识库」面板（tencent-lexiang-panel）规则")
    # 用户 2026-09-17 第三次同模式：内部全透明，白挂在外壳上。
    try:
        css = inj.read_css()
        import re as _re2

        def strip_c2(text):
            return _re2.sub(r"/\*.*?\*/", "", text, flags=_re.S)

        def has_rule2(text, selector, must_contain=None):
            for m in _re2.finditer(r"([^{}]+)\{([^}]*)\}", text):
                if selector in m.group(1):
                    if must_contain is None:
                        return True
                    if must_contain in m.group(2):
                        return True
            return False

        clean2 = strip_c2(css)

        check("有 .tencent-lexiang-panel 规则",
              has_rule2(clean2, ".tencent-lexiang-panel"))
        check("  → 压成 transparent",
              has_rule2(clean2, ".tencent-lexiang-panel", "transparent"))
        check("有 .lexiang-auth-guide 透明规则",
              has_rule2(clean2, ".lexiang-auth-guide", "transparent"))
        check("有 .route-keep-alive-slot 透明规则",
              has_rule2(clean2, ".route-keep-alive-slot", "transparent"))
        check("权限卡 .__permissions 是玻璃（带 backdrop-filter）",
              has_rule2(clean2, ".lexiang-auth-guide__permissions", "backdrop-filter"))

        # ⚠️ 关键：主按钮不能把 background 压透明，否则白字看不见
        btn_body = None
        for m in _re2.finditer(r"([^{}]+)\{([^}]*)\}", clean2):
            if ".lexiang-auth-guide__btn" in m.group(1):
                btn_body = m.group(2)
                break
        check("主按钮规则存在", btn_body is not None)
        if btn_body is not None:
            check("  → **没有**覆盖 background（保深色底）",
                  "background:" not in btn_body.replace("background-color:", ""))
    except Exception as error:
        check("乐享规则检查未抛异常", False, repr(error))

    print("\n[9] 乐享文件列表表头 —— 用户要求 **50% 透明**（不是全透明）")
    # ⚠️ 这是唯一一处用户明确要"半透明"的地方，断言要锁住 0.5 这个力度，
    #    防止以后被"顺手统一成全透明"。
    try:
        css = inj.read_css()
        import re as _re3

        def strip_c3(text):
            return _re3.sub(r"/\*.*?\*/", "", text, flags=_re.S)

        clean3 = strip_c3(css)
        body = None
        for m in _re3.finditer(r"([^{}]+)\{([^}]*)\}", clean3):
            sel = m.group(1)
            if ".tencent-lexiang-catalog__header" in sel \
                    and "dark" not in sel:
                body = m.group(2)
                break
        check("有 .tencent-lexiang-catalog__header 规则", body is not None)
        if body is not None:
            check("  → 背景是 50% 白（不是 transparent，也不是纯白）",
                  "rgb(255 255 255 / 0.5)" in body, body.strip()[:90])
            check("  → 带 backdrop-filter（玻璃质感）", "backdrop-filter" in body)
            check("  → **没有**写成 transparent（用户要的是半透明）",
                  "background: transparent" not in body.replace("background-color: transparent", ""))
        # 深色也有对应规则
        dark_body = None
        for m in _re3.finditer(r"([^{}]+)\{([^}]*)\}", clean3):
            if ".tencent-lexiang-catalog__header" in m.group(1) and "dark" in m.group(1):
                dark_body = m.group(2)
                break
        check("深色主题有对应规则", dark_body is not None)
        if dark_body is not None:
            check("  → 深色下也是 50%", "0.5" in dark_body)
        # 分隔线要保留（层次感）
        check("列表分隔线 .__files-divider 未被透明化",
              not has_rule2(clean3, ".tencent-lexiang-panel__files-divider"))
    except Exception as error:
        check("表头 50% 检查未抛异常", False, repr(error))

    print("\n[10] 第四轮：红框 4 处必须是「完全透明」而非半透明")
    # 用户 2026-09-17 明确要求：这些位置不要半透明玻璃，要 transparent。
    # 这组断言是**回归锁** —— 以后谁把它改回 rgb(... / .xx) 就会红。
    try:
        css = inj.read_space_css()
        # 逐个建「规则块」切片：从选择器到最后一条声明
        import re

        def strip_comments(text):
            """去掉 /* ... */ 注释 —— 注释里会引用旧值做说明，不算违规。"""
            return re.sub(r"/\*.*?\*/", "", text, flags=re.S)

        def rule_body(text, selector):
            """取出**任何**包含该选择器的规则块的声明体。

            ⚠️ 不能只匹配 `selector {`：这几条规则都是**逗号并列选择器**
            （`html.wbas-space .a,\nhtml.wbas-space .b { ... }`），
            选择器名字不在行首。所以改成：找到所有 {...} 块，
            谁的**前置选择器列表**里包含这个 token，就要它的声明体。
            """
            out = []
            for m in re.finditer(r"([^{}]+)\{([^}]*)\}", text):
                sel_text, body = m.group(1), m.group(2)
                if selector in sel_text:
                    out.append(body)
            return out

        clean = strip_comments(css)

        FOUR = [
            ".workspace-tree-section-header-pc",
            ".memory-nav-item-pc",
            ".workspace-tree-space-node-pc",
            ".doc-feed-th-pc",
        ]
        for sel in FOUR:
            bodies = rule_body(clean, "html.wbas-space " + sel)
            merged = "\n".join(bodies)
            check("  %s 存在浅色规则" % sel, bool(bodies), "%d 条" % len(bodies))
            check("  %s 是 transparent" % sel,
                  "background: transparent" in merged
                  or "background-color: transparent" in merged)
            check("  %s 不再有半透明值" % sel,
                  "255 255 255 / ." not in merged and "255 255 255 /." not in merged,
                  merged.strip().splitlines()[:1])

        # 深色主题段同样不该出现 .10 / .14 这类半透明
        dark_bodies = rule_body(clean, "html.wbas-space.dark .doc-feed-th-pc")
        check("深色下 .doc-feed-th-pc 也是 transparent",
              any("transparent" in b for b in dark_bodies),
              "%d 条" % len(dark_bodies))
        dark_sec = rule_body(clean, "html.wbas-space.dark .workspace-tree-section-header-pc")
        check("深色下 .workspace-tree-section-header-pc 也是 transparent",
              any("transparent" in b for b in dark_sec),
              "%d 条" % len(dark_sec))

        # 全局不该再残留这两个旧值（只看非注释正文）
        check("正文里不再有 255 255 255 / .28", "255 255 255 / .28" not in clean)
        check("正文里不再有 255 255 255 / .34", "255 255 255 / .34" not in clean)

        # 但「选中态」必须保留可辨识底色 —— 这是唯一例外
        active = rule_body(clean, "html.wbas-space .memory-nav-item-active-pc")
        check("选中态保留了可辨识的主色底",
              any("rgb(86 118 200" in b for b in active),
              "%d 条" % len(active))
    except Exception as error:
        check("第四轮回归检查未抛异常", False, repr(error))

    # ---- [11] 视频背景：CLI 参数 + 注入源码 + CSS 规则 ----
    print("\n[11] 视频背景（--video / --video-off）")
    try:
        skill_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        renderer_path = os.path.join(skill_root, "scripts", "lib", "renderer.mjs")
        inject_path = os.path.join(skill_root, "scripts", "inject.py")
        css_path = os.path.join(skill_root, "assets", "ambient.css")

        renderer_src = io.open(renderer_path, encoding="utf-8").read()
        inject_src = io.open(inject_path, encoding="utf-8").read()
        css_src = io.open(css_path, encoding="utf-8").read()

        # CLI 必须同时有开/关两个入口（关不掉的功能不算功能）
        check("inject.py 支持 --video", '"--video"' in inject_src and "--video" in inject_src)
        check("inject.py 支持 --video-off", "--video-off" in inject_src)
        check("inject.py 支持 --video= 等号写法", '"--video="' in inject_src or "--video=" in inject_src)

        # 渲染层核心件（缺一个这个功能就不成立）
        for token, why in [
            ("wbas-video-layer", "视频层元素 id"),
            ("normalizeVideoSrc", "路径规范化"),
            ("encodeFilePath", "非 ASCII 路径百分号编码"),
            ("paintVideoUi", "菜单 UI 回灌"),
            ("setVideo", "对外开关接口"),
            ("getVideo", "对外读状态接口"),
            ("videoLayer.play()", "自动播放调用"),
        ]:
            check("renderer.mjs 含 %s（%s）" % (token, why), token in renderer_src)

        # 自动播放三件套：少一个浏览器就拦
        for attr in ["muted", "loop", "autoplay", "playsinline"]:
            check("视频层设置了 %s" % attr, attr in renderer_src)

        # 关键：必须挂真实 DOM，不能指望 ::before
        check("视频层是真实元素并 append 到 DOM",
              "appendChild(videoLayer)" in renderer_src)

        # CSS 规则 27 的三条
        check("CSS 有视频开启时隐藏静态壁纸的规则",
              'data-wbas-video="on"' in css_src and "background-image: none" in css_src)
        check("CSS 有 #wbas-video-layer 本体规则", "#wbas-video-layer" in css_src)
        check("CSS 视频模式下的底部蒙版被加强",
              'data-wbas-video="on"' in css_src and "#root::after" in css_src)

        # 编码函数必须幂等 —— 这是"点两次应用写坏路径"那个 bug 的护栏
        check("encodeFilePath 先 decode 再 encode（幂等）",
              "decodeURIComponent" in renderer_src)

        # cleanup 要能收干净，否则下次注入会叠第二层视频
        cleanup_block = renderer_src.split("cleanup() {")[1].split("},")[0] if "cleanup() {" in renderer_src else ""
        check("cleanup 会移除视频层", "videoLayer.remove()" in cleanup_block)
        check("cleanup 会清掉 data-wbas-video 属性",
              "delete html.dataset.wbasVideo" in cleanup_block)

        # 用户明确要求过"路径要用 Windows 盘符写法" —— UI 得给出这个提示
        check("菜单里有 Windows 盘符写法的提示",
              "Windows 盘符" in renderer_src)

        # ---- 滑块不能留原生白底（2026-09-17 用户报的红框白带）----
        # Chromium 的 input[type=range] 默认 appearance 会给一块不透明白色
        # （实测 computed background-color 就是 rgb(255,255,255)），
        # 在深色/半透明面板上就是一条突兀白带。必须显式取消 appearance
        # 并自己画轨道 + 滑块。
        check("滑块显式设了 appearance:none",
              "appearance:none" in renderer_src and 'input[type="range"]' in renderer_src)
        check("滑块背景是 transparent（不是原生白）",
              'input[type="range"]{-webkit-appearance:none;appearance:none' in renderer_src)
        check("自己画了 webkit 轨道",
              "::-webkit-slider-runnable-track" in renderer_src)
        check("自己画了 webkit 滑块",
              "::-webkit-slider-thumb" in renderer_src)
        check("给了 Firefox 兜底（-moz-range-track）",
              "::-moz-range-track" in renderer_src)
        check("深色主题下轨道颜色也跟着改",
              'data-appearance="dark"' in renderer_src
              and "::-webkit-slider-runnable-track" in renderer_src)
        check("滑块的 accent-color 仍在（accent-color 之外的兜底）",
              "accent-color" in renderer_src)

        # ---- 选择本地视频：和「选择本地图片」同款交互（2026-09-17）----
        # 用户要求：不要把路径丢给用户手敲，要一个能点开文件对话框的入口。
        check("有「选择本地视频」按钮行", 'class="item video-pick"' in renderer_src)
        check("有隐藏的 video 文件 input",
              'class="video-picker"' in renderer_src
              and 'type="file"' in renderer_src)
        check("文件 input 限定了视频类型（accept 含 video/mp4）",
              "video/mp4" in renderer_src and "accept=" in renderer_src)
        check("文件 input 带 hidden（不能露在面板上）",
              "video-picker\" type=\"file\"" in renderer_src
              and " hidden" in renderer_src)
        check("用 createObjectURL 把 File 变成可播地址",
              "URL.createObjectURL" in renderer_src)
        check("关闭/换源时要 revokeObjectURL（否则内存泄漏）",
              "revokeObjectURL" in renderer_src)
        check("手动路径行默认折叠",
              '<div class="video-row" hidden>' in renderer_src)
        check("手动填路径有折叠开关",
              "video-manual-toggle" in renderer_src
              and "手动填路径" in renderer_src)
        check("超大文件会被挡下（避免点完卡死）",
              "1024 * 1024 * 1024" in renderer_src)
        check("视频名会单独记一份（blob 失效后还能报出名字）",
              "video-name-v1" in renderer_src)

        # ---- blob: 重启失效必须被识别（Electron 拿不到 File.path）----
        check("blob: 开头的源会被判为过期", "/^blob:/.test(videoState.src)" in renderer_src)
        check("过期后清空 src 并关掉开关",
              "expiredBlobName" in renderer_src
              and "videoState.enabled = false" in renderer_src)
        check("过期后在状态行给出可读提示",
              "重启后需要重新选择" in renderer_src)

        # ---- 播放卡顿优化（用户明确点名的痛点）----
        # 1) 视频层独立成 GPU 合成层，UI 重绘不再等视频解码
        check("视频层做了 GPU 合成层提升",
              "translateZ(0)" in renderer_src and "will-change" in renderer_src)
        check("视频层用了 backface-visibility:hidden（少一次合成）",
              "backface-visibility" in renderer_src)
        # 2) 缩放走 CSS 变量，否则会覆盖 translateZ(0) 把图层提升冲掉
        check("缩放走 CSS 变量而不是直接写 transform",
              "--wbas-video-scale" in renderer_src
              and "scale(var(--wbas-video-scale,1))" in renderer_src)
        # 3) 只在值真变了时才写样式 —— 重复写 filter 会重建合成器图层
        for expr, why in [
            ("videoLayer.style.filter !== filter", "filter"),
            ('getPropertyValue("--wbas-video-scale") !== scale', "缩放变量"),
            ("videoLayer.style.opacity !== opacity", "透明度"),
        ]:
            check("只在值变了时才写 %s" % why, expr in renderer_src)
        # 4) 换源判定不能比 videoLayer.src —— 浏览器会把它重写成绝对 URL
        check("用 data-wbas-src 记上次源（避免误判换源反复 load）",
              'getAttribute("data-wbas-src")' in renderer_src
              and 'setAttribute("data-wbas-src"' in renderer_src)
        # 5) 后台节流后 Chromium 会静默 paused（不触发任何事件）→ 靠低频看门狗
        check("有低频看门狗兜底恢复播放",
              "videoWatchdog" in renderer_src
              and "setInterval" in renderer_src)
        check("看门狗在 cleanup 里被清掉",
              "clearInterval(videoWatchdog)" in renderer_src)
        check("窗口重新获得焦点时恢复播放",
              '"focus", resumeVideo' in renderer_src)
        check("页面可见性变化时恢复播放",
              "visibilitychange" in renderer_src and "resumeVideo" in renderer_src)
        # 6) 缓冲态要看得见，不然用户只会觉得"卡住了"
        check("等待/播放事件有监听（缓冲状态可视）",
              '"waiting"' in renderer_src and '"playing"' in renderer_src)
        check("状态行会显示「缓冲中」", "缓冲中" in renderer_src)
        # 7) 自动播放被拦只提示一次，不要每到一帧就弹
        check("自动播放被拦只提示一次", "videoState.prompted" in renderer_src)
        check("换源时重置 prompted",
              "if (next !== videoState.src) videoState.prompted = false;" in renderer_src)
        # 8) 关掉画中画/远程播放，少两个后台开销
        check("关掉了画中画和远程播放",
              "disablePictureInPicture" in renderer_src
              and "disableRemotePlayback" in renderer_src)
        # 9) cleanup 要释放解码器，不能只 remove() 元素
        check("cleanup 会释放视频解码器",
              "videoLayer.pause()" in cleanup_block
              and "removeAttribute(\"src\")" in cleanup_block)
    except Exception as error:
        check("视频背景静态检查未抛异常", False, repr(error))

    # ---- [12] 首页文字可控（hero 主标题 / 副标题 / 快捷按钮）----
    print("\n[12] 首页文字（可自定义 hero 文案）")
    try:
        skill_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        renderer_src = io.open(os.path.join(skill_root, "scripts", "lib", "renderer.mjs"),
                               encoding="utf-8").read()
        css_src = io.open(os.path.join(skill_root, "assets", "ambient.css"),
                          encoding="utf-8").read()

        # --- 选择器：三个位置一个都不能少 ---
        check("主标题选择器是 .wb-home-header__title span",
              '".wb-home-header__title span"' in renderer_src)
        check("副标题用的是原生槽位 .wb-home-header__slogan-slot-inner",
              '".wb-home-header__slogan-slot-inner"' in renderer_src)
        check("快捷按钮按 data-show-id 定位（不是按文字）",
              'wb-scene-tabs__pill[data-show-id=' in renderer_src
              and '"home_mode_work"' in renderer_src
              and '"home_mode_code"' in renderer_src)

        # --- 认按钮里的文字 span：必须排除图标 span ---
        # ⚠️ 文字 span 没有 class，只能靠"不是 icon 的那个"来认
        check("认按钮文字时排除了图标 span",
              "wb-scene-tabs__icon" in renderer_src
              and "!span.classList.contains" in renderer_src)
        check("只遍历直接子元素（不会误抓嵌套节点）",
              "[...button.children]" in renderer_src)

        # --- React 抗性：重渲染会冲掉我们的文字，必须重新对齐 ---
        check("MutationObserver 里挂了首页文字重对齐",
              "scheduleHomeText()" in renderer_src
              and "observer.observe" in renderer_src)
        check("重对齐做了防抖（首页挂载会连发几十次 mutation）",
              "homeTextTimer" in renderer_src and "clearTimeout(homeTextTimer)" in renderer_src)

        # --- 防死循环：值没变就不写 ---
        # ⚠️ 我们自己写 DOM 会再触发 observer；无条件写会变成
        #    「写 → 触发 → 再写」的死循环。这是本功能最关键的护栏。
        check("值相同时不写 DOM（防 MutationObserver 死循环）",
              "if (current === custom) return false;" in renderer_src)
        check("还原时也在值相同时提前返回",
              "if (current === original) return false;" in renderer_src)

        # --- 还原原生文案：必须记下被覆盖前的内容 ---
        check("用 WeakMap 记下被覆盖的原生文案",
              "new WeakMap()" in renderer_src and "nativeHomeText.set" in renderer_src)
        check("元素上打了 data-wbas-text 标记",
              "dataset.wbasText" in renderer_src)
        check("清空输入框能立刻还原（不等 React 重渲染）",
              "nativeHomeText.has(element)" in renderer_src)

        # --- 空串语义 = 用原生默认 ---
        check("空串代表「用原生默认」而不是「显示空」",
              'const homeTextState = { title: "", slogan: "", chipWork: "", chipCode: "" };' in renderer_src)

        # --- 持久化 ---
        check("首页文字有独立的存储键",
              "workbuddy-ambient-skin.home-text-v1" in renderer_src)

        # --- 菜单 UI ---
        check("菜单里有 4 个 data-home 输入框",
              renderer_src.count('data-home="') == 4)
        for key, label in [("title", "主标题"), ("slogan", "副标题"),
                           ("chipWork", "按钮一"), ("chipCode", "按钮二")]:
            check("  菜单有「%s」输入框（data-home=%s）" % (label, key),
                  'data-home="%s"' % key in renderer_src)
        check("菜单有「恢复默认」按钮", "hometext-reset" in renderer_src)
        check("菜单有说明文字（留空即恢复原生）",
              "留空即恢复原生文案" in renderer_src)
        check("输入框带 maxlength（防止撑爆布局）",
              'maxlength="40"' in renderer_src and 'maxlength="12"' in renderer_src)
        check("输入框有 aria-label（可访问性）",
              'aria-label="首页主标题"' in renderer_src)

        # --- 边打边生效 ---
        check("输入框 input 事件直接生效（不用点保存）",
              'input.addEventListener("input"' in renderer_src
              and "paintHomeText()" in renderer_src)
        check("失焦时收拾纯空格输入",
              'input.addEventListener("blur"' in renderer_src)
        check("正在打字的输入框不被回灌覆盖",
              "document.activeElement === input" in renderer_src)

        # --- 卸载要还回原生文案 ---
        # ⚠️ 顺序：先还原再置 disposed —— paintHomeText 有 disposed 守卫，
        #    写反了停用皮肤后首页会留着一句自定义文案。
        cleanup_block = renderer_src.split("cleanup() {")[1].split("},")[0] if "cleanup() {" in renderer_src else ""
        check("cleanup 会还原首页文字",
              "setHomeText({ title: \"\", slogan: \"\", chipWork: \"\", chipCode: \"\" })" in cleanup_block)
        check("cleanup 里「还原」在「置 disposed」之前",
              cleanup_block.index("setHomeText(") < cleanup_block.index("disposed = true"))
        check("cleanup 会清掉首页文字的存储键",
              "localStorage.removeItem(homeTextKey)" in cleanup_block)

        # --- 对外接口（CLI / 自检工具要能读写）---
        check("导出了 setHomeText", "setHomeText," in renderer_src)
        check("导出了 getHomeText", "getHomeText:" in renderer_src)
        check("导出了 paintHomeText（供自检触发）", "paintHomeText," in renderer_src)

        # --- CSS：副标题槽位是折叠的，必须显式展开 ---
        # ⚠️ 原生 .wb-home-header__slogan-slot 是 display:grid +
        #    grid-template-rows:0px。只改 height/overflow 没用（实测还是 0），
        #    必须把 grid-template-rows 放开。
        check("CSS 放开了槽位的 grid-template-rows（否则副标题看不见）",
              "grid-template-rows: auto" in css_src)
        check("展开条件绑在 data-wbas-text=slogan 上（用户清空即收回）",
              ':has(> .wb-home-header__slogan-slot-inner[data-wbas-text="slogan"])' in css_src)
        check("副标题做了居中和可读性阴影",
              ".wb-home-header__slogan-slot-inner[data-wbas-text=\"slogan\"]" in css_src
              and "text-shadow" in css_src)
        check("主标题自定义后也补了阴影",
              '.wb-home-header__title [data-wbas-text="title"]' in css_src)
        check("快捷按钮文字超长会被省略号截断（不撑坏按钮）",
              'data-wbas-text^="chip"' in css_src and "text-overflow: ellipsis" in css_src)
    except Exception as error:
        check("首页文字静态检查未抛异常", False, repr(error))

    # ---- [13] 「专家·技能·连接器」页的技能 / 连接器两个 Tab ----
    print("\n[13] 技能 / 连接器 Tab 透明化（规则 29）")
    try:
        skill_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        css_path = os.path.join(skill_root, "assets", "ambient.css")
        css_raw = io.open(css_path, encoding="utf-8").read()

        # 剥掉注释再查选择器 —— 规则 21/29 的注释里都提到过 .um-tab--active，
        # 直接 in 判断会把注释当成规则，误判成"我们改了选中态"。
        def strip_comments(text):
            import re as _re
            return _re.sub(r"/\*.*?\*/", "", text, flags=_re.S)

        css_src = strip_comments(css_raw)

        # --- 两个 Tab 的页面容器必须完全透明 ---
        for cls, why in [("skills-view", "技能 Tab 容器"),
                         ("connector-panel", "连接器 Tab 容器")]:
            check("规则 29 处理了 .%s（%s）" % (cls, why),
                  "." + cls in css_src)
        # 容器那组选择器要同时出现在同一个规则里（一起 transparent）
        container_block = css_src.split(".skills-view")[1].split("}")[0] if ".skills-view" in css_src else ""
        check("技能容器设为 transparent", "background: transparent" in container_block)
        check("技能容器同时清掉 background-color / background-image",
              "background-color: transparent" in container_block
              and "background-image: none" in container_block)

        # --- 卡片走玻璃（和规则 21 的 ec-* 同一套配方）---
        check("规则 29 处理了 .skill-card", ".skill-card" in css_src)
        check("规则 29 处理了 .connector-card", ".connector-card" in css_src)
        card_block = css_src.split(".skill-card")[1].split("}")[0] if ".skill-card" in css_src else ""
        check("卡片用 panel × 0.62 的玻璃配方（和专家页一致）",
              "var(--wbas-panel-opacity) * 0.62" in card_block)
        check("卡片带 backdrop-filter 磨砂",
              "backdrop-filter" in card_block and "-webkit-backdrop-filter" in card_block)
        check("卡片用了 --wbas-surface（跟随主题明暗）",
              "var(--wbas-surface)" in card_block)

        # --- 必须带 !important：卡片底是设计令牌给的，不带压不住 ---
        # 实测：深色下 --wb-bg-card = #242424 = rgb(36,36,36)，浅色下 = #FFFFFF。
        # 用户截图是浅色，所以看到的是白的。不带 !important 会被令牌值盖回去。
        check("容器和卡片都带 !important（否则压不住令牌值）",
              "transparent !important" in container_block
              and "!important" in card_block)

        # --- 明确不该碰的（功能性对比）---
        check("没有给 .um-tab--active 设背景（选中态是功能性对比）",
              ".um-tab--active" not in css_src)
        check("没有动技能图标的白色 logo 底（img 不在规则里）",
              ".skill-card img" not in css_src and ".skill-card-img" not in css_src)

        # --- 规则 21 的专家页不能因此回归 ---
        check("专家页的 .ec-expert-card 规则仍在", ".ec-expert-card" in css_src)
        check("外层 .expert-center-page 仍透明（规则 20）",
              ".expert-center-page" in css_src)

        # --- 注释里要留下实测数据和性能提醒（后来人排错用）---
        check("注释记录了实测的元素/数量/颜色",
              "rgb(36,36,36)" in css_raw and "99" in css_raw and "1,220,900" in css_raw)
        check("注释说明了为什么不用改令牌的老办法",
              "--wb-bg-card" in css_raw)
        check("注释留了性能提醒（卡片多，backdrop-filter 有成本）",
              "backdrop-filter" in css_raw and "滚动发涩" in css_raw)
    except Exception as error:
        check("技能/连接器透明化检查未抛异常", False, repr(error))

    print("\n" + "=" * 66)
    if failures:
        print("失败 %d 项：%s" % (len(failures), failures))
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
