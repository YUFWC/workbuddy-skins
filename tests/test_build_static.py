# -*- coding: utf-8 -*-
"""PyInstaller 打包清单与冻结模式的静态回归测试。

不启动 PyInstaller、不启动 exe；检查最容易漏掉的运行期文件和冻结路径契约。
真正的 exe 验收由 tests/test_packaged_exe.py（打包后）完成。
"""
import importlib.util
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
BUILD_SCRIPT = os.path.join(SKILL, "scripts", "_build_exe.py")
LAUNCHER = os.path.join(SKILL, "scripts", "inject_launcher.py")
INJECT = os.path.join(SKILL, "scripts", "inject.py")
ICON = os.path.join(SKILL, "assets", "launcher.ico")

fails = []

def check(name, cond, extra=""):
    print("  %s %s%s" % ("[OK]  " if cond else "[FAIL]", name,
                         ("  -> %s" % extra) if extra else ""))
    if not cond:
        fails.append(name)


spec = importlib.util.spec_from_file_location("wbas_build", BUILD_SCRIPT)
B = importlib.util.module_from_spec(spec)
sys.modules["wbas_build"] = B
spec.loader.exec_module(B)

launcher_source = io.open(LAUNCHER, encoding="utf-8").read()
inject_source = io.open(INJECT, encoding="utf-8").read()

print("=" * 70)
print("PyInstaller 打包静态检查")
print("=" * 70)

# [1] 入口和图标
print("[1] 入口与图标")
check("GUI 入口存在", os.path.isfile(LAUNCHER), LAUNCHER)
check("inject.py 存在", os.path.isfile(INJECT), INJECT)
check("ICO 存在", os.path.isfile(ICON), ICON)
if os.path.isfile(ICON):
    raw = open(ICON, "rb").read()
    check("ICO 头正确", len(raw) >= 6 and raw[0:4] == b"\x00\x00\x01\x00")
    count = int.from_bytes(raw[4:6], "little") if len(raw) >= 6 else 0
    check("ICO 含 7 个尺寸", count == 7, count)
    # 最后一个尺寸的宽高字段 0 = 256
    last = 6 + (count - 1) * 16
    check("ICO 的 256 尺寸按格式写成 0",
          count > 0 and raw[last] == 0 and raw[last + 1] == 0)

# [2] 数据清单
print("")
print("[2] 数据清单")
all_data = B.collect_datas(True)
small_data = B.collect_datas(False)
paths = {os.path.normcase(os.path.abspath(src)): dest for src, dest in all_data}
check("默认清单包含全部 assets 文件",
      all(os.path.normcase(os.path.abspath(os.path.join(root, filename))) in paths
          for root, _, files in os.walk(os.path.join(SKILL, "assets"))
          for filename in files))
check("默认清单包含 4 张 README 预览图",
      sum(1 for src, _ in all_data if os.path.basename(src).startswith("preview-")) == 4)
check("--without-previews 只排除预览图",
      len(all_data) - len(small_data) == 4,
      "%d -> %d" % (len(all_data), len(small_data)))
check("包含 scripts/inject.py",
      os.path.normcase(os.path.abspath(INJECT)) in paths)
check("包含 scripts/space_doctor.py",
      any(os.path.basename(src) == "space_doctor.py" for src, _ in all_data))
lib_files = [os.path.join(root, filename)
             for root, _, files in os.walk(os.path.join(SKILL, "scripts", "lib"))
             for filename in files]
check("scripts/lib 全量进入包",
      all(os.path.normcase(os.path.abspath(src)) in paths for src in lib_files),
      "%d 个" % len(lib_files))
# 除构建产物 / 缓存外，技能目录下的每个文件都应进入包
excluded = {".git", "build", "dist", "__pycache__"}
expected = []
for root, dirs, files in os.walk(SKILL):
    dirs[:] = [d for d in dirs if d not in excluded]
    for filename in files:
        if filename.endswith((".pyc", ".pyo")):
            continue
        expected.append(os.path.normcase(os.path.abspath(os.path.join(root, filename))))
actual = {os.path.normcase(os.path.abspath(src)) for src, _ in all_data}
check("除构建产物外技能目录文件全部进入包",
      set(expected).issubset(actual),
      "%d expected / %d actual" % (len(expected), len(actual)))
check("没有把上一次 build/dist 产物打进包",
      not any(os.path.normcase(os.path.abspath(src)).startswith(
          os.path.normcase(os.path.join(SKILL, "build")))
              or os.path.normcase(os.path.abspath(src)).startswith(
          os.path.normcase(os.path.join(SKILL, "dist")))
              for src, _ in all_data))
check("包内保持 assets 相对目录",
      all(dest.replace("\\", "/").startswith("assets")
          for src, dest in all_data if os.path.abspath(src).startswith(
              os.path.join(SKILL, "assets"))))
check("包内保持 scripts/lib 相对目录",
      all(dest.replace("\\", "/").startswith("scripts")
          for src, dest in all_data if os.path.abspath(src).startswith(
              os.path.join(SKILL, "scripts", "lib"))))

# [3] 冻结路径与异常日志
print("")
print("[3] 冻结路径与异常保护")
for token, why in [
    ("_MEIPASS", "读取 PyInstaller onefile 解压根"),
    ("BUNDLE_ROOT", "统一的包内根目录"),
    ("FROZEN", "冻结模式分支"),
    ("launcher-error.log", "无控制台时的启动异常日志"),
    ("report_callback_exception", "接管 Tk 回调异常"),
    ("root.after(1500, root.destroy)", "启动成功后自动关窗"),
    ("def _smoke_test", "打包后真实创建 Tk 窗口的验收入口"),
    ('sys.modules["inject"]', "space_doctor 动态 import 的兼容别名"),
]:
    check("launcher 包含 %s" % why, token in launcher_source, token)

for token, why in [
    ("def _watcher_command", "统一构造守护进程命令"),
    ('getattr(sys, "frozen", False)', "inject 冻结分支"),
    ('sys.executable', "冻结后重新调起 exe 自身"),
    ('_install_startup_entry(interpreter, args, log)', "开机自启使用同一命令"),
    ("def _clean_child_env", "spawn 子进程前剥离 PyInstaller 的 _PYI_* 变量"),
    ('key.upper().startswith("_PYI_")', "剥离逻辑按 _PYI_ 前缀判定"),
    ('env=_clean_child_env()', "裸 DETACHED spawn 用了干净环境"),
    ('sh.Environment("PROCESS").Remove', "wscript 路径也在 .vbs 里剥 _PYI_*"),
]:
    check("inject 包含 %s" % why, token in inject_source, token)

# [4] 构建选项
print("")
print("[4] 构建选项")
source = io.open(BUILD_SCRIPT, encoding="utf-8").read()
for token, why in [
    ("--onefile", "单文件构建"),
    ("--onedir", "文件夹构建"),
    ("--windowed", "无黑色控制台"),
    ("--hidden-import", "动态加载模块显式收集"),
    ("--add-data", "运行期数据文件收集"),
    ("--clean", "构建前清理"),
    ("--without-previews", "可选排除预览图"),
]:
    check("构建器支持 %s" % why, token in source, token)

print("")
print("=" * 70)
if fails:
    print("失败 %d 项：" % len(fails))
    for item in fails:
        print("  - %s" % item)
    raise SystemExit(1)
print("打包静态检查全部通过 ✅")
