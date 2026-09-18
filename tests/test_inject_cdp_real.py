# -*- coding: utf-8 -*-
"""用真实 Chromium（Chrome/Edge 无头模式）验证 inject.py 的 CDP 客户端。

覆盖 mock 测不到的部分：
  * 真实 Chromium 是否接受"不带 Origin 头"的 WebSocket 握手
  * Runtime.evaluate + awaitPromise 的真实语义
  * 超大表达式（inject.py 实际会发 ~554 KB）能否完整送达
  * 从 renderer.mjs 切出来的 installInRenderer 源码在真实 Chromium 里能否解析成函数
"""
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("inj", os.path.join(SKILL, "scripts", "inject.py"))
inj = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inj)

BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]
PORT = 9351

failures = []


def check(label, ok, detail=""):
    print(("  [OK]   " if ok else "  [失败] ") + label + ("" if ok else "  " + str(detail)))
    if not ok:
        failures.append(label)


def free_port(preferred):
    for port in range(preferred, preferred + 20):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return preferred


def main():
    browser = next((p for p in BROWSERS if os.path.exists(p)), None)
    if not browser:
        print("没找到 Chrome/Edge，跳过（需要真实 Chromium 才能跑）")
        return 0
    port = free_port(PORT)
    profile = tempfile.mkdtemp(prefix="wbas-cdp-test-")
    print("浏览器:", browser)
    print("端口  :", port)
    print("临时配置目录:", profile)

    process = subprocess.Popen(
        [browser, "--headless=new", "--no-first-run", "--no-default-browser-check",
         "--disable-gpu", "--disable-extensions", "--remote-debugging-address=127.0.0.1",
         "--remote-debugging-port=%d" % port, "--user-data-dir=%s" % profile, "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        deadline = time.time() + 30
        up = False
        while time.time() < deadline:
            if inj.cdp_up(port):
                up = True
                break
            time.sleep(0.4)
        print("\n[1] 真实 Chromium 的 CDP 端口")
        check("cdp_up() 探测到端口", up)
        if not up:
            return 1

        print("\n[2] HTTP 目标列表")
        listing = inj.http_get_json("127.0.0.1", port, "/json/list", timeout=5)
        pages = [t for t in listing if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
        check("拿到 page 目标", bool(pages), listing[:1])
        if not pages:
            return 1
        ws_url = pages[0]["webSocketDebuggerUrl"]

        print("\n[3] 真实 WebSocket 握手 + Runtime.evaluate（不带 Origin 头）")
        value = inj.cdp_evaluate(ws_url, "1 + 1", timeout=15)
        check("同步表达式求值", value == 2, repr(value))

        value = inj.cdp_evaluate(ws_url, "(async () => { await new Promise(r => setTimeout(r, 120)); return 'async-ok'; })()", timeout=15)
        check("awaitPromise 生效", value == "async-ok", repr(value))

        value = inj.cdp_evaluate(ws_url, "({ a: 1, b: [2, 3] })", timeout=15)
        check("对象按值返回", value == {"a": 1, "b": [2, 3]}, repr(value))

        print("\n[4] 真实语法错误能被识别")
        try:
            inj.cdp_evaluate(ws_url, "this is not js", timeout=15)
            check("语法错误抛出 CdpError", False, "没有抛异常")
        except inj.CdpError as error:
            check("语法错误抛出 CdpError", True, str(error)[:60])

        print("\n[5] 大表达式传输（inject.py 实际约 554 KB）")
        real_expression = inj.build_install_expression(
            inj.read_css(),
            inj.list_themes([inj.BUNDLED_THEMES_ROOT, inj.user_themes_root()]),
            active_id="")
        check("真实表达式长度 > 500 KB", len(real_expression) > 500 * 1024, len(real_expression))

        # 把整段真实表达式当成字符串常量发过去，验证大帧完整送达
        probe = "(() => { const s = %s; return { len: s.length, head: s.slice(0, 24), tail: s.slice(-24) }; })()" % json.dumps(real_expression)
        info = inj.cdp_evaluate(ws_url, probe, timeout=60)
        check("554 KB 字符串完整送达", bool(info) and info.get("len") == len(real_expression),
              "收到 %s / 期望 %s" % ((info or {}).get("len"), len(real_expression)))
        check("首尾内容一致",
              bool(info) and info.get("head") == real_expression[:24] and info.get("tail") == real_expression[-24:],
              repr(info))

        print("\n[6] installInRenderer 源码在真实 Chromium 里能否解析")
        fn_source = inj.extract_install_source()
        value = inj.cdp_evaluate(ws_url, "typeof (%s)" % fn_source, timeout=30)
        check("切出来的源码是合法函数（typeof=function）", value == "function", repr(value))

        print("\n[7] 完整表达式可被真实 Chromium 解析（不执行，只看语法）")
        value = inj.cdp_evaluate(
            ws_url,
            "(() => { try { new Function('return (' + %s + ')'); return 'parse-ok'; } catch (e) { return 'parse-fail: ' + e.message; } })()"
            % json.dumps(real_expression), timeout=60)
        check("整段注入表达式语法合法", value == "parse-ok", repr(value))
    finally:
        try:
            process.terminate()
            process.wait(timeout=10)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        shutil.rmtree(profile, ignore_errors=True)

    print("\n" + "=" * 66)
    if failures:
        print("失败 %d 项：%s" % (len(failures), failures))
        return 1
    print("真实 Chromium 全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
