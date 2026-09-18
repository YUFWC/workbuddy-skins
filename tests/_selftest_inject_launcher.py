# -*- coding: utf-8 -*-
"""inject_launcher.py 的无头自检。

不建窗口、不碰用户真实配置、不真的启动 WorkBuddy（除非显式 --live）。

覆盖：
  A. 配置读写（默认值、往返、损坏容错、越界窗口位置）
  B. 路径解析（手工 > 自动、手工路径失效要如实报错）
  C. discover() 与 inject.py 的发现结果一致
  D. launch() 的各条分支 —— 用假 exe + 打桩模块，不真启动任何东西
  E. 真启动路径（--live）：对**当前已在运行的**实例做只读判定，
     以及用一个几乎不存在的东西验证超时路径能正常返回
  F. GUI 构造（能建窗口、控件齐全、能销毁）

用法：
    python tests/_selftest_inject_launcher.py
    python tests/_selftest_inject_launcher.py --live    # 额外跑真实端口探测
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
LAUNCHER = os.path.join(SKILL, "scripts", "inject_launcher.py")

fails = []
checks = [0]


def check(name, cond, extra=""):
    checks[0] += 1
    mark = "[OK]  " if cond else "[FAIL]"
    line = "  %s %s" % (mark, name)
    if extra != "":
        line += "  -> %s" % (extra,)
    print(line)
    if not cond:
        fails.append(name)


# ============================================================================
# 0) 隔离环境：配置/日志全部落到临时目录
# ============================================================================
work = tempfile.mkdtemp(prefix="wbas-launcher-selftest-")
os.environ["WORKBUDDY_AMBIENT_LAUNCHER_CONFIG"] = os.path.join(work, "launcher.json")
os.environ["LOCALAPPDATA"] = work      # state_root() 的日志也一起隔离

spec = importlib.util.spec_from_file_location("wbas_launcher", LAUNCHER)
L = importlib.util.module_from_spec(spec)
sys.modules["wbas_launcher"] = L
spec.loader.exec_module(L)

print("=" * 70)
print(" inject_launcher 无头自检")
print("=" * 70)
print("临时工作区：%s" % work)
print("")


# ============================================================================
# A) 配置读写
# ============================================================================
print("[A] 配置读写")

cfg = L.read_config()
check("默认配置结构完整",
      set(cfg) == {"version", "cn", "oversea", "injectAfterLaunch",
                   "closeOnSuccess", "window"}, sorted(cfg))
check("默认开启「启动后注入」", cfg["injectAfterLaunch"] is True)
check("默认开启「成功后自动关闭」", cfg["closeOnSuccess"] is True)
check("默认两个版本都走自动探测",
      cfg["cn"]["auto"] is True and cfg["cn"]["exe"] == "")

cfg["cn"]["exe"] = r"D:\fake\WorkBuddy.exe"
cfg["oversea"]["exe"] = r"D:\fake\WorkBuddyAI.exe"
cfg["injectAfterLaunch"] = False
cfg["window"] = {"x": 120, "y": 80}
check("写配置成功", L.write_config(cfg) is True)
check("配置文件确实落盘", os.path.isfile(L.config_path()))

back = L.read_config()
check("往返：cn 路径", back["cn"]["exe"] == cfg["cn"]["exe"], back["cn"]["exe"])
check("往返：oversea 路径", back["oversea"]["exe"] == cfg["oversea"]["exe"])
check("往返：布尔开关", back["injectAfterLaunch"] is False)
check("往返：关闭开关仍为 True", back["closeOnSuccess"] is True)
check("往返：窗口位置", back["window"] == {"x": 120, "y": 80}, back["window"])
check("写的是 UTF-8 且无 BOM",
      open(L.config_path(), "rb").read(3) != b"\xef\xbb\xbf")
check("写配置没留临时文件",
      not [f for f in os.listdir(work) if f.endswith(".tmp")],
      os.listdir(work))

# 损坏容错：绝不能因为配置坏掉就打不开窗口
with open(L.config_path(), "w", encoding="utf-8") as handle:
    handle.write("{ 这不是合法 JSON ,,, ")
broken = L.read_config()
check("损坏的配置回退到默认值（不抛异常）",
      broken["injectAfterLaunch"] is True and broken["cn"]["exe"] == "")

with open(L.config_path(), "w", encoding="utf-8") as handle:
    json.dump({"cn": "应该是字典不是字符串", "window": {"x": "abc"}}, handle)
weird = L.read_config()
check("字段类型不对时被忽略", weird["cn"]["exe"] == "")
check("非整数窗口位置被忽略", weird["window"]["x"] is None)

# 不存在的配置文件
os.remove(L.config_path())
check("配置文件不存在也能读（返回默认）", L.read_config()["version"] == 1)


# ============================================================================
# B) 路径解析
# ============================================================================
print("")
print("[B] 路径解析")

found = L.discover()
check("discover() 认出了国内版", bool(found["cn"]["exe"]), found["cn"]["exe"])
check("discover() 认出了国际版", bool(found["oversea"]["exe"]),
      found["oversea"]["exe"])
check("discover() 认出了两个变体",
      found["cn"]["exe"].lower().endswith("workbuddy.exe"),
      found["cn"]["exe"])
check("国际版路径以 WorkBuddyAI.exe 结尾",
      found["oversea"]["exe"].lower().endswith("workbuddyai.exe"),
      found["oversea"]["exe"])

# 手工指定的路径 > 自动探测
manual_exe = found["cn"]["exe"]
cfg = L.read_config()
cfg["cn"] = {"exe": manual_exe, "auto": False}
L.write_config(cfg)
exe, why = L.resolve_exe("cn")
check("手工指定的路径被采用", exe == manual_exe, why)
check("来源标为「手工指定」", why == "手工指定", why)

# 手工路径失效 → 明确报错，不要静默退回自动
cfg["cn"] = {"exe": os.path.join(work, "不存在.exe"), "auto": False}
L.write_config(cfg)
exe, why = L.resolve_exe("cn")
check("手工路径失效时返回空", exe == "")
check("并且说明里点出路径不存在", "不存在" in why, why)

# 清空 → 回到自动
cfg["cn"] = {"exe": "", "auto": True}
L.write_config(cfg)
exe, why = L.resolve_exe("cn")
check("清空后回到自动探测", exe == found["cn"]["exe"] and why == "自动探测", why)

# 显式传入 config/found，不读盘（GUI 内部用的就是这个形式）
exe, why = L.resolve_exe("oversea",
                         config={"oversea": {"exe": "", "auto": True}},
                         found=found)
check("显式传参时用传进来的 found", exe == found["oversea"]["exe"], why)

# 未找到安装时要说人话
exe, why = L.resolve_exe("cn", config={"cn": {"exe": "", "auto": True}},
                         found={"cn": {"exe": "", "product": "", "running": 0}})
check("没找到安装时给出提示", exe == "" and "没找到" in why, why)


# ============================================================================
# C) 与 inject.py 的发现结果一致（防止两边各走各的）
# ============================================================================
print("")
print("[C] 与 inject.py 的一致性")

module = L.load_inject()
check("inject.py 加载成功", module is not None, L.inject_error() or "")
if module is not None:
    raw = module.discover_installs()
    by_variant = {}
    for key, (path, variant, product) in raw.items():
        by_variant.setdefault(variant, []).append(path)
    for variant in ("cn", "oversea"):
        expect = by_variant.get(variant) or []
        got = found[variant]["exe"]
        check("discover() 的 %s 来自 inject.py 的安装列表" % variant,
              (not expect) or (got in expect), "%s in %s" % (got, expect))

    # 端口判定必须复用 inject.py，不能自己写一套
    os_exe = found["oversea"]["exe"]
    port = L.port_of(os_exe)
    check("port_of() 与国际版实际状态一致",
          (port is None) or module.cdp_up(port), port)
    if L.running_of(os_exe) > 0:
        check("国际版在跑时应该能拿到 CDP 端口", port is not None, port)
    check("不存在的 exe 拿不到端口",
          L.port_of(os.path.join(work, "nope.exe")) is None)
    check("空路径不炸", L.port_of("") is None and L.running_of("") == 0)


# ============================================================================
# D) launch() 的分支（打桩，不真启动任何进程）
# ============================================================================
print("")
print("[D] launch() 分支（使用打桩模块）")

real_module = module
calls = {"force_quit": 0, "launch_with_cdp": 0, "picked_port": []}


class StubModule(object):
    """替身模块：记录被调用的动作，按脚本返回，绝不碰真实进程。"""

    def __init__(self, live_port=None, ready_after=0.0, quit_error=None):
        self.live_port = live_port
        self.ready_after = ready_after
        self.quit_error = quit_error
        self.started_at = None

    def find_live_port(self, exe, first=9347, tries=12):
        if self.live_port is not None:
            return self.live_port
        if self.started_at is not None and time.time() - self.started_at >= self.ready_after:
            return 9347
        return None

    def pick_port(self, preferred=9347, tries=12):
        calls["picked_port"].append(preferred)
        return preferred

    def running_counts(self):
        return {os.path.normcase(os.path.abspath(fake_exe)): 3}

    def force_quit(self, exe):
        calls["force_quit"] += 1
        if self.quit_error:
            raise RuntimeError(self.quit_error)
        return {"wasRunning": True, "stopped": True, "pids": [111, 222]}

    def launch_with_cdp(self, exe, port=9347):
        calls["launch_with_cdp"] += 1
        self.started_at = time.time()
        return {"pid": 999, "port": port, "executable": exe}


fake_exe = os.path.join(work, "WorkBuddyAI.exe")
with open(fake_exe, "wb") as handle:
    handle.write(b"MZ")            # 只需要"文件存在"

real = L.load_inject
real_err = L._inject_error

# D1: 已经带 CDP 在跑 → 直接成功，不能重启
calls.update({"force_quit": 0, "launch_with_cdp": 0})
L._inject = StubModule(live_port=9347)
L._inject_error = None
result = L.launch(fake_exe, timeout=3.0)
check("D1 已在运行时不重启 force_quit", calls["force_quit"] == 0)
check("D1 已在运行时不重新拉起", calls["launch_with_cdp"] == 0)
check("D1 判定为成功", result["ok"] is True)
check("D1 标记 alreadyUp", result["alreadyUp"] is True)
check("D1 报出端口", result["port"] == 9347)

# D2: 没在跑 → 先关旧的，再带 CDP 启动，CDP 通后成功
calls.update({"force_quit": 0, "launch_with_cdp": 0})
L._inject = StubModule(live_port=None, ready_after=0.0)
result = L.launch(fake_exe, timeout=5.0)
check("D2 先关掉旧实例", calls["force_quit"] == 1)
check("D2 带 CDP 拉起", calls["launch_with_cdp"] == 1)
check("D2 判定为成功", result["ok"] is True)
check("D2 alreadyUp 为假", result["alreadyUp"] is False)
check("D2 带回旧进程 pid 列表", result["pids"] == [111, 222], result["pids"])

# D3: 关不掉旧实例 → 失败，且原因里要有真实错误
calls.update({"force_quit": 0, "launch_with_cdp": 0})
L._inject = StubModule(live_port=None, quit_error="杀不掉 pid 42")
result = L.launch(fake_exe, timeout=5.0)
check("D3 关不掉时判定为失败", result["ok"] is False)
check("D3 原因包含真实错误", "杀不掉 pid 42" in result["reason"], result["reason"])
check("D3 不该继续启动", calls["launch_with_cdp"] == 0)

# D4: CDP 一直不通 → 超时失败（超时要短，不然自检要等 90 秒）
calls.update({"force_quit": 0, "launch_with_cdp": 0})
L._inject = StubModule(live_port=None, ready_after=9999.0)
started = time.time()
result = L.launch(fake_exe, timeout=1.5)
elapsed = time.time() - started
check("D4 超时后判定为失败", result["ok"] is False)
check("D4 确实等满了超时时间（1.5~4s）", 1.4 <= elapsed <= 4.0, "%.2fs" % elapsed)
check("D4 原因里提示了可能的原因", "调试端口" in result["reason"]
      or "启动失败" in result["reason"], result["reason"])

# D5: 路径不存在 → 立刻失败，不该调用任何动作
calls.update({"force_quit": 0, "launch_with_cdp": 0})
L._inject = StubModule(live_port=None)
result = L.launch(os.path.join(work, "根本没有这个.exe"), timeout=1.0)
check("D5 路径不存在时立刻失败", result["ok"] is False)
check("D5 不该关闭旧进程", calls["force_quit"] == 0)
check("D5 不该拉起进程", calls["launch_with_cdp"] == 0)
check("D5 原因点明文件不存在", "不存在" in result["reason"], result["reason"])

# D6: 空路径
result = L.launch("", timeout=1.0)
check("D6 空路径立刻失败", result["ok"] is False)

# D7: 用户取消 —— 启动等待中途取消要能退出
L._inject = StubModule(live_port=None, ready_after=9999.0)
flag = {"stop": False}
started = time.time()


def cancel_soon():
    time.sleep(0.6)
    flag["stop"] = True


import threading
threading.Thread(target=cancel_soon, daemon=True).start()
result = L.launch(fake_exe, timeout=30.0, cancel=lambda: flag["stop"])
check("D7 取消后立刻返回", time.time() - started < 5.0,
      "%.2fs" % (time.time() - started))
check("D7 取消判定为失败", result["ok"] is False)
check("D7 原因标明已取消", "取消" in result["reason"], result["reason"])

# D8: 进度与日志回调真的被调用
events = []
L._inject = StubModule(live_port=None, ready_after=0.0)
L.launch(fake_exe, timeout=5.0, log_fn=lambda line: events.append(("log", line)),
         on_progress=lambda label, pct=None: events.append(
             ("progress", label, pct)))
logs = [item[1] for item in events if item[0] == "log"]
check("D8 有日志产出", bool(logs), logs)
progress = [(item[1], item[2]) for item in events if item[0] == "progress"]
check("D8 有进度产出", bool(progress), progress)
percentages = [pct for _, pct in progress]
check("D8 进度最后到 100", 100 in percentages, percentages)
check("D8 进度没超过 100", all(p is None or 0 <= p <= 100 for p in percentages),
      percentages)
check("D8 中间有不确定态进度（None，驱动滚动条）",
      any(p is None for p in percentages), percentages)

# 还原真身
L.load_inject = real
L._inject = real_module
L._inject_error = real_err


# ============================================================================
# E) run_inject 的接口（打桩 main）
# ============================================================================
print("")
print("[E] run_inject 接口")

captured = {}

if real_module is not None:
    real_main = real_module.main
    real_module.main = lambda argv=None: (captured.update({"argv": argv}), 0)[1]
    code, error = L.run_inject("cn", log_fn=lambda line: None)
    check("run_inject 成功时返回 0", code == 0, (code, error))
    check("run_inject 传了 --target cn",
          captured.get("argv") == ["--target", "cn"], captured.get("argv"))

    real_module.main = lambda argv=None: (captured.update({"argv": argv}), 7)[1]
    code, error = L.run_inject("oversea", log_fn=lambda line: None)
    check("run_inject 透传失败退出码", code == 7, code)
    check("run_inject 传了 --target oversea",
          captured.get("argv") == ["--target", "oversea"], captured.get("argv"))

    real_module.main = lambda argv=None: (captured.update({"argv": argv}), 0)[1]
    code, error = L.run_inject("cn", log_fn=lambda line: None, video_off=True)
    check("run_inject 支持 --video-off",
          captured.get("argv") == ["--target", "cn", "--video-off"],
          captured.get("argv"))
    code, error = L.run_inject("cn", log_fn=lambda line: None, video="D:/bg.mp4")
    check("run_inject 支持 --video",
          captured.get("argv") == ["--target", "cn", "--video", "D:/bg.mp4"],
          captured.get("argv"))

    # stdout 必须被还原 —— 不还原会把后续所有 print 吞掉
    def raise_main(argv=None):
        raise RuntimeError("打桩抛异常")
    real_module.main = raise_main
    marker = sys.stdout
    code, error = L.run_inject("cn", log_fn=lambda line: None)
    check("run_inject 接住异常返回 99", code == 99, code)
    check("run_inject 还原了 sys.stdout", sys.stdout is marker)

    # SystemExit 也要接住（inject.py 内部有 sys.exit 路径）
    def exit_main(argv=None):
        raise SystemExit(5)
    real_module.main = exit_main
    code, error = L.run_inject("cn", log_fn=lambda line: None)
    check("run_inject 接住 SystemExit 并取到退出码", code == 5, code)

    real_module.main = real_main


# ============================================================================
# F) GUI 构造与销毁（能建窗口即算通过；窗口不会常驻）
# ============================================================================
print("")
print("[F] GUI 构造")

gui_ok = True
try:
    import tkinter as tk
except ImportError:
    gui_ok = False
    check("tkinter 可用（当前解释器）", False, sys.executable)

if gui_ok:
    import tkinter.ttk as ttk
    from tkinter import messagebox

    # 屏蔽弹窗，否则会阻塞
    messagebox.showerror = lambda *a, **k: None
    messagebox.showwarning = lambda *a, **k: None
    messagebox.showinfo = lambda *a, **k: None
    messagebox.askyesno = lambda *a, **k: True

    real_mainloop = tk.Tk.mainloop
    captured_root = {}

    def fake_mainloop(self, *a, **k):
        captured_root["root"] = self
        self.update_idletasks()
        self.update()
        # 不真的跑事件循环；构造完就交还给下面断言
    tk.Tk.mainloop = fake_mainloop

    try:
        rc = L.launch_gui()
        check("launch_gui() 正常返回", rc == 0, rc)
        root = captured_root.get("root")
        check("窗口被构造出来", root is not None)
        if root is not None:
            def walk(widget):
                yield widget
                for child in widget.winfo_children():
                    yield from walk(child)

            widgets = list(walk(root))
            buttons = [w for w in widgets if w.winfo_class() == "Button"]
            entries = [w for w in widgets if w.winfo_class() == "Entry"]
            texts = [w for w in widgets if w.winfo_class() == "Text"]
            checks_ = [w for w in widgets if w.winfo_class() == "Checkbutton"]
            labels = [w for w in widgets if w.winfo_class() == "Label"]

            button_texts = [str(w.cget("text")) for w in buttons]
            check("有「启动国内版」按钮",
                  any("启动国内版" in t for t in button_texts), button_texts)
            check("有「启动国际版」按钮",
                  any("启动国际版" in t for t in button_texts), button_texts)
            check("有两个路径输入框（国内/国际）", len(entries) == 2, len(entries))
            check("有浏览按钮", any("浏览" in t for t in button_texts))
            check("有自动按钮", any("自动" in t for t in button_texts))
            check("有取消按钮", any("取消" in t for t in button_texts))
            check("有一个日志区", len(texts) == 1, len(texts))
            check("有两个选项复选框", len(checks_) == 2, len(checks_))
            check("有进度条",
                  any(w.winfo_class() == "TProgressbar" for w in widgets))
            check("标题正确",
                  "快捷启动器" in str(root.title()), root.title())

            option_texts = [str(w.cget("text")) for w in checks_]
            check("选项含「自动注入皮肤」",
                  any("注入" in t for t in option_texts), option_texts)
            check("选项含「自动关闭本窗口」",
                  any("关闭" in t for t in option_texts), option_texts)

            option_vars = dict((str(w.cget("text")), w) for w in checks_)
            inject_box = [w for w in checks_ if "注入" in str(w.cget("text"))]
            close_box = [w for w in checks_ if "关闭" in str(w.cget("text"))]
            # Checkbutton 本身没有 .get()，值在它的 textvariable 上
            check("「自动关闭」默认勾选",
                  bool(close_box
                       and str(close_box[0].cget("variable"))
                       and bool(close_box[0].getvar(
                           str(close_box[0].cget("variable"))))),
                  close_box[0].cget("variable") if close_box else "无控件")
            check("「自动注入」默认勾选",
                  bool(inject_box
                       and bool(inject_box[0].getvar(
                           str(inject_box[0].cget("variable"))))),
                  inject_box[0].cget("variable") if inject_box else "无控件")

            # 路径框应该被自动填上探测结果或配置值（刷新提示后）
            entry_values = [w.get() for w in entries]
            check("两个输入框都能取值（不抛异常）", len(entry_values) == 2,
                  entry_values)

            # 有没有把 hint 文案填出来
            hint_texts = [str(w.cget("text")) for w in labels
                          if "CDP" in str(w.cget("text"))
                          or "自动探测" in str(w.cget("text"))
                          or "未找到" in str(w.cget("text"))
                          or "不存在" in str(w.cget("text"))]
            check("路径提示已渲染（来源/进程/CDP）", bool(hint_texts), hint_texts)

            # 窗口尺寸合理、在屏幕内
            root.update_idletasks()
            width = root.winfo_width()
            height = root.winfo_height()
            check("窗口尺寸合理", 500 <= width <= 1400 and 400 <= height <= 1100,
                  "%dx%d" % (width, height))
            check("窗口位置在屏幕内",
                  0 <= root.winfo_x() < root.winfo_screenwidth()
                  and 0 <= root.winfo_y() < root.winfo_screenheight(),
                  (root.winfo_x(), root.winfo_y()))

            # 关窗要能存下位置。
            # 走真实路径：调 WM_DELETE_WINDOW 上挂的处理函数，而不是直接 destroy
            # （直接 destroy 会绕过 protocol 回调，测不出 on_close 有没有干活）。
            protocol_handler = root.protocol("WM_DELETE_WINDOW")
            check("关窗协议已注册", bool(protocol_handler), protocol_handler)
            if callable(protocol_handler):
                # Tk 的 protocol() 不带第二参时返回已注册的命令名，
                # 这里直接触发「窗口被关」这条链路
                root.tk.call(protocol_handler)
            saved = L.read_config()
            check("关窗后窗口位置被记下",
                  isinstance(saved["window"].get("x"), int)
                  and isinstance(saved["window"].get("y"), int),
                  saved["window"])
            check("关窗后选项也被存下",
                  isinstance(saved.get("closeOnSuccess"), bool),
                  saved.get("closeOnSuccess"))
            if root.winfo_exists():
                root.destroy()
    finally:
        tk.Tk.mainloop = real_mainloop


# ============================================================================
# G) --live：只读地确认真实端口探测（可选）
# ============================================================================
if "--live" in sys.argv and real_module is not None:
    print("")
    print("[G] 真实端口探测（只读）")
    for variant in ("cn", "oversea"):
        exe, why = L.resolve_exe(variant)
        if not exe:
            check("%s 路径解析" % variant, False, why)
            continue
        count = L.running_of(exe)
        port = L.port_of(exe)
        check("%s 状态可读" % variant, True,
              "进程 %d 个，CDP %s" % (count, port))
        if count > 0:
            check("%s 在跑时端口判定与 cdp_up 一致" % variant,
                  (port is None) or real_module.cdp_up(port), port)


# ============================================================================
# 收尾
# ============================================================================
shutil.rmtree(work, ignore_errors=True)

print("")
print("=" * 70)
if fails:
    print("失败 %d / %d 项：" % (len(fails), checks[0]))
    for item in fails:
        print("  - %s" % item)
    sys.exit(1)
print("全部 %d 项自检通过 ✅" % checks[0])
