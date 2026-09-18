# -*- coding: utf-8 -*-
"""把启动器打包成独立 exe（onefile / onedir 两种）。

为什么需要这个脚本而不是手敲 pyinstaller 命令：
  打包要带一堆数据文件，而且**必须保持相对目录结构** —— inject.py 是靠
  `__file__` 往上推两级去找 assets/ 的。手敲容易漏文件，漏了不会在构建期报错，
  只在用户双击时才炸（"找不到 ambient.css"）。所以把清单固化在这里。

打包清单：技能目录下的**全部文件**都会进入包（排除 .git、build、dist、__pycache__
这类版本库/构建产物目录）。包括 runtime 脚本、完整 scripts/lib、所有 assets、
诊断脚本、测试、README、SKILL.md 和 bat/ps1/sh 文件，且保持原相对目录结构。

这样 onefile / onedir 运行时不需要旁边再放任何技能文件；用户拿到一个 exe（或整个
onedir 文件夹）即可启动。
用法：
    python scripts/_build_exe.py                 # 两种都打，**默认带全部 assets**
    python scripts/_build_exe.py --only onefile  # 只打单文件
    python scripts/_build_exe.py --without-previews  # 不带 README 预览图（省 4.4MB）
"""
import os
import shutil
import subprocess
import sys
import time

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY = os.path.join(SKILL, "scripts", "inject_launcher.py")
ICON = os.path.join(SKILL, "assets", "launcher.ico")
DIST = os.path.join(SKILL, "dist")
BUILD = os.path.join(SKILL, "build")
SPEC = os.path.join(SKILL, "build", "spec")

APP_NAME = "WorkBuddy美化启动器"

# inject.py / space_doctor.py 是 importlib 动态加载的，PyInstaller 的静态分析
# **看不到它们**，所以它们用到的模块必须显式声明 —— 否则打包不报错，
# 运行时走到那一步才 ImportError（winreg 是最容易漏的那个）。
HIDDEN_IMPORTS = [
    "winreg", "ctypes.wintypes", "functools", "hashlib", "base64",
    "socket", "struct", "subprocess", "tempfile", "threading",
    "json", "re", "shutil", "glob", "traceback",
    # tkinter 是在函数内部才 import 的，也显式声明保险
    "tkinter", "tkinter.ttk", "tkinter.font", "tkinter.messagebox",
    "tkinter.filedialog", "tkinter.constants",
]

# 只排明确用不到的，别乱排 —— tkinter 有隐藏依赖
EXCLUDES = [
    "unittest", "pydoc", "doctest", "pdb", "lib2to3", "distutils",
    "setuptools", "pip", "wheel", "pytest", "numpy", "PIL",
    "email", "html", "http", "xml", "xmlrpc", "sqlite3",
]

# 找 pyinstaller：优先用专门建的构建 venv（它有 tkinter）
VENV_CANDIDATES = [
    os.path.join(os.path.expanduser("~"), ".workbuddy-ai", "binaries",
                 "python", "envs", "gui-build"),
]
PY_CANDIDATES = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python",
                 "Python313", "python.exe"),
]


def find_pyinstaller():
    for venv in VENV_CANDIDATES:
        exe = os.path.join(venv, "Scripts", "pyinstaller.exe")
        if os.path.isfile(exe):
            return exe, os.path.join(venv, "Scripts", "python.exe")
    # 退到 PATH
    found = shutil.which("pyinstaller")
    if found:
        return found, sys.executable
    return None, None


def collect_datas(include_previews=True):
    """(源路径, 包内目录) 列表。默认收进全部 assets。"""
    datas = []
    seen = set()

    def add(src, dest):
        if not os.path.isfile(src):
            return
        key = (os.path.normcase(os.path.abspath(src)), dest)
        if key not in seen:
            datas.append((src, dest))
            seen.add(key)

    # 用户明确要求「所有文件都打进包」。递归收技能目录并保持原相对结构，
    # 只排除版本库 / 构建产物 / Python 缓存，避免把上一次 exe 又套进下一次 exe。
    excluded_dirs = {".git", "build", "dist", "__pycache__"}
    for root, dirs, files in os.walk(SKILL):
        dirs[:] = sorted(d for d in dirs if d not in excluded_dirs)
        rel = os.path.relpath(root, SKILL)
        dest = "." if rel == "." else rel
        for filename in sorted(files):
            if filename.endswith((".pyc", ".pyo")):
                continue
            if not include_previews and dest.replace("\\", "/").startswith("assets/images"):
                continue
            add(os.path.join(root, filename), dest)

    return datas


def build(pyinstaller, python, onefile, include_previews):
    datas = collect_datas(include_previews)
    mode = "onefile" if onefile else "onedir"
    out_name = APP_NAME if onefile else APP_NAME + "-文件夹"
    # 每次用唯一 work/spec 目录，避免 PyInstaller --clean 为了清理上一次
    # 的大目录而触发环境的批量删除保护；旧构建产物本身不影响新构建。
    build_id = "%s-%d" % (time.strftime("%Y%m%d-%H%M%S"), os.getpid())
    workpath = os.path.join(BUILD, mode + "-" + build_id)
    specpath = os.path.join(BUILD, "spec", mode + "-" + build_id)

    # PyInstaller 会先替换同名目标。当前环境对一次删除大量文件会拦截确认，
    # 重跑时统一改名备份，既不丢旧产物也不阻断构建。
    existing = os.path.join(DIST, out_name + (".exe" if onefile else ""))
    if os.path.exists(existing):
        backup = existing + "-old-" + time.strftime("%Y%m%d-%H%M%S")
        try:
            os.replace(existing, backup)
            print("已把旧产物改名备份到：%s" % backup)
        except OSError as error:
            print("[警告] 旧产物无法改名：%s" % error)

    cmd = [
        pyinstaller,
        "--noconfirm", "--clean",
        "--onefile" if onefile else "--onedir",
        "--windowed",
        "--name", out_name,
        "--icon", ICON,
        "--distpath", DIST,
        "--workpath", workpath,
        "--specpath", specpath,
    ]
    for module in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", module]
    for module in EXCLUDES:
        cmd += ["--exclude-module", module]
    for src, dest in datas:
        cmd += ["--add-data", "%s;%s" % (src, dest)]
    cmd.append(ENTRY)

    print("=" * 70)
    print(" 构建 %s（%d 个数据文件）" % (mode, len(datas)))
    print("=" * 70)
    started = time.time()
    result = subprocess.run(cmd, cwd=SKILL, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    elapsed = time.time() - started
    if result.returncode != 0:
        print("[失败] pyinstaller 退出码 %d" % result.returncode)
        print((result.stdout or "")[-2500:])
        print((result.stderr or "")[-2500:])
        return None
    # 成功也把尾部打出来（里面有体积/警告信息）
    tail = [line for line in (result.stdout or "").splitlines()
            if line.strip().startswith("WARNING") or "Building" in line]
    for line in tail[:12]:
        print("  " + line.strip())

    if onefile:
        target = os.path.join(DIST, out_name + ".exe")
    else:
        target = os.path.join(DIST, out_name)
    print("")
    print("完成 %s，耗时 %.1fs" % (mode, elapsed))
    if os.path.exists(target):
        if onefile:
            print("  %s  %.1f MB" % (target, os.path.getsize(target) / 1048576.0))
        else:
            total = sum(os.path.getsize(os.path.join(root, f))
                        for root, _, files in os.walk(target) for f in files)
            print("  %s  （整目录 %.1f MB）" % (target, total / 1048576.0))
            zip_path = zip_onedir(target)
            if zip_path:
                print("  %s  %.1f MB" % (zip_path,
                                         os.path.getsize(zip_path) / 1048576.0))
    else:
        print("  [警告] 没找到产物：%s" % target)
    return target


def zip_onedir(folder):
    """把 onedir 打成 zip。

    为什么构建阶段就顺手压：onedir 有上千个文件，用户直接拷文件夹很容易
    漏（尤其是隐藏的 _internal 子目录）；给一个 zip 就不会漏，也方便传输。
    另外本机环境会定期回收 dist 下的大目录，压成单文件更耐久。
    """
    import zipfile
    out = folder + ".zip"
    if os.path.exists(out):
        try:
            os.remove(out)
        except OSError:
            pass
    entries = 0
    try:
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6) as archive:
            for root, dirs, files in os.walk(folder):
                dirs.sort()
                for filename in sorted(files):
                    full = os.path.join(root, filename)
                    arc = os.path.relpath(full, os.path.dirname(folder))
                    archive.write(full, arc.replace(os.sep, "/"))
                    entries += 1
    except Exception as error:
        print("  [警告] 打包 zip 失败：%s" % error)
        return None
    print("  zip 条目数 %d" % entries)
    return out


def main():
    argv = sys.argv[1:]
    only = None
    if "--only" in argv:
        index = argv.index("--only")
        if index + 1 < len(argv):
            only = argv[index + 1]
    include_previews = "--without-previews" not in argv

    if not os.path.isfile(ENTRY):
        print("[失败] 找不到入口脚本：%s" % ENTRY)
        return 2
    if not os.path.isfile(ICON):
        print("[失败] 找不到图标：%s（先跑 scripts/_make_icon.py）" % ICON)
        return 2

    pyinstaller, python = find_pyinstaller()
    if not pyinstaller:
        print("[失败] 找不到 pyinstaller。先建构建 venv：")
        print('  "%s" -m venv "%s"' % (PY_CANDIDATES[0], VENV_CANDIDATES[0]))
        print('  "%s/Scripts/python.exe" -m pip install pyinstaller'
              % VENV_CANDIDATES[0])
        return 2
    print("pyinstaller: %s" % pyinstaller)
    print("数据文件：%s" % ("含预览图" if include_previews else "不含预览图（省 4.4MB）"))
    print("")

    targets = []
    for onefile in (True, False):
        if only and only != ("onefile" if onefile else "onedir"):
            continue
        target = build(pyinstaller, python, onefile, include_previews)
        if target:
            targets.append(target)

    print("")
    print("=" * 70)
    print(" 产物")
    print("=" * 70)
    for target in targets:
        print("  %s" % target)
    return 0 if targets else 1


if __name__ == "__main__":
    sys.exit(main())
