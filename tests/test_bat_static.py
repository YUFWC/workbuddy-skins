# -*- coding: utf-8 -*-
"""静态检查 一键美化.bat.src 的批处理语法风险。

不执行 bat（沙箱里 cmd 行为不可靠），只做文本层面的配平检查：
  * 每个 goto :x 都有对应的 :x 标签
  * 每个 set "..." 的引号成对
  * 括号块内外不混用 %VAR%（块内 %VAR% 会提前展开，是老坑）
  * 视频相关变量确实出现在最终调用行
"""
import io
import importlib.util
import os
import re
import sys

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "scripts", "一键美化.bat.src")
text = io.open(SRC, encoding="utf-8").read()
print("检查对象：%s" % SRC)
print("")

fail = []

# 1) 标签配平
labels = set(re.findall(r"^:([A-Za-z_][\w-]*)", text, re.M))
gotos = set(re.findall(r"goto :([A-Za-z_][\w-]*)", text))
missing = gotos - labels
print("标签: %d 个 | goto: %d 处 | 缺失: %s"
      % (len(labels), len(gotos), sorted(missing) if missing else "无"))
if missing:
    fail.append("goto 指向了不存在的标签：%s" % sorted(missing))

# 2) set 引号配平
unbalanced = []
for number, line in enumerate(text.split("\n"), 1):
    stripped = line.strip()
    if stripped.lower().startswith("set ") and stripped.count('"') % 2:
        unbalanced.append((number, stripped))
print("set 引号不配平: %s" % (unbalanced if unbalanced else "无"))
if unbalanced:
    fail.append("set 引号不配平：%s" % unbalanced)

# 3) 块内 %VAR% 展开风险：for/if 的 ( ) 块里出现 %VAR% 要报警
#    允许 %%V（for 的循环变量）和 %~dp0 这类
block_risk = []
depth = 0
for number, line in enumerate(text.split("\n"), 1):
    if re.search(r"\)\s*do\s*\($|^\s*\(\s*$", line, re.I):
        depth += 1
    elif depth and re.match(r"^\s*\)", line):
        depth -= 1
    elif depth:
        for var in re.findall(r"%([A-Za-z_]\w*)%", line):
            block_risk.append((number, var, line.strip()[:70]))
print("块内 %%VAR%% 风险: %s" % (block_risk if block_risk else "无"))
if block_risk:
    fail.append("括号块内引用了 %%VAR%%（会提前展开）：%s" % block_risk)

# 4) 视频变量真的接到了 inject.py 调用
has_arg = re.search(r'"%PY%"\s+"%SCRIPT%".*%VIDEO_ARG%', text)
print("VIDEO_ARG 已接到 inject.py 调用行: %s" % ("是" if has_arg else "否"))
if not has_arg:
    fail.append("VIDEO_ARG 没有传给 inject.py")

for var in ["VIDEO_FILE", "VIDEO_ARG", "VIDEO_LINE"]:
    count = text.count(var)
    print("  %-12s 出现 %d 次" % (var, count))
    if count < 2:
        fail.append("%s 定义但没被使用（疑似漏接线）" % var)

# 5) 视频开关必须支持「旁挂文件」三态：不设置 / 开 / 关
for token, why in [
    ("视频背景.txt", "旁挂文件名"),
    ("--video-off", "显式关闭的映射"),
    ("%~dp0", "相对 bat 自身定位（不能依赖 CWD）"),
]:
    ok = token in text
    print("含 %-14s（%s）: %s" % (token, why, "是" if ok else "否"))
    if not ok:
        fail.append("缺少 %s（%s）" % (token, why))

if "eol=;" not in text:
    fail.append("读旁挂文件没有 eol=; 注释支持（用户没法在文件里写说明）")
    print("eol=; 注释支持: 否")
else:
    print("eol=; 注释支持: 是")

# 6) 兜底：VIDEO_ARG 在调用行出现时，该行不能在括号块内（块内会提前展开）
depth = 0
call_line_depth = None
for number, line in enumerate(text.split("\n"), 1):
    if re.search(r"\)\s*do\s*\($|^\s*\(\s*$", line, re.I):
        depth += 1
    elif depth and re.match(r"^\s*\)", line):
        depth -= 1
    elif "%VIDEO_ARG%" in line:
        call_line_depth = depth
print("引用 %%VIDEO_ARG%% 时的括号深度: %s" % call_line_depth)
if call_line_depth not in (0, None):
    fail.append("%%VIDEO_ARG%% 在括号块内被引用（会提前展开成空值）")

# 7) 模板不许含本机绝对路径，且必须靠占位符由生成器注入
#    为什么单列一条：这个仓库要公开，模板里写死 C:\Users\<某人>\... 既不可移植
#    又会泄露开发者目录。占位符化之后必须防止有人改回去。
print("")
print("模板占位符与隐私检查:")
for banned, why in [
    ("C:\\Users\\", "硬编码的用户目录"),
    ("Administrator", "本机用户名"),
]:
    present = banned in text
    print("  含 %-16s（%s）: %s" % (banned, why, "是" if present else "否"))
    if present:
        fail.append("模板含%s（%s）—— 应改用占位符" % (banned, why))

PLACEHOLDERS = ("{SCRIPT}", "{DOCTOR}", "{PY_CHAIN}", "{PY_LIST_ECHO}")
for token in PLACEHOLDERS:
    ok = token in text
    print("  含占位符 %-16s: %s" % (token, "是" if ok else "否"))
    if not ok:
        fail.append("模板缺少占位符 %s" % token)

# 8) 生成器要能把占位符全部填掉，且产物是 GBK + CRLF
sys.path.insert(0, os.path.join(os.path.dirname(SRC)))
spec = importlib.util.spec_from_file_location(
    "batgen", os.path.join(os.path.dirname(SRC), "_make_bats.py"))
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

candidates = gen.python_candidates()
print("  本机探测到的 Python 候选: %d 个" % len(candidates))
if not candidates:
    fail.append("生成器在本机没探测到任何 Python 候选（生成的 bat 必然失败）")

rendered = gen.render(text, "cn", "国内版 WorkBuddy（--target cn）", candidates)
print("  生成物可按 GBK 解码: 是")
for token in PLACEHOLDERS:
    if token in rendered.decode("gbk"):
        fail.append("生成后仍残留占位符 %s（生成器没替换干净）" % token)
bare_lf = rendered.count(b"\n") - rendered.count(b"\r\n")
print("  生成物行尾: CRLF %d / 裸 LF %d" % (rendered.count(b"\r\n"), bare_lf))
if bare_lf:
    fail.append("生成的 bat 含裸 LF（cmd 会解析异常）")
if rendered[:3] == b"\xef\xbb\xbf":
    fail.append("生成的 bat 带 UTF-8 BOM")

# 生成物里 PY 候选不能重复展开（曾经的 bug：注释里写了占位符名被一起替换）
body = rendered.decode("gbk")
py_sets = [line for line in body.split("\r\n") if line.startswith('set "PY=')]
no_py = [line for line in body.split("\r\n") if line.strip() == "goto :no_py"]
print("  生成物里 set \"PY= 行数: %d（应为候选数 +1 个置空）" % len(py_sets))
if len(py_sets) != len(candidates) + 1:
    fail.append("PY 候选被重复展开：%d 行，预期 %d 行"
                % (len(py_sets), len(candidates) + 1))
if len(no_py) != 1:
    fail.append("goto :no_py 出现 %d 次，应为 1 次" % len(no_py))

print("")
if fail:
    print("检查未通过：")
    for item in fail:
        print("  - %s" % item)
    raise SystemExit(1)
print("批处理静态检查全部通过 ✅")
