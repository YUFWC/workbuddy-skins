#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""WorkBuddy 美化 · 快捷启动器（Python GUI）

国内版 / 国际版各配一条 exe 路径，点一下启动，CDP 就绪后本窗口自动关闭。

两条必须记住的设计约束：

1. **不复制 inject.py 的逻辑** —— 用 importlib 按路径加载它，复用
   discover_installs / read_variant / find_live_port / cdp_up /
   launch_with_cdp / force_quit / pick_port。inject.py 改了这边自动跟随。
2. **「启动成功」= CDP 端口真的通了**，不是「Popen 没报错」。后续注入和自愈
   全靠这个端口，不通等于白启动。判据用 find_live_port —— 它还顺带确认端口
   **属于目标安装**（两版共存时 9347/9348 分属不同应用）。

GUI 需要 tkinter，而托管 Python 是精简版不带 tkinter，必须用系统 Python 跑
（见 启动美化.bat）。核心逻辑是不碰 Tk 的纯函数，便于无头自检。

    python inject_launcher.py              # 打开 GUI
    python inject_launcher.py --list       # CLI：打印探测到的安装
    python inject_launcher.py --selftest   # 无头自检
"""
import ctypes
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

# ============================================================================
# 零、常量与路径
# ============================================================================
def _bundle_root():
    """依赖文件的根目录（打包后 / 未打包两种情形）。

    打包成 exe 后 `__file__` 指向 _MEIxxxx 临时解压目录、`sys.executable` 指向
    exe 本身，两者都不能拿来推同级文件。PyInstaller 把解压根写在 sys._MEIPASS，
    数据文件按原相对结构放在那里，直接用即可。

    打包时刻意保持 `<root>/scripts/inject.py` + `<root>/assets/**` 这个布局：
    inject.py 自己用 `__file__` 往上推两级定位 assets/，布局一致它一行都不用改。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return meipass
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


BUNDLE_ROOT = _bundle_root()
SCRIPTS_DIR = os.path.join(BUNDLE_ROOT, "scripts")
INJECT_PY = os.path.join(SCRIPTS_DIR, "inject.py")
FROZEN = bool(getattr(sys, "frozen", False))

CONFIG_NAME = "launcher.json"

VARIANT_CN = "cn"
VARIANT_OVERSEA = "oversea"
VARIANT_LABELS = {
    VARIANT_CN: "国内版",
    VARIANT_OVERSEA: "国际版",
}

# 两个版本的默认 CDP 端口。inject.py 的 DEFAULT_PORT 是 9347，
# 但两个版本共存时它会给第二个让位到 9348 —— 靠 find_live_port 实际探测。
DEFAULT_PORT = 9347
PORT_SCAN = 12          # 和 inject.py 的 pick_port/find_live_port 对齐

WAIT_TIMEOUT = 90.0     # 等 CDP 就绪的总时长，和 inject.py 的 wait_for_renderers 一致
POLL_INTERVAL = 0.5


def state_root():
    """状态目录，和 inject.py 用同一个（日志/配置都在这里，方便一起清理）。"""
    try:
        base = (os.environ.get("LOCALAPPDATA")
                or os.path.join(os.path.expanduser("~"), "AppData", "Local"))
        root = os.path.join(base, "WorkBuddyAmbientSkin")
        os.makedirs(root, exist_ok=True)
        return root
    except Exception:
        return os.path.expanduser("~")


def config_path():
    """配置文件路径。

    `WORKBUDDY_AMBIENT_LAUNCHER_CONFIG` 可以覆盖 —— 自检靠它指向临时目录，
    绝不碰用户真实配置。
    """
    override = os.environ.get("WORKBUDDY_AMBIENT_LAUNCHER_CONFIG")
    if override:
        return os.path.abspath(override)
    return os.path.join(state_root(), CONFIG_NAME)


def log_path():
    return os.path.join(state_root(), "launcher.log")


def error_log_path():
    return os.path.join(state_root(), "launcher-error.log")


def log(message):
    text = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), message)
    try:
        with open(log_path(), "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    except Exception:
        pass
    return text


def log_exception(exc_type=None, exc=None, tb=None):
    """把异常落盘。

    打包成 --windowed 后没有控制台，stderr 无处可去，异常一抛程序就凭空消失，
    用户只会看到"双击没反应"。必须自己写盘。
    """
    import traceback
    if exc_type is None:
        exc_type, exc, tb = sys.exc_info()
    try:
        with open(error_log_path(), "a", encoding="utf-8") as handle:
            handle.write("\n=== %s ===\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            traceback.print_exception(exc_type, exc, tb, file=handle)
    except Exception:
        pass


# ============================================================================
# 一、加载 inject.py（不复制它的逻辑）
# ============================================================================
_inject = None
_inject_error = None


def load_script(filename):
    """按路径加载 scripts/ 下的脚本，返回 (模块, 错误说明)。

    为什么不用 `import inject`：脚本可能在任意 CWD 下被拉起，`scripts/` 未必在
    sys.path 上；而且 inject.py 这名字太通用，容易和别的包撞。
    """
    path = os.path.join(SCRIPTS_DIR, filename)
    if not os.path.isfile(path):
        return None, "找不到 %s：%s" % (filename, path)
    key = "wbas_" + os.path.splitext(filename)[0]
    if key in sys.modules:
        return sys.modules[key], ""
    try:
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)
        spec = importlib.util.spec_from_file_location(key, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module     # 先注册，被加载的脚本内部有同目录导入
        spec.loader.exec_module(module)
        # space_doctor.py 采用 `import inject as inj`，动态加载后给它一个
        # 标准模块别名；否则 --doctor 只在打包环境下会 ImportError。
        if filename == "inject.py":
            sys.modules["inject"] = module
        return module, ""
    except Exception as error:
        return None, "加载 %s 失败：%s: %s" % (filename, type(error).__name__, error)


def load_inject():
    """加载 inject.py，返回模块或 None（结果缓存）。"""
    global _inject, _inject_error
    if _inject is not None or _inject_error is not None:
        return _inject
    _inject, _inject_error = load_script("inject.py")
    return _inject


def inject_error():
    return _inject_error


# ============================================================================
# 二、配置读写
# ============================================================================
DEFAULT_CONFIG = {
    "version": 1,
    "cn": {"exe": "", "auto": True},
    "oversea": {"exe": "", "auto": True},
    "injectAfterLaunch": True,      # 启动成功后顺手注入皮肤
    "closeOnSuccess": True,         # 启动成功后自动关闭启动器
    "window": {"x": None, "y": None},
}


def read_config():
    """读配置；缺失/损坏都回退到默认值（绝不因为配置坏掉就打不开）。"""
    data = json.loads(json.dumps(DEFAULT_CONFIG))       # 深拷贝
    path = config_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            stored = json.load(handle)
    except Exception:
        return data
    if not isinstance(stored, dict):
        return data
    for key in ("injectAfterLaunch", "closeOnSuccess"):
        if isinstance(stored.get(key), bool):
            data[key] = stored[key]
    for key in (VARIANT_CN, VARIANT_OVERSEA):
        block = stored.get(key)
        if isinstance(block, dict):
            if isinstance(block.get("exe"), str):
                data[key]["exe"] = block["exe"].strip()
            if isinstance(block.get("auto"), bool):
                data[key]["auto"] = block["auto"]
    window = stored.get("window")
    if isinstance(window, dict):
        for axis in ("x", "y"):
            value = window.get(axis)
            if isinstance(value, int) and not isinstance(value, bool):
                data["window"][axis] = value
    return data


def write_config(data):
    """原子写入配置（临时文件 + os.replace，同目录防跨卷失败）。"""
    path = config_path()
    directory = os.path.dirname(path) or "."
    try:
        os.makedirs(directory, exist_ok=True)
    except Exception:
        return False
    fd = tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            json.dump(data, handle, ensure_ascii=False, indent=2)
        last = None
        for attempt in range(4):        # 防瞬时占用（杀软/索引器）
            try:
                os.replace(tmp, path)
                return True
            except PermissionError as error:
                last = error
                time.sleep(0.12 * (attempt + 1))
        raise last
    except Exception as error:
        log("写配置失败：%s: %s" % (type(error).__name__, error))
        return False
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


# ============================================================================
# 三、安装发现与路径解析
# ============================================================================
def discover():
    """{变体: {"exe": 路径, "product": 产品名, "running": 进程数}}。

    数据来自 inject.py —— 它已经覆盖了注册表 DisplayIcon、常见安装路径、
    正在运行的进程三条路，还负责读 product.json 判变体。
    """
    module = load_inject()
    out = {
        VARIANT_CN: {"exe": "", "product": "", "running": 0},
        VARIANT_OVERSEA: {"exe": "", "product": "", "running": 0},
    }
    if module is None:
        return out
    try:
        installs = module.discover_installs()
        counts = module.running_counts()
    except Exception as error:
        log("发现安装失败：%s: %s" % (type(error).__name__, error))
        return out
    for key, (path, variant, product) in installs.items():
        slot = out.get(variant)
        if slot is None:
            continue
        running = counts.get(key, 0)
        # 同变体有多个安装时，优先留正在运行的那个，其次留进程多的
        if not slot["exe"] or running > slot["running"]:
            slot.update({"exe": path, "product": product, "running": running})
    return out


def resolve_exe(variant, config=None, found=None):
    """决定这个变体最终要用哪个 exe。

    优先级：配置里手工指定的（且文件存在）> 自动探测到的。
    返回 (exe 路径或 "", 来源说明)。
    说明文字会显示在界面上，用户一眼能看出"用的是不是我想要的那个"。
    """
    config = config if config is not None else read_config()
    found = found if found is not None else discover()
    manual = (config.get(variant) or {}).get("exe") or ""
    if manual:
        if os.path.isfile(manual):
            return manual, "手工指定"
        return "", "手工指定的路径不存在：%s" % manual
    auto = (found.get(variant) or {}).get("exe") or ""
    if auto:
        return auto, "自动探测"
    return "", "没找到%s的安装" % VARIANT_LABELS.get(variant, variant)


def running_of(exe):
    """这个 exe 当前有多少个进程。"""
    module = load_inject()
    if module is None or not exe:
        return 0
    try:
        return module.running_counts().get(os.path.normcase(os.path.abspath(exe)), 0)
    except Exception:
        return 0


def port_of(exe):
    """这个安装的 CDP 端口，没在跑就返回 None。"""
    module = load_inject()
    if module is None or not exe:
        return None
    try:
        return module.find_live_port(exe, DEFAULT_PORT, PORT_SCAN)
    except Exception:
        return None


# ============================================================================
# 四、启动与等待
# ============================================================================
def launch(exe, port=None, timeout=WAIT_TIMEOUT, log_fn=None,
           on_progress=None, cancel=None):
    """带 CDP 端口启动 WorkBuddy，并**等到 CDP 真的可用**为止。

    参数
    ----
    exe       要启动的 exe 完整路径
    port      指定端口；None 表示自动挑（默认 9347 起）
    timeout   等 CDP 的上限秒数
    log_fn    收日志文本的回调（可选）
    on_progress(阶段文字[, 百分比])  进度回调（可选）
    cancel    () -> bool，返回 True 时提前放弃（用户点了取消）

    返回 dict：{"ok": bool, "alreadyUp": bool, "port": int|None,
                "pids": [...], "reason": str}

    为什么先 force_quit
    -------------------
    被启动的实例必须带 `--remote-debugging-port` 才有 CDP。如果这个版本
    已经在跑（当初是普通双击起来的，没带调试端口），直接再 Popen 一个
    只会被单实例锁挡掉 —— 表现为"点了没反应"。所以先按 inject.py 的
    老办法把它关掉再带参启动。inject.py 的 force_quit 会等到进程族彻底
    退出并确认没有残留，这一步不能省。

    但**已经带 CDP 在跑**的情况要跳过重启（reload 会丢当前对话上下文），
    直接算成功 —— 和 inject.py 热注入时的判断一致。
    """
    def say(text, *args):
        message = text % args if args else text
        if log_fn:
            log_fn(message)
        log(message)

    def tick(text, percent=None):
        if on_progress:
            on_progress(text, percent)

    def cancelled():
        return bool(cancel and cancel())

    module = load_inject()
    if module is None:
        return {"ok": False, "alreadyUp": False, "port": None, "pids": [],
                "reason": _inject_error or "inject.py 不可用"}

    if not exe or not os.path.isfile(exe):
        return {"ok": False, "alreadyUp": False, "port": None, "pids": [],
                "reason": "可执行文件不存在：%s" % exe}

    # ---- 1) 已经在带 CDP 跑着？直接成功，别动它 --------------------------
    live = port_of(exe)
    if live is not None:
        say("%s 已在端口 %d 上开着 CDP → 无需重启", os.path.basename(exe), live)
        tick("已在运行（CDP %d）" % live, 100)
        return {"ok": True, "alreadyUp": True, "port": live, "pids": [],
                "reason": "已经在运行"}

    # ---- 2) 挑端口 ------------------------------------------------------
    target_port = port
    if target_port is None:
        try:
            target_port = module.pick_port(DEFAULT_PORT, PORT_SCAN)
        except Exception:
            target_port = DEFAULT_PORT
    say("将使用 CDP 端口 %d", target_port)

    # ---- 3) 关掉可能存在的旧实例 ----------------------------------------
    pids = []
    was_running = running_of(exe)
    if was_running:
        tick("正在关闭旧实例…", None)
        say("检测到 %d 个进程，先关闭（否则单实例锁会挡住新实例）", was_running)
        try:
            result = module.force_quit(exe)
            pids = list(result.get("pids") or [])
            say("已关闭：%s", pids if pids else "（本来就没在运行）")
        except Exception as error:
            # 杀不掉就把真实原因说出来，别让用户对着"启动中…"干等
            return {"ok": False, "alreadyUp": False, "port": None, "pids": pids,
                    "reason": "关不掉旧实例：%s" % error}
    if cancelled():
        return {"ok": False, "alreadyUp": False, "port": None, "pids": pids,
                "reason": "已取消"}

    # ---- 4) 带 CDP 启动 -------------------------------------------------
    tick("正在启动…", None)
    try:
        info = module.launch_with_cdp(exe, target_port)
    except OSError as error:
        return {"ok": False, "alreadyUp": False, "port": None, "pids": pids,
                "reason": "启动失败：%s" % error}
    say("已拉起进程 pid=%s，等待 CDP 就绪…", info.get("pid"))

    # ---- 5) 等 CDP 真的可用 ---------------------------------------------
    #    判据是 find_live_port：既要端口通，还要确认端口**属于这个安装**。
    #    两个版本共存时 9347/9348 分属不同应用，只看端口通会误判。
    started = time.time()
    ready_port = None
    while time.time() - started < timeout:
        if cancelled():
            return {"ok": False, "alreadyUp": False, "port": None, "pids": pids,
                    "reason": "已取消"}
        try:
            ready_port = module.find_live_port(exe, DEFAULT_PORT, PORT_SCAN)
        except Exception:
            ready_port = None
        if ready_port is not None:
            break
        elapsed = time.time() - started
        tick("等待 CDP 就绪… %.0fs" % elapsed, min(95.0, elapsed / timeout * 100.0))
        time.sleep(POLL_INTERVAL)

    if ready_port is None:
        say("等待超时：%d 秒内 CDP 端口没通", int(timeout))
        return {"ok": False, "alreadyUp": False, "port": None, "pids": pids,
                "reason": "等了 %d 秒，CDP 端口仍未就绪。" % int(timeout)
                          + "可能是启动失败，或安全软件拦了本地调试端口。"
                            "可以先手动打开 WorkBuddy 确认它能正常启动。"}

    say("CDP 就绪（端口 %d）✅", ready_port)
    tick("启动成功（CDP %d）" % ready_port, 100)
    return {"ok": True, "alreadyUp": False, "port": ready_port, "pids": pids,
            "reason": "启动成功"}


def run_inject(variant, log_fn=None, on_progress=None, video=None, video_off=False):
    """注入皮肤 —— 直接调 inject.main()，不 fork 子进程。

    为什么不用 subprocess 跑 `inject.py --target xx`：
      * 同进程调用省掉一次解释器冷启动，而且日志能直接接到界面上；
      * inject.main() 已经是「返回退出码」的干净接口，正适合当库调。
    但它会**重新检查一遍端口并可能重启**。这没问题：刚启动完 CDP 已经通，
    它会走「热注入，不重启」分支（也就是 already_up 那条路）。
    """
    module = load_inject()
    if module is None:
        return 2, _inject_error or "inject.py 不可用"

    argv = ["--target", variant]
    if video_off:
        argv.append("--video-off")
    elif video:
        argv += ["--video", video]

    if on_progress:
        on_progress("正在注入皮肤…", None)

    # inject.py 的 main() 会往 stdout 打日志；这里临时接管，把每行转到界面
    import io

    class Tee(io.TextIOBase):
        def __init__(self, sink):
            self._sink = sink
            self._buf = ""

        def write(self, text):
            if not text:
                return 0
            self._buf += text
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if line.strip() and log_fn:
                    log_fn(line.rstrip())
                    log(line.rstrip())
            return len(text)

        def flush(self):
            if self._buf.strip() and log_fn:
                log_fn(self._buf.strip())
            self._buf = ""

    real_stdout = sys.stdout
    tee = Tee(log_fn)
    try:
        sys.stdout = tee
        try:
            code = module.main(argv)
        finally:
            tee.flush()
    except SystemExit as error:            # main() 里可能有 sys.exit 路径
        code = error.code if isinstance(error.code, int) else 1
    except Exception as error:
        if log_fn:
            log_fn("[异常] %s: %s" % (type(error).__name__, error))
        code = 99
    finally:
        sys.stdout = real_stdout
    return code, ""


# ============================================================================
# 五、CLI 入口（无头，方便脚本化验证）
# ============================================================================
def cli_list():
    found = discover()
    print("配置：%s" % config_path())
    print("")
    module = load_inject()
    if module is None:
        print("[失败] %s" % _inject_error)
        return 2
    print("inject.py：%s" % INJECT_PY)
    print("")
    for variant in (VARIANT_CN, VARIANT_OVERSEA):
        exe, why = resolve_exe(variant)
        slot = found.get(variant) or {}
        print("%-6s %s" % (VARIANT_LABELS[variant], exe or "（未找到）"))
        if exe:
            port = port_of(exe)
            print("        来源 %s | 进程 %d 个 | CDP %s"
                  % (why, running_of(exe),
                     ("端口 %d" % port) if port else "未开启"))
        else:
            print("        来源 %s" % why)
    return 0


# ============================================================================
# 六、GUI
# ============================================================================
# 配色：WorkBuddy 本身是深色优先的应用，这里也走深色，
# 和技能里的 ambient.css / 深色主题保持一致。
C_BG = "#1e1f22"
C_PANEL = "#2b2d30"
C_INPUT = "#3c3f41"
C_BORDER = "#4a4d51"
C_FG = "#dfe1e5"
C_FG_DIM = "#9aa0a6"
C_ACCENT = "#3574f0"
C_ACCENT_HOVER = "#4c8dff"
C_CN = "#2f9e6e"          # 国内版按钮 —— 绿色
C_CN_HOVER = "#3fb37f"
C_OS = "#7b5cd6"          # 国际版按钮 —— 紫色
C_OS_HOVER = "#8f72e6"
C_OK = "#4caf72"
C_WARN = "#e0a33e"
C_ERR = "#e05c5c"

FONT_UI = ("Microsoft YaHei UI", 10)
FONT_BOLD = ("Microsoft YaHei UI", 10, "bold")
FONT_SMALL = ("Microsoft YaHei UI", 9)
FONT_TITLE = ("Microsoft YaHei UI", 14, "bold")
FONT_MONO = ("Consolas", 9)

# ⚠️ 等宽字体（Consolas / Cascadia Mono）**没有中文字形**。
# Tk 会逐字符回退，但回退出来的中文经常渲染成乱码 —— 实测截图里
# 日志区的「国内版：D:\...\WorkBuddy.exe」整行变成了西里尔字母样的鬼画符，
# 而旁边用 Microsoft YaHei UI 的标签完全正常。
# 所以日志区必须挑一个**含中文**的字体：
#   1) 优先真正的 CJK 等宽字体（本机没装，但别人机器上可能有）
#   2) 退到 Microsoft YaHei UI —— 不是等宽，但中英文都正常，可读性优先
_MONO_CANDIDATES = (
    "Sarasa Mono SC", "Noto Sans Mono CJK SC", "Noto Sans Mono CJK",
    "Microsoft YaHei UI", "Microsoft YaHei", "SimSun",
)


def resolve_mono_font(size=9):
    """挑一个能正常显示中文的日志字体。必须在建 Tk 之后调用。"""
    try:
        import tkinter.font as tkfont
        families = set(tkfont.families())
    except Exception:
        return ("Consolas", size)
    for name in _MONO_CANDIDATES:
        if name in families:
            return (name, size)
    return ("Consolas", size)


def make_button(parent, text, command, kind="normal", width=None, font=None):
    """带悬停的 tk.Button。kind: normal / primary / cn / oversea / ghost"""
    palette = {
        "normal": (C_INPUT, C_FG, C_BORDER),
        "primary": (C_ACCENT, "#ffffff", C_ACCENT_HOVER),
        "cn": (C_CN, "#ffffff", C_CN_HOVER),
        "oversea": (C_OS, "#ffffff", C_OS_HOVER),
        "ghost": (C_PANEL, C_FG_DIM, C_INPUT),
    }[kind]
    bg, fg, hover = palette
    button = _tk_button(parent, text, command, bg, fg, hover, width, font)
    return button


def _tk_button(parent, text, command, bg, fg, hover, width, font):
    import tkinter as tk
    kwargs = dict(text=text, command=command, bg=bg, fg=fg,
                  activebackground=hover, activeforeground=fg,
                  relief="flat", bd=0, padx=14, pady=8,
                  font=font or FONT_UI, cursor="hand2",
                  highlightthickness=0, takefocus=0,
                  disabledforeground=C_FG_DIM)
    if width:
        kwargs["width"] = width
    button = tk.Button(parent, **kwargs)
    button.bind("<Enter>", lambda e: button.configure(bg=hover) if button["state"] != "disabled" else None)
    button.bind("<Leave>", lambda e: button.configure(bg=bg) if button["state"] != "disabled" else None)
    return button


def make_entry(parent, textvariable, width=None, font=None):
    import tkinter as tk
    kwargs = dict(textvariable=textvariable, bg=C_INPUT, fg=C_FG,
                  insertbackground=C_FG, relief="flat", bd=0,
                  font=font or FONT_UI, highlightthickness=1,
                  highlightbackground=C_BORDER, highlightcolor=C_ACCENT,
                  disabledbackground=C_PANEL, disabledforeground=C_FG_DIM)
    if width:
        kwargs["width"] = width
    return tk.Entry(parent, **kwargs)


def launch_gui():
    import tkinter as tk
    from tkinter import messagebox

    config = read_config()

    root = tk.Tk()
    root.title("WorkBuddy 美化 · 快捷启动器")
    root.configure(bg=C_BG)
    # 允许纵向拉伸：日志区长一点更好读。给个最小尺寸，防止拖到没法看。
    root.resizable(True, True)
    root.minsize(660, 690)

    # ⚠️ --windowed 打包后按钮回调里的异常是完全静默的（默认只打 stderr，
    # 而没有控制台）。必须接管，否则用户点一下没反应、也无从查起。
    def _on_callback_error(exc_type, exc, tb):
        log_exception(exc_type, exc, tb)
        try:
            messagebox.showerror(
                "操作出错",
                "发生了未预期的错误：\n\n%s: %s\n\n详情已写入：\n%s"
                % (exc_type.__name__, exc, error_log_path()),
                parent=root)
        except Exception:
            pass

    root.report_callback_exception = _on_callback_error

    # 窗口图标：打包后从解压根读 assets/launcher.ico（未打包时读技能目录）
    try:
        icon = os.path.join(BUNDLE_ROOT, "assets", "launcher.ico")
        if os.path.isfile(icon):
            root.iconbitmap(default=icon)
    except Exception:
        pass

    # ttk 样式（滚动条/复选框才好看）
    import tkinter.ttk as ttk
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure("Vertical.TScrollbar", background=C_INPUT,
                    troughcolor=C_BG, borderwidth=0, arrowsize=13)
    style.map("Vertical.TScrollbar", background=[("active", C_BORDER)])

    state = {
        "busy": False,
        "cancel": False,
        "found": {},
        "thread": None,
        "auto_filled": set(),   # 哪些框的当前值是自动探测填进去的
        "touched": set(),       # 哪些框被用户改过（改过就不再被自动值覆盖）
        "last_save": 0.0,       # 窗口位置上次落盘的时间（防抖）
    }

    # ---------------- 顶部标题 ----------------
    header = tk.Frame(root, bg=C_BG)
    header.pack(fill="x", padx=18, pady=(16, 4))
    tk.Label(header, text="WorkBuddy 美化 · 快捷启动器", bg=C_BG, fg=C_FG,
             font=FONT_TITLE).pack(anchor="w")
    tk.Label(header, text="选一个版本启动，CDP 就绪后本窗口会自动关闭",
             bg=C_BG, fg=C_FG_DIM, font=FONT_SMALL).pack(anchor="w", pady=(3, 0))

    # ---------------- 路径配置区 ----------------
    paths = tk.Frame(root, bg=C_PANEL, highlightthickness=1,
                     highlightbackground=C_BORDER)
    paths.pack(fill="x", padx=18, pady=(12, 0))
    tk.Label(paths, text="WorkBuddy 路径配置", bg=C_PANEL, fg=C_FG,
             font=FONT_BOLD).pack(anchor="w", padx=14, pady=(11, 0))

    entries = {}
    hints = {}
    for variant in (VARIANT_CN, VARIANT_OVERSEA):
        block = tk.Frame(paths, bg=C_PANEL)
        block.pack(fill="x", padx=14, pady=(9, 0))
        head = tk.Frame(block, bg=C_PANEL)
        head.pack(fill="x")
        tk.Label(head, text=VARIANT_LABELS[variant], bg=C_PANEL, fg=C_FG,
                 font=FONT_UI, width=6, anchor="w").pack(side="left")
        hint = tk.Label(head, text="", bg=C_PANEL, fg=C_FG_DIM, font=FONT_SMALL,
                        anchor="w")
        hint.pack(side="left", padx=(8, 0))
        hints[variant] = hint

        row = tk.Frame(block, bg=C_PANEL)
        row.pack(fill="x", pady=(5, 0))
        # 输入框里显示**实际会用的路径**（手工指定优先，否则由自动探测回填）。
        # 为什么要回填：如果自动探测出来却留个空框，用户看到的是"什么都没有"，
        # 完全不知道待会儿会启哪个版本 —— 而提示行写着"自动探测"，很困惑。
        # 语义仍然由 state["touched"] 决定：没被用户改过 = 自动探测，
        # 改过（含用「浏览」选的）= 手工指定。所以回填不等于固化配置。
        var = tk.StringVar(value=(config.get(variant) or {}).get("exe") or "")
        if (var.get() or "").strip():
            state["touched"].add(variant)       # 配置里存了 = 用户以前指定过
        entry = make_entry(row, var, font=FONT_SMALL)
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        entries[variant] = (var, entry)
        make_button(row, "浏览", lambda v=variant: browse(v),
                    kind="ghost").pack(side="left", padx=(6, 0))
        make_button(row, "自动", lambda v=variant: auto_path(v),
                    kind="ghost").pack(side="left", padx=(6, 0))
        # ⚠️ 只在**用户真的改了这个框**之后才落盘。不能拿"框里有内容"当
        # "用户指定了" —— 自动探测也会把值填进来，那时落盘等于把探测结果
        # 永久固化成手工配置，用户以后换了安装路径程序还在找老地方。
        #
        # 用 KeyRelease 而不是 <<Paste>> 作主绑定：`<<Paste>>` 在**粘贴生效之前**
        # 触发，那时读 var 拿到的还是旧值。但中键 / 右键菜单粘贴不走按键事件，
        # 所以 <<Paste>> 也得挂上 —— 靠 on_path_typed 内部的 after_idle 延后读值。
        entry.bind("<KeyRelease>", lambda e, v=variant: on_path_typed(v))
        entry.bind("<<Paste>>", lambda e, v=variant: on_path_typed(v))

    tk.Label(paths, text="留空 = 每次自动探测（注册表 / 常见安装位置 / 正在运行的进程）",
             bg=C_PANEL, fg=C_FG_DIM, font=FONT_SMALL).pack(anchor="w", padx=14,
                                                            pady=(9, 12))

    # ---------------- 启动区 ----------------
    actions = tk.Frame(root, bg=C_BG)
    actions.pack(fill="x", padx=18, pady=(14, 0))
    buttons = {}
    for variant, kind in ((VARIANT_CN, "cn"), (VARIANT_OVERSEA, "oversea")):
        button = make_button(actions, "启动%s" % VARIANT_LABELS[variant],
                             lambda v=variant: start(v), kind=kind,
                             width=17, font=FONT_BOLD)
        button.pack(side="left", expand=True, fill="x",
                    padx=(0, 6) if variant == VARIANT_CN else (6, 0))
        buttons[variant] = button

    # ---------------- 选项 ----------------
    options = tk.Frame(root, bg=C_BG)
    options.pack(fill="x", padx=18, pady=(12, 0))
    inject_after = tk.BooleanVar(value=bool(config.get("injectAfterLaunch")))
    close_after = tk.BooleanVar(value=bool(config.get("closeOnSuccess")))

    def make_check(parent, text, variable):
        box = tk.Checkbutton(parent, text=text, variable=variable, bg=C_BG,
                             fg=C_FG, activebackground=C_BG, activeforeground=C_FG,
                             selectcolor=C_INPUT, font=FONT_UI, bd=0,
                             highlightthickness=0, cursor="hand2",
                             command=lambda: save_options())
        return box

    make_check(options, "启动成功后自动注入皮肤", inject_after).pack(anchor="w")
    make_check(options, "启动成功后自动关闭本窗口", close_after).pack(anchor="w",
                                                                  pady=(4, 0))
    tk.Label(options, text="（关闭本窗口不影响已启动的 WorkBuddy）",
             bg=C_BG, fg=C_FG_DIM, font=FONT_SMALL).pack(anchor="w", padx=(22, 0))

    # ---------------- 进度 ----------------
    progress_block = tk.Frame(root, bg=C_BG)
    progress_block.pack(fill="x", padx=18, pady=(14, 0))
    status = tk.Label(progress_block, text="就绪", bg=C_BG, fg=C_FG_DIM,
                      font=FONT_SMALL, anchor="w")
    status.pack(fill="x")
    bar = ttk.Progressbar(progress_block, mode="determinate", maximum=100)
    bar.pack(fill="x", pady=(5, 0))

    # ---------------- 日志 ----------------
    log_block = tk.Frame(root, bg=C_PANEL, highlightthickness=1,
                         highlightbackground=C_BORDER)
    log_block.pack(fill="both", expand=True, padx=18, pady=(12, 0))
    # height 给足：注入日志一行能有 100+ 字符（路径长），
    # 行数太少会一直需要滚动，用户看不到关键结论。
    text = tk.Text(log_block, height=12, bg="#181a1c", fg=C_FG_DIM,
                   insertbackground=C_FG, relief="flat", bd=0,
                   font=resolve_mono_font(9),
                   wrap="word", padx=10, pady=8, highlightthickness=0)
    scroll = ttk.Scrollbar(log_block, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    text.pack(side="left", fill="both", expand=True)
    text.configure(state="disabled")

    # ---------------- 底部 ----------------
    footer = tk.Frame(root, bg=C_BG)
    footer.pack(fill="x", padx=18, pady=(12, 16))
    log_hint = tk.Label(footer, text="", bg=C_BG, fg=C_FG_DIM, font=FONT_SMALL,
                        anchor="w")
    log_hint.pack(side="left")
    cancel_button = make_button(footer, "取消", lambda: cancel(), kind="ghost")
    cancel_button.configure(state="disabled")
    cancel_button.pack(side="right")

    # ========================================================================
    # 界面行为
    # ========================================================================
    def append(line):
        text.configure(state="normal")
        text.insert("end", line + "\n")
        text.see("end")
        # 只留最后 400 行，别让长注入日志撑爆内存
        if int(text.index("end-1c").split(".")[0]) > 400:
            text.delete("1.0", "100.0")
        text.configure(state="disabled")

    def save_options():
        config["injectAfterLaunch"] = bool(inject_after.get())
        config["closeOnSuccess"] = bool(close_after.get())
        write_config(config)

    def refresh_hints():
        """把每条路径的「来源 / 进程 / CDP」显示出来。

        为什么要有：用户最怕的是"点了启动却不知道启的是哪个版本"。
        把探测结果摊开，一眼能确认。

        ⚠️ 判定"手工 / 自动"必须看 state["touched"]，**不能**看"框里有没有字"：
        自动探测也会把路径回填进框里，那时框里有字但并不是用户选的。
        早期版本就是这么写错的，界面上把自动探测的结果标成了"手工指定"。
        """
        touched = state.get("touched", set())
        for variant in (VARIANT_CN, VARIANT_OVERSEA):
            var, _ = entries[variant]
            manual = variant in touched
            found = state["found"].get(variant) or {}
            shown = (var.get() or "").strip() or found.get("exe") or ""
            if not shown:
                hints[variant].configure(
                    text="未找到 —— 请手工指定或先安装", fg=C_WARN)
                continue
            if not os.path.isfile(shown):
                hints[variant].configure(text="路径不存在", fg=C_ERR)
                continue
            parts = ["手工指定" if manual else "自动探测"]
            count = running_of(shown)
            parts.append("运行中 %d 进程" % count if count else "未运行")
            port = port_of(shown)
            parts.append("CDP %d" % port if port else "无 CDP")
            if count and not port:
                parts.append("需重启才能注入")
            hints[variant].configure(
                text=" | ".join(parts),
                fg=C_OK if port else C_FG_DIM)

    def refresh_paths():
        """把自动探测到的路径回填进输入框。

        只在用户没手工指定（框里为空且没敲过字）时填 —— 已经填了就是用户的选择，
        不能被自动探测覆盖掉。
        """
        state.setdefault("auto_filled", set())
        for variant in (VARIANT_CN, VARIANT_OVERSEA):
            var, _ = entries[variant]
            if variant in state.get("touched", set()):
                continue
            if (var.get() or "").strip():
                continue
            auto = (state["found"].get(variant) or {}).get("exe") or ""
            if auto:
                var.set(auto)
                state["auto_filled"].add(variant)

    def on_path_typed(variant):
        """用户改了这个框 → 记成手工指定，并立刻落盘。

        用 after_idle 延到当前事件处理完再读值：粘贴类事件在值更新**之前**
        触发，直接读会拿到旧的。
        """
        root.after_idle(lambda: _commit_path(variant))

    def _commit_path(variant):
        state.setdefault("touched", set())
        state["touched"].add(variant)
        var, _ = entries[variant]
        remember(variant, (var.get() or "").strip())
        refresh_hints()

    def browse(variant):
        from tkinter import filedialog
        current = (entries[variant][0].get() or "").strip()
        initial = os.path.dirname(current) if current and os.path.isfile(current) else None
        path = filedialog.askopenfilename(
            parent=root, title="选择%s的 WorkBuddy 可执行文件" % VARIANT_LABELS[variant],
            initialdir=initial,
            filetypes=[("可执行文件", "*.exe"), ("全部文件", "*.*")])
        if not path:
            return
        module = load_inject()
        variant_of = None
        if module is not None:
            try:
                variant_of = module.read_variant(path)[0]
            except Exception:
                variant_of = None
        if variant_of in (VARIANT_CN, VARIANT_OVERSEA) and variant_of != variant:
            messagebox.showwarning(
                "选错了版本",
                "这个文件是**%s**的，不是%s。\n\n%s"
                % (VARIANT_LABELS[variant_of], VARIANT_LABELS[variant], path),
                parent=root)
            return
        entries[variant][0].set(path)
        state.setdefault("touched", set()).add(variant)
        remember(variant, path)
        append("已设置%s路径：%s" % (VARIANT_LABELS[variant], path))
        refresh_hints()

    def auto_path(variant):
        """回到自动探测：清空输入框，然后用探测到的值填上。"""
        var, _ = entries[variant]
        state.setdefault("touched", set()).discard(variant)
        var.set("")
        remember(variant, "")
        auto = (state["found"].get(variant) or {}).get("exe") or ""
        if auto:
            var.set(auto)
            append("%s 恢复自动探测 → %s" % (VARIANT_LABELS[variant], auto))
        else:
            append("%s 恢复自动探测，但当前没探测到安装" % VARIANT_LABELS[variant])
        refresh_hints()

    def remember(variant, path):
        """记下用户的手工选择。传空串 = 不落盘路径（走自动探测）。"""
        config[variant] = {"exe": path, "auto": not bool(path)}
        write_config(config)

    def set_busy(busy):
        state["busy"] = busy
        for button in buttons.values():
            button.configure(state="disabled" if busy else "normal")
        cancel_button.configure(state="normal" if busy else "disabled")
        for variant in (VARIANT_CN, VARIANT_OVERSEA):
            entries[variant][1].configure(state="disabled" if busy else "normal")

    def set_progress(label, percent=None):
        status.configure(text=label, fg=C_FG_DIM)
        if percent is None:
            bar.configure(mode="indeterminate")
            try:
                bar.start(12)
            except Exception:
                pass
        else:
            try:
                bar.stop()
            except Exception:
                pass
            bar.configure(mode="determinate")
            bar["value"] = percent

    def cancel():
        state["cancel"] = True
        status.configure(text="正在取消…", fg=C_WARN)
        append("—— 用户取消 ——")

    def finish_ok(variant, port):
        set_progress("完成", 100)
        status.configure(text="%s 已启动（CDP %d）" % (VARIANT_LABELS[variant], port),
                         fg=C_OK)
        append("")
        append("✅ %s 启动成功，CDP 端口 %d。" % (VARIANT_LABELS[variant], port))
        append("   之后点 WorkBuddy 右上角的浮球即可随时换主题 / 调参。")
        if close_after.get():
            append("   本窗口将在 1.5 秒后自动关闭。")
            root.after(1500, root.destroy)

    def finish_fail(reason):
        set_progress("失败", None)
        try:
            bar.stop()
            bar.configure(mode="determinate")
            bar["value"] = 0
        except Exception:
            pass
        status.configure(text="启动失败", fg=C_ERR)
        append("")
        append("❌ 启动失败：%s" % reason)
        set_busy(False)

    def worker(variant):
        """后台线程：启动 +（可选）注入。Tk 控件一律通过 root.after 回主线程。"""
        def ui(fn, *args):
            root.after(0, lambda: fn(*args))

        def on_progress(label, percent=None):
            ui(set_progress, label, percent)

        exe = (entries[variant][0].get() or "").strip()
        if not exe:
            found = state["found"].get(variant) or {}
            exe = found.get("exe") or ""
        if not exe:
            ui(finish_fail, "没找到%s的安装。点「浏览」手工指定可执行文件。"
                            % VARIANT_LABELS[variant])
            return
        if not os.path.isfile(exe):
            ui(finish_fail, "路径不存在：%s" % exe)
            return

        append("")
        append("=" * 56)
        append("启动%s：%s" % (VARIANT_LABELS[variant], exe))

        def say(line):
            ui(append, line)

        result = launch(exe, log_fn=say, on_progress=on_progress,
                        cancel=lambda: state["cancel"])
        if not result["ok"]:
            if state["cancel"]:
                ui(set_progress, "已取消", None)
                ui(set_busy, False)
                return
            ui(finish_fail, result["reason"])
            return

        port = result["port"]
        if inject_after.get():
            code, error = run_inject(variant, log_fn=say, on_progress=on_progress)
            if code != 0:
                # 注入失败不代表启动失败 —— WorkBuddy 已经起来了，如实说清楚
                ui(set_progress, "已启动，但注入失败", None)
                ui(status.configure, {"text": "%s 已启动（注入失败）"
                                              % VARIANT_LABELS[variant],
                                      "fg": C_WARN})
                ui(append, "⚠️ WorkBuddy 已启动，但注入没成功（退出码 %s）。%s"
                           % (code, error))
                ui(append, "   日志：%s" % log_path())
                ui(set_busy, False)
                return
            append("皮肤注入完成 ✅")

        ui(finish_ok, variant, port)

    def start(variant):
        if state["busy"]:
            return
        state["cancel"] = False
        set_busy(True)
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.configure(state="disabled")
        append("准备启动%s…" % VARIANT_LABELS[variant])
        set_progress("准备中…", None)
        state["thread"] = threading.Thread(target=worker, args=(variant,),
                                           daemon=True)
        state["thread"].start()

    # ========================================================================
    # 初始化：探测安装、回填提示、恢复窗口位置
    # ========================================================================
    append("WorkBuddy 美化 · 快捷启动器")
    module = load_inject()
    if module is None:
        append("[失败] %s" % _inject_error)
        status.configure(text="inject.py 不可用", fg=C_ERR)
        for button in buttons.values():
            button.configure(state="disabled")
    else:
        state["found"] = discover()
        # 先把探测到的路径回填进输入框，再据此渲染提示 —— 顺序不能反，
        # 否则提示行描述的是"框里的值"，而框还是空的。
        refresh_paths()
        for variant in (VARIANT_CN, VARIANT_OVERSEA):
            found = state["found"].get(variant) or {}
            if found.get("exe"):
                append("%s：%s" % (VARIANT_LABELS[variant], found["exe"]))
            else:
                append("%s：未探测到安装" % VARIANT_LABELS[variant])
        refresh_hints()
    append("")
    append("提示：启动会带上 CDP 调试端口，这样皮肤才能注入和自愈。")
    append("      已经在带 CDP 运行的实例不会被重启。")

    geometry = config.get("window") or {}
    # 宽一点：国际版的路径（...\Programs\WorkBuddyAI\WorkBuddyAI.exe）比
    # 国内版长，窄了会在输入框里被截断，用户看不出是不是自己那个。
    width, height = 720, 740
    x, y = geometry.get("x"), geometry.get("y")
    if isinstance(x, int) and isinstance(y, int):
        # 别把窗口恢复到屏幕外（换分辨率/拔外接屏后很常见）
        screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
        if not (-50 <= x <= screen_w - 100 and -20 <= y <= screen_h - 100):
            x = y = None
    if x is None or y is None:
        root.update_idletasks()
        x = (root.winfo_screenwidth() - width) // 2
        y = max(40, (root.winfo_screenheight() - height) // 3)
    root.geometry("%dx%d+%d+%d" % (width, height, x, y))

    def on_close():
        save_geometry()
        save_options()
        root.destroy()

    def save_geometry():
        """把窗口位置记下来。

        为什么挂在 `<Configure>` 上而不只在关窗时存：
        `WM_DELETE_WINDOW` 只在用户点右上角 × 时触发。进程被任务管理器杀、
        或应用自己崩掉时它不会跑，位置就丢了 —— 而"下次打开还在原来的地方"
        恰恰是用户最在意的体验细节。
        频繁写盘没必要，所以只在坐标**真的变了**且**距离上次写盘超过 1 秒**时落盘。
        """
        try:
            x, y = root.winfo_x(), root.winfo_y()
            if root.state() != "normal":        # 最小化时会变成 -32000
                return
            if (x, y) == (state.get("last_x"), state.get("last_y")):
                return
            now = time.time()
            if now - state.get("last_save", 0.0) < 1.0:
                return
            state["last_x"], state["last_y"] = x, y
            state["last_save"] = now
            config["window"] = {"x": x, "y": y}
            write_config(config)
        except Exception:
            pass

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.bind("<Configure>", lambda event: save_geometry())

    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    return 0


# ============================================================================
# 七、入口
# ============================================================================
def _smoke_test():
    """打包验收用：查数据、加载 inject.py、创建 Tk 窗口，再自动销毁。"""
    required = [
        os.path.join(BUNDLE_ROOT, "scripts", "inject.py"),
        os.path.join(BUNDLE_ROOT, "scripts", "lib", "renderer.mjs"),
        os.path.join(BUNDLE_ROOT, "assets", "ambient.css"),
        os.path.join(BUNDLE_ROOT, "assets", "space-glass.css"),
        os.path.join(BUNDLE_ROOT, "assets", "space-selfheal.js"),
        os.path.join(BUNDLE_ROOT, "assets", "launcher.ico"),
        os.path.join(BUNDLE_ROOT, "assets", "themes", "doraemon-snow-fortune", "hero.webp"),
        os.path.join(BUNDLE_ROOT, "assets", "themes", "genshin-raiden-shogun", "hero.webp"),
        os.path.join(BUNDLE_ROOT, "assets", "themes", "miku-neko-maid", "hero.webp"),
        os.path.join(BUNDLE_ROOT, "assets", "themes", "paper-aurora", "theme.json"),
    ]
    if not all(os.path.isfile(path) for path in required):
        return 11
    module = load_inject()
    if module is None:
        return 12
    try:
        if len(module.read_css()) < 1000:
            return 13
        if not module.list_themes([module.BUNDLED_THEMES_ROOT]):
            return 14
    except Exception:
        log_exception()
        return 15
    import tkinter as tk
    root = tk.Tk()
    root.title("WorkBuddy 美化 · 快捷启动器")
    root.withdraw()
    root.update_idletasks()
    ok = root.winfo_screenwidth() > 0 and root.winfo_screenheight() > 0
    root.destroy()
    return 0 if ok else 16


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--smoke-test" in argv:
        return _smoke_test()

    # ---- 后台守护转发 ----------------------------------------------------
    # 打包成 exe 后，inject.py 的「资料库 iframe 守护」要靠重新调起**本 exe**
    # 才能获得游离进程（没有 pythonw + .py 可调了）。这里必须抢在 GUI 之前
    # 转发，否则被 spawn 出来的 watcher 会弹出一个多余的窗口。
    if "--space-watch" in argv:
        module = load_inject()
        if module is None:
            log("--space-watch 失败：%s" % _inject_error)
            return 2
        return module.main(argv)

    # 同理转发 space_doctor.py（一键美化.bat 在注入成功后会跑它做自检 + 补装）
    if "--doctor" in argv:
        if load_inject() is None:
            log("--doctor 失败：%s" % _inject_error)
            return 2
        module, error = load_script("space_doctor.py")
        if module is None:
            log("--doctor 失败：%s" % error)
            return 2
        # space_doctor.main() 不接参数、自己读 sys.argv，得替它摆好再调用
        saved = sys.argv
        try:
            sys.argv = ["space_doctor.py"] + [x for x in argv if x != "--doctor"]
            return module.main()
        finally:
            sys.argv = saved

    if "--list" in argv:
        return cli_list()

    if "--selftest" in argv:
        # 自检脚本是开发期文件，打包后也按 tests/ 原路径收进包。
        selftest = os.path.join(BUNDLE_ROOT, "tests", "_selftest_inject_launcher.py")
        if not os.path.isfile(selftest):
            print("找不到自检脚本：%s" % selftest)
            if FROZEN:
                print("（打包后的 exe 不带自检脚本 —— 它是开发期工具，"
                      "请在技能目录里跑 tests/_selftest_inject_launcher.py）")
            return 2
        return subprocess.call([sys.executable, selftest])

    try:
        import tkinter  # noqa: F401
    except ImportError:
        message = (
            "当前的 Python 没有 tkinter，无法显示窗口：\n%s\n\n"
            "请用「启动美化.bat」启动（它会自动挑一个带 tkinter 的 Python），"
            "或安装 Python 时勾选 tcl/tk 组件。" % sys.executable)
        print(message)
        try:                    # 有 tkinter 就不用这条；没有才走 ctypes 弹窗
            ctypes.windll.user32.MessageBoxW(None, message, "WorkBuddy 启动器", 0x10)
        except Exception:
            pass
        return 3
    try:
        return launch_gui()
    except Exception:
        # 启动阶段就崩的话用户什么都看不到 —— 弹个原生窗口告诉他日志在哪
        log_exception()
        try:
            ctypes.windll.user32.MessageBoxW(
                None, "启动器初始化失败。\n\n详情已写入：\n%s" % error_log_path(),
                "WorkBuddy 启动器", 0x10)
        except Exception:
            pass
        return 4


if __name__ == "__main__":
    # --windowed 打包后没有控制台，异常一抛就凭空消失，必须自己落盘。
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        log_exception()
        raise
