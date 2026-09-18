# -*- coding: utf-8 -*-
"""验收打包产物：onefile 单文件 exe + onedir 的 ZIP。

为什么验 ZIP 而不是 onedir 文件夹：文件夹有上千个文件，实际交付和传输都用
ZIP；而且本机环境会定期回收 dist 下的大目录，只验文件夹会变成"时有时无"。
所以这里**把 ZIP 解到临时目录，真的跑一遍里面的 exe**，才算验完。

两种产物都用 `--smoke-test`：它会校验包内脚本、CSS、JS、主题、Tk 创建，
并加载 inject.py，**不启动 WorkBuddy**。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
DIST = os.path.join(SKILL, "dist")

ONEFILE = os.path.join(DIST, "WorkBuddy美化启动器.exe")
ZIP_PATH = os.path.join(DIST, "WorkBuddy美化启动器-文件夹.zip")
ONEDIR_DIRNAME = "WorkBuddy美化启动器-文件夹"
ONEDIR_INNER = ONEDIR_DIRNAME + ".exe"

fails = []


def check(name, cond, extra=""):
    print("  %s %s%s" % ("[OK]  " if cond else "[FAIL]", name,
                         ("  -> %s" % extra) if extra else ""))
    if not cond:
        fails.append(name)


def run_smoke(exe, cwd):
    """跑 --smoke-test，返回 (ok, 说明)。"""
    log_dir = tempfile.mkdtemp(prefix="wbas-packaged-")
    env = os.environ.copy()
    env["LOCALAPPDATA"] = log_dir
    env["WORKBUDDY_AMBIENT_LAUNCHER_CONFIG"] = os.path.join(log_dir, "launcher.json")
    started = time.time()
    try:
        result = subprocess.run([exe, "--smoke-test"], cwd=cwd, env=env,
                                timeout=40, capture_output=True)
        elapsed = time.time() - started
        error_log = os.path.join(log_dir, "WorkBuddyAmbientSkin",
                                 "launcher-error.log")
        detail = "rc=%d %.2fs" % (result.returncode, elapsed)
        if result.returncode != 0:
            detail += " stdout=%r stderr=%r" % (result.stdout[-300:],
                                                result.stderr[-300:])
        if os.path.exists(error_log):
            detail += " ⚠ 有 launcher-error.log"
        return (result.returncode == 0 and not os.path.exists(error_log)), detail
    except subprocess.TimeoutExpired:
        return False, "超过 40 秒"
    except Exception as error:
        return False, "%s: %s" % (type(error).__name__, error)


print("=" * 70)
print("打包产物验收")
print("=" * 70)

# ---------------------------------------------------------------- onefile
print("")
print("[onefile] %s" % ONEFILE)
check("产物存在", os.path.isfile(ONEFILE), ONEFILE)
if os.path.isfile(ONEFILE):
    ok, detail = run_smoke(ONEFILE, DIST)
    check("--smoke-test 通过且无异常日志", ok, detail)
    print("  大小：%.1f MB" % (os.path.getsize(ONEFILE) / 1048576.0))

# ------------------------------------------------------------------- zip
print("")
print("[onedir] %s" % ZIP_PATH)
check("ZIP 存在", os.path.isfile(ZIP_PATH), ZIP_PATH)

if not os.path.isfile(ZIP_PATH):
    print("")
    print("（先跑 scripts/_build_exe.py 生成 onedir 与 ZIP）")
else:
    with zipfile.ZipFile(ZIP_PATH) as archive:
        bad = archive.testzip()
        check("ZIP 无损坏条目", bad is None, bad or "")
        names = archive.namelist()
        print("  条目数：%d，压缩后 %.1f MB"
              % (len(names), os.path.getsize(ZIP_PATH) / 1048576.0))
        for rel in ("_internal/scripts/inject.py",
                    "_internal/scripts/space_doctor.py",
                    "_internal/scripts/lib/renderer.mjs",
                    "_internal/assets/ambient.css",
                    "_internal/assets/space-glass.css",
                    "_internal/assets/space-selfheal.js",
                    "_internal/assets/launcher.ico",
                    "_internal/assets/images/preview-paper-aurora.png",
                    "_internal/assets/themes/doraemon-snow-fortune/hero.webp",
                    "_internal/assets/themes/genshin-raiden-shogun/hero.webp",
                    "_internal/assets/themes/miku-neko-maid/hero.webp",
                    "_internal/assets/themes/paper-aurora/theme.json",
                    "_internal/README.md",
                    "_internal/SKILL.md",
                    "_internal/tests/_selftest_inject_launcher.py"):
            check("ZIP 含 " + rel, any(n.endswith(rel) for n in names), rel)
        exes = [n for n in names if n.endswith(".exe")]
        check("ZIP 含主 exe", any(n.endswith(ONEDIR_INNER) for n in exes), exes)

    # 真解压出来跑一遍 —— 唯一能证明"ZIP 可直接用"的方式
    work = tempfile.mkdtemp(prefix="wbas-unzip-")
    print("")
    print("[onedir] 解压到临时目录实测")
    try:
        with zipfile.ZipFile(ZIP_PATH) as archive:
            archive.extractall(work)
        root = os.path.join(work, ONEDIR_DIRNAME)
        exe = os.path.join(root, ONEDIR_INNER)
        check("解压后主 exe 就位", os.path.isfile(exe), exe)
        if os.path.isfile(exe):
            ok, detail = run_smoke(exe, root)
            check("解压后 --smoke-test 通过且无异常日志", ok, detail)
        internal = os.path.join(root, "_internal")
        for rel in ("scripts/inject.py", "scripts/lib/renderer.mjs",
                    "assets/ambient.css",
                    "assets/themes/paper-aurora/theme.json",
                    "tests/_selftest_inject_launcher.py"):
            check("解压后含 " + rel,
                  os.path.isfile(os.path.join(internal, *rel.split("/"))), rel)
    except Exception as error:
        check("ZIP 可解压并运行", False, "%s: %s" % (type(error).__name__, error))
    finally:
        shutil.rmtree(work, ignore_errors=True)

print("")
print("=" * 70)
if fails:
    print("失败 %d 项：" % len(fails))
    for item in fails:
        print("  - %s" % item)
    sys.exit(1)
print("打包产物验收全部通过 ✅")
