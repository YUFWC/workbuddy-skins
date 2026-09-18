# -*- coding: utf-8 -*-
"""生成「WorkBuddy 美化 · 快捷启动器」的 .bat 启动器（GBK + CRLF）。

为什么需要单独写一个生成器（而不是直接用 windows-tkinter-gui-app 的
make_launcher.py）：那个模板假定 .py 和 .bat 在同一目录、用 `%~dp0` 定位；
我们这个 GUI 装在技能的 `scripts/` 下，而 .bat 要放到桌面/工作区，
所以必须内嵌**绝对路径**，并且还要：
  * 挑一个**带 tkinter** 的 Python（托管版没有 tkinter，这是最大的坑）
  * 优先 pythonw.exe 启动（不弹黑窗口）
  * 失败时能看到原因（带 --keep-console 变体）

.bat 里出现中文时必须写 GBK(cp936) + CRLF，且**不要 chcp**：
在批处理中间 chcp 65001 会让 cmd 用新代码页重读文件、吞掉后面几行，
表现为"双击没反应"。中文 Windows 的 cmd 默认代码页就是 936。

用法：
    python scripts/_make_launcher_bat.py [输出目录...]
不传目录时默认写到桌面和工作区。
"""
import os
import sys

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUI = os.path.join(SKILL, "scripts", "inject_launcher.py")

def default_dirs():
    """默认输出目录：桌面 + 技能所在目录的上一级。

    不写死某个工作区路径 —— 换台机器、换个解压位置都能用。
    """
    out = []
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.isdir(desktop):
        out.append(desktop)
    out.append(os.path.dirname(SKILL))
    return out

# {gui} 会被替换成 GUI 的绝对路径。用 %%~C 而不是 %%C 是因为候选值本身带引号。
TEMPLATE = r'''@echo off
setlocal EnableExtensions
title WorkBuddy 美化 · 快捷启动器

rem ============================================================
rem  双击即用：弹出启动器窗口，选国内版或国际版一键启动
rem
rem  为什么要有这个 .bat：
rem    GUI 需要 tkinter，而 WorkBuddy 的**托管 Python 不带 tkinter**，
rem    必须挑一个系统装的、带 tcl/tk 的解释器。这个 .bat 负责挑。
rem
rem  .bat 里带中文，所以本文件是 GBK(cp936) 编码，
rem  并且**故意不写 chcp** —— 中途 chcp 会让 cmd 重读文件、吞掉后续行。
rem ============================================================

rem 某些终端（从 WorkBuddy 内部启动的 cmd）PATH 里没有 System32，
rem 会让 findstr 这类命令找不到，先把系统路径补回去。
if not defined SystemRoot set "SystemRoot=C:\Windows"
set "PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem;%PATH%"

set "GUI={gui}"
if not exist "%GUI%" goto :no_gui

rem ---- 挑一个带 tkinter 的解释器（按优先级）------------------------------
set "PY="
set "PYW="

rem 1) 用户装的 Python（最常见的位置）
for %%C in (
  "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
  "C:\Python313\python.exe"
  "C:\Python312\python.exe"
  "C:\Python311\python.exe"
) do (
  if not defined PY if exist %%~C (
    %%~C -c "import tkinter" >nul 2>nul && set "PY=%%~C"
  )
)

rem 2) 托管运行时兜底（本机实测**没有** tkinter，所以放最后）
if not defined PY (
  for %%C in (
    "%USERPROFILE%\.workbuddy-ai\binaries\python\envs\default\Scripts\python.exe"
    "%USERPROFILE%\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe"
  ) do (
    if not defined PY if exist %%~C (
      %%~C -c "import tkinter" >nul 2>nul && set "PY=%%~C"
    )
  )
)

rem 3) PATH 上的 python 兜底（注意可能有 WindowsApps 商店占位程序，会假通过）
if not defined PY (
  python -c "import tkinter" >nul 2>nul && set "PY=python"
)

if not defined PY goto :no_python

rem ---- 优先用 pythonw 启动，不弹黑色控制台窗口 ---------------------------
for %%I in ("%PY%") do set "PYW=%%~dpInpythonw.exe"
if exist "%PYW%" (
  start "" "%PYW%" "%GUI%"
) else (
  start "" "%PY%" "%GUI%"
)
exit /b 0

:no_python
echo.
echo [失败] 没有找到带 tkinter 的 Python 解释器。
echo.
echo 本启动器的界面需要 tkinter，而 WorkBuddy 自带的 Python 是精简版、
echo 不含 tkinter。请安装一个完整版 Python：
echo.
echo     https://www.python.org/downloads/windows/
echo.
echo 安装时**务必勾选 "tcl/tk and IDLE"**（默认是选上的，别取消）。
echo.
pause
exit /b 2

:no_gui
echo.
echo [失败] 找不到启动器脚本：
echo          %GUI%
echo.
echo 技能目录可能被移动或删除了。
echo.
pause
exit /b 3
'''

# 调试变体：保留控制台，能看到 traceback
CONSOLE_TEMPLATE = TEMPLATE.replace(
    'if exist "%PYW%" (\n  start "" "%PYW%" "%GUI%"\n) else (\n  start "" "%PY%" "%GUI%"\n)',
    '"%PY%" "%GUI%"\necho.\necho 程序已退出，按任意键关闭窗口...\npause >nul'
).replace("title WorkBuddy 美化 · 快捷启动器",
          "title WorkBuddy 美化 · 快捷启动器 [调试]")

VARIANTS = [
    ("启动美化.bat", TEMPLATE),
    ("启动美化-调试.bat", CONSOLE_TEMPLATE),
]


def render(template):
    text = template.replace("{gui}", GUI)
    return text.replace("\r\n", "\n").replace("\n", "\r\n").encode("gbk")


def main():
    dirs = sys.argv[1:] or default_dirs()
    if not os.path.isfile(GUI):
        print("[失败] 找不到 GUI 脚本：%s" % GUI)
        return 2
    written = 0
    for name, template in VARIANTS:
        data = render(template)
        for directory in dirs:
            if not os.path.isdir(directory):
                print("跳过（目录不存在）：%s" % directory)
                continue
            path = os.path.join(directory, name)
            try:
                with open(path, "wb") as handle:
                    handle.write(data)
            except PermissionError as error:
                print("[失败] 写不进去 %s：%s" % (path, error))
                continue
            print("写入 %-52s %d 字节" % (path, len(data)))
            written += 1
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())
