#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 美化一键注入器（纯 Python 3，不依赖 PowerShell，也不依赖 Node）

为什么重写：原来的一键脚本走的是  .bat → PowerShell → Node  三层，
WorkBuddy 更新后主进程被**不带调试端口**地重启，CDP 通道没了，
而 PowerShell 那层又容易受 PATH / 执行策略 / CIM 状态影响，一断就整条链断。

本脚本自己把整条链做掉：
  1. 找 WorkBuddyAI.exe        （注册表 DisplayIcon / InstallLocation + 常见候选路径 + 运行中进程）
  2. 探测 CDP 9347             （直连 socket，绕开系统代理）
  3. 不在就强杀 + 带 --remote-debugging-port 重启
  4. 等渲染进程就绪            （轮询 /json/list，匹配 …/app.asar/renderer/index.html）
  5. 注入皮肤                  （复用 renderer.mjs 里 installInRenderer 的**源码文本**，
                                在渲染进程里执行；主题表由本脚本按同样的规则从磁盘读取）
  6. 校验并给出退出码          （0=成功，非 0=失败）

设计要点：
  * installInRenderer 是一段「在浏览器里跑」的函数，Node 侧只是
    `(${installInRenderer.toString()})(payload)` 把它字符串化后丢进去。
    而它正好占用了 renderer.mjs 从第 16 行到文件末尾 —— 所以直接按
    「从 `async function installInRenderer(data) {` 到 EOF」切片即可，
    不需要解析 JS（JS 的花括号配对会被字符串/注释/正则干扰，绝不能手写）。
  * WebSocket 客户端是手写的（约 120 行），只为满足 CDP 的 Runtime.evaluate。
    握手时**故意不发 Origin 头** —— Chromium 对没带 Origin 的本地连接是放行的，
    带了才需要 --remote-allow-origins。
"""

import base64
import ctypes
import functools
import hashlib
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time
import winreg
from ctypes import wintypes

# 中文控制台是 cp936，不要强推 utf-8；只做降级保护
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

# ============================================================================
# 常量（对应 scripts/lib/constants.mjs）
# ============================================================================
VERSION = "1.5.0"
DEFAULT_PORT = 9347
RENDERER_SUFFIX = "/app.asar/renderer/index.html"
STYLE_ID = "workbuddy-ambient-skin-style"
HOST_ID = "workbuddy-ambient-skin-host"
STATE_KEY = "__WORKBUDDY_AMBIENT_SKIN__"
MAX_IMAGE_BYTES = 15 * 1024 * 1024

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_ROOT = os.path.join(SKILL_ROOT, "assets")
BUNDLED_THEMES_ROOT = os.path.join(ASSETS_ROOT, "themes")
CSS_PATH = os.path.join(ASSETS_ROOT, "ambient.css")
# 「资料库」面板是一个**跨域 iframe**（https://www.workbuddy.cn/space/home），
# 父文档 CSS 进不去。它自己是一套 PC 端设计系统（类名全带 -pc 后缀），
# 所以单独一个样式表，由 inject_space_iframe() 注入到它的 document 里。
SPACE_CSS_PATH = os.path.join(ASSETS_ROOT, "space-glass.css")
SPACE_STYLE_ID = "wbas-space-glass"
SPACE_HTML_CLASS = "wbas-space"
# 自愈器：住进**主渲染进程**，监听资料库 iframe 的 load 事件后自己连 CDP 补注入。
# 为什么这是首选方案（2026-09-17 实测逐条确认）：
#   * 资料库 iframe 跨域 → 父文档读不到它的 DOM（SecurityError）；
#   * iframe 自己的 CDP target 上 Page.addScriptToEvaluateOnNewDocument
#     返回 identifier 但**永不触发**（OOPIF 限制）→ 没法原生持久化；
#   * 父文档**能**监听到 iframe 的 load 事件（实测 ticks 0→2）；
#   * 渲染进程里 fetch('http://127.0.0.1:PORT/json/list') **能成功**（不受同源限制）；
#   * 渲染进程的 WebSocket 会自带 Origin 头 → CDP 返回 403，
#     除非应用带 --remote-allow-origins=* 启动（launch_with_cdp 已加）。
# 于是：主渲染进程监听 load → fetch 找 target → WebSocket 注入 <style>。
# 全程在渲染进程内部，不需要任何后台常驻进程。
SPACE_SELFHEAL_PATH = os.path.join(ASSETS_ROOT, "space-selfheal.js")
SPACE_SELFHEAL_MARK = "__wbasSpaceSelfHeal"
RENDERER_MJS = os.path.join(SKILL_ROOT, "scripts", "lib", "renderer.mjs")

PREFIX = "workbuddy-ambient-skin."
LOG_PATH = None


def log(message):
    line = str(message)
    print(line, flush=True)
    if LOG_PATH:
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:
            pass


ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
HEX_RE = re.compile(r"^#[0-9a-f]{6}$", re.I)
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
SAFE_AREAS = ("auto", "left", "right", "center", "none")
APPEARANCES = ("auto", "light", "dark")
MATERIAL_STYLES = ("ambient", "studio")
MAX_DIMENSION = 16384
MAX_PIXELS = 50_000_000

DEFAULT_PALETTE = {"accent": "#78A7FF", "secondary": "#A78BFA", "surface": "#11151F", "text": "#F2F5FA"}
PALETTE_KEYS = ("accent", "secondary", "surface", "text")


class ThemeError(Exception):
    pass


def nz(mapping, key, default):
    """对应 JS 的 `??`：只有 None 才回落（空字符串要保留）。"""
    value = mapping.get(key) if isinstance(mapping, dict) else None
    return default if value is None else value


def _record(value, label):
    if not isinstance(value, dict):
        raise ThemeError("%s must be an object" % label)
    return value


def _bounded(value, fallback, lo=0.0, hi=1.0):
    selected = fallback if value is None else value
    if isinstance(selected, bool) or not isinstance(selected, (int, float)):
        raise ThemeError("value must be between %s and %s" % (lo, hi))
    selected = float(selected)
    if selected != selected or selected in (float("inf"), float("-inf")) or selected < lo or selected > hi:
        raise ThemeError("value must be between %s and %s" % (lo, hi))
    return selected


def validate_theme(source):
    _record(source, "theme")
    if source.get("schemaVersion") != 1:
        raise ThemeError("unsupported theme schema")
    theme_id = source.get("id")
    if not isinstance(theme_id, str) or not ID_RE.match(theme_id):
        raise ThemeError("theme id must use lowercase letters, numbers, and hyphens")
    name = source.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ThemeError("theme name is required")
    description = nz(source, "description", "")
    if not isinstance(description, str) or len(description) > 120:
        raise ThemeError("theme description must be a string up to 120 characters")
    appearance = nz(source, "appearance", "auto")
    if appearance not in APPEARANCES:
        raise ThemeError("appearance must be auto, light, or dark")

    palette_input = "auto" if source.get("palette") == "auto" else _record(nz(source, "palette", DEFAULT_PALETTE), "palette")

    def validate_palette(candidate, label):
        out = {}
        for key in PALETTE_KEYS:
            value = nz(candidate, key, DEFAULT_PALETTE[key])
            if not isinstance(value, str) or not HEX_RE.match(value):
                raise ThemeError("%s.%s must be a six-digit hex color" % (label, key))
            out[key] = value.upper()
        return out

    palette = "auto"
    if palette_input != "auto":
        dual = ("light" in palette_input) or ("dark" in palette_input)
        if dual:
            palette = {
                "light": validate_palette(_record(nz(palette_input, "light", {}), "palette.light"), "palette.light"),
                "dark": validate_palette(_record(nz(palette_input, "dark", {}), "palette.dark"), "palette.dark"),
            }
        else:
            palette = validate_palette(palette_input, "palette")

    art = _record(nz(source, "art", {}), "art")
    safe_area = nz(art, "safeArea", "auto")
    if safe_area not in SAFE_AREAS:
        raise ThemeError("art.safeArea is invalid")
    modes = _record(nz(source, "modes", {}), "modes")
    material = _record(nz(source, "material", {}), "material")
    material_style = nz(material, "style", "ambient")
    if material_style not in MATERIAL_STYLES:
        raise ThemeError("material.style must be ambient or studio")
    background = nz(source, "background", None)
    if background is not None:
        if not isinstance(background, str) or len(background) > 1000 or re.search(r"[;{}]", background):
            raise ThemeError("background must be a safe CSS image value")

    studio = material_style == "studio"
    return {
        "schemaVersion": 1,
        "id": theme_id,
        "name": name.strip(),
        "description": description.strip(),
        "image": safe_relative_image(nz(source, "image", None)),
        "background": background,
        "appearance": appearance,
        "palette": palette,
        "art": {
            "focusX": _bounded(nz(art, "focusX", None), 0.5),
            "focusY": _bounded(nz(art, "focusY", None), 0.5),
            "safeArea": safe_area,
        },
        "modes": {
            "homeOpacity": _bounded(nz(modes, "homeOpacity", None), 1.0),
            "workOpacity": _bounded(nz(modes, "workOpacity", None), 1.0),
            "detailOpacity": _bounded(nz(modes, "detailOpacity", None), 1.0),
            "sidebarOpacity": _bounded(nz(modes, "sidebarOpacity", None), 0.88),
        },
        "material": {
            "style": material_style,
            "panelOpacity": _bounded(nz(material, "panelOpacity", None), 0.82 if studio else 0.90, 0.5, 1.0),
            "cardOpacity": _bounded(nz(material, "cardOpacity", None), 0.74 if studio else 0.90, 0.45, 1.0),
            "blur": _bounded(nz(material, "blur", None), 24.0 if studio else 18.0, 0.0, 40.0),
            "radius": _bounded(nz(material, "radius", None), 18.0 if studio else 12.0, 6.0, 28.0),
            "borderStrength": _bounded(nz(material, "borderStrength", None), 0.14 if studio else 0.12, 0.0, 0.7),
            "shadowStrength": _bounded(nz(material, "shadowStrength", None), 0.10 if studio else 0.08, 0.0, 0.5),
        },
    }


def safe_relative_image(value):
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ThemeError("image must be a relative path inside the theme directory")
    if os.path.isabs(value) or re.match(r"^[a-zA-Z]:", value):
        raise ThemeError("image must be a relative path inside the theme directory")
    if any(part == ".." for part in re.split(r"[\\/]+", value)):
        raise ThemeError("image must be a relative path inside the theme directory")
    if os.path.splitext(value)[1].lower() not in IMAGE_EXT:
        raise ThemeError("image must be PNG, JPEG, or WebP")
    return value


def _inside(root, candidate):
    try:
        rel = os.path.relpath(candidate, root)
    except ValueError:
        return False
    return rel not in ("", "..") and not rel.startswith(".." + os.sep) and not os.path.isabs(rel)


def _dimensions(data, ext):
    """图片尺寸解析（PNG / JPEG / WebP），对应 theme.mjs 的 dimensions()。"""
    def u16be(o):
        return data[o] * 256 + data[o + 1]

    def u16le(o):
        return data[o] + data[o + 1] * 256

    def u24le(o):
        return data[o] + data[o + 1] * 256 + data[o + 2] * 65536

    def u32be(o):
        return (data[o] << 24) + (data[o + 1] << 16) + (data[o + 2] << 8) + data[o + 3]

    if ext == ".png" and len(data) >= 24 and data[1:4] == b"PNG" and data[12:16] == b"IHDR":
        return u32be(16), u32be(20)
    if ext in (".jpg", ".jpeg") and len(data) >= 2 and data[0] == 0xFF and data[1] == 0xD8:
        markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
        offset = 2
        while offset + 9 < len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in (0xDA, 0xD9):
                break
            if marker == 0x01 or 0xD0 <= marker <= 0xD8:
                continue
            if offset + 2 > len(data):
                break
            length = u16be(offset)
            if length < 2 or offset + length > len(data):
                break
            if marker in markers:
                return u16be(offset + 5), u16be(offset + 3)
            offset += length
    if ext == ".webp" and len(data) >= 30 and data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        kind = data[12:16]
        if kind == b"VP8X":
            return u24le(24) + 1, u24le(27) + 1
        if kind == b"VP8L" and data[20] == 0x2F:
            return (1 + data[21] + ((data[22] & 0x3F) << 8),
                    1 + (data[22] >> 6) + (data[23] << 2) + ((data[24] & 0x0F) << 10))
        if kind == b"VP8 " and data[23] == 0x9D and data[24] == 0x01 and data[25] == 0x2A:
            return u16le(26) & 0x3FFF, u16le(28) & 0x3FFF
    return None


def load_theme(theme_dir):
    root = os.path.abspath(theme_dir)
    with open(os.path.join(root, "theme.json"), "r", encoding="utf-8") as handle:
        manifest = validate_theme(json.load(handle))
    image_path = None
    image_data_url = None
    art_key = None
    if manifest["image"]:
        image_path = os.path.abspath(os.path.join(root, manifest["image"]))
        if not _inside(root, image_path):
            raise ThemeError("theme image escapes its directory")
        real_root = os.path.realpath(root)
        real_image = os.path.realpath(image_path)
        if not _inside(real_root, real_image):
            raise ThemeError("theme image symlink escapes its directory")
        size = os.path.getsize(real_image)
        if not os.path.isfile(real_image) or size < 1 or size > MAX_IMAGE_BYTES:
            raise ThemeError("theme image is empty or exceeds 15 MB")
        ext = os.path.splitext(real_image)[1].lower()
        with open(real_image, "rb") as handle:
            data = handle.read()
        dims = _dimensions(data, ext)
        if dims is None:
            raise ThemeError("image dimensions are unsupported or exceed 50 megapixels")
        width, height = dims
        if width < 1 or height < 1 or width > MAX_DIMENSION or height > MAX_DIMENSION or width * height > MAX_PIXELS:
            raise ThemeError("image dimensions are unsupported or exceed 50 megapixels")
        art_key = hashlib.sha256(data).hexdigest()[:24]
        mime = "image/png" if ext == ".png" else ("image/webp" if ext == ".webp" else "image/jpeg")
        image_data_url = "data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii"))
    return {"root": root, "manifest": manifest, "imagePath": image_path,
            "imageDataUrl": image_data_url, "artKey": art_key}


def _collation_locale():
    """取系统默认区域名（形如 zh-CN）。

    用 GetUserDefaultLocaleName，不用 locale.getdefaultlocale()
    —— 后者在 Python 3.13 已弃用，会在 stderr 上打 DeprecationWarning。
    """
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetUserDefaultLocaleName.restype = ctypes.c_int
        k32.GetUserDefaultLocaleName.argtypes = [wintypes.LPWSTR, ctypes.c_int]
        buffer = ctypes.create_unicode_buffer(85)      # LOCALE_NAME_MAX_LENGTH
        if k32.GetUserDefaultLocaleName(buffer, 85):
            return buffer.value
    except Exception:
        pass
    return "zh-CN"


_COMPARE_STRING_EX = None


def _collation_cmp(left, right):
    """按系统排序规则比较两个主题名。

    上游用的是 JS 的 `a.manifest.name.localeCompare(b.manifest.name)` ——
    走 ICU，中文按拼音排（晨 chén < 初 chū < 哆 duō < 雷 léi）。
    Python 默认按码位排（初 < 哆 < 晨 < 雷），顺序会不一样。
    实测 Windows 的 CompareStringEx 与 Node 的 ICU 结果**完全一致**，
    所以这里直接借用系统排序规则；取不到就退回码位比较。
    """
    global _COMPARE_STRING_EX
    if _COMPARE_STRING_EX is None:
        try:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CompareStringEx.restype = ctypes.c_int
            k32.CompareStringEx.argtypes = [
                wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPCWSTR, ctypes.c_int,
                wintypes.LPCWSTR, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ]
            _COMPARE_STRING_EX = k32.CompareStringEx
        except Exception:
            _COMPARE_STRING_EX = False
    if _COMPARE_STRING_EX:
        try:
            result = _COMPARE_STRING_EX(_collation_locale(), 0, left, -1, right, -1, None, None, None)
            if result == 1:      # CSTR_LESS_THAN
                return -1
            if result == 3:      # CSTR_GREATER_THAN
                return 1
            if result == 2:      # CSTR_EQUAL
                return 0
        except Exception:
            pass
    return -1 if left < right else (1 if left > right else 0)


def list_themes(roots):
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if not os.path.isdir(path):
                continue
            try:
                found.append(load_theme(path))
            except Exception:
                continue  # 与上游一致：忽略不完整的主题
    unique = {}
    for theme in found:
        unique[theme["manifest"]["id"]] = theme
    return sorted(unique.values(),
                  key=functools.cmp_to_key(lambda a, b: _collation_cmp(a["manifest"]["name"], b["manifest"]["name"])))


def theme_entries(themes):
    """对应 injector.mjs 的 entries()：manifest 展开 + imageDataUrl + artKey。"""
    out = []
    for theme in themes:
        entry = dict(theme["manifest"])
        entry["imageDataUrl"] = theme["imageDataUrl"]
        entry["artKey"] = theme["artKey"]
        out.append(entry)
    return out


def read_css():
    """读取 ambient.css。

    必须二进制读再解码：Python 文本模式会把 CRLF 归一成 LF，
    注入的 CSS 就和磁盘上的文件不一致了（一致性校验就是这么发现的）。
    """
    with open(CSS_PATH, "rb") as handle:
        return handle.read().decode("utf-8")


def extract_install_source():
    """从 renderer.mjs 切出 installInRenderer 的源码文本。

    上游是 `(${installInRenderer.toString()})(payload)`，而该函数在 renderer.mjs
    里从第 16 行一直延伸到文件末尾（末尾那个 `}` 就是它的收尾）。
    所以按「首个匹配行 → EOF」切片即可，绝不手写 JS 花括号配对。
    同样用二进制读，避免 CRLF 被归一化。
    """
    with open(RENDERER_MJS, "rb") as handle:
        source = handle.read().decode("utf-8")
    marker = "async function installInRenderer(data) {"
    index = source.find(marker)
    if index < 0:
        raise ThemeError("在 renderer.mjs 里找不到 installInRenderer（上游结构可能变了）")
    body = source[index:].rstrip()
    if not body.endswith("}"):
        raise ThemeError("installInRenderer 的切片没有以 } 收尾（上游结构可能变了）")
    return body


def _js_numbers(value):
    """把整数值的 float 收敛成 int。

    JS 的 JSON.stringify(1.0) 得到 "1"，而 Python 的 json.dumps(1.0) 得到 "1.0"。
    两者对 JS 解析完全等价，但为了让 Python 产出的表达式和 Node 逐字节一致，
    这里统一成 JS 的写法。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    if isinstance(value, dict):
        return {key: _js_numbers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_js_numbers(item) for item in value]
    return value


def build_install_expression(css, themes, active_id="", force=True):
    payload = {
        "css": css,
        "themes": theme_entries(themes),
        "activeId": active_id,
        "force": force,
        "STYLE_ID": STYLE_ID,
        "HOST_ID": HOST_ID,
        "STATE_KEY": STATE_KEY,
        "VERSION": VERSION,
    }
    dumped = json.dumps(_js_numbers(payload), ensure_ascii=False, separators=(",", ":"))
    return "(%s)(%s)" % (extract_install_source(), dumped)


class CdpError(Exception):
    pass


def _dechunk(body, strict=False):
    """解 chunked 编码。strict=True 时，若还没读到结束块就返回 None。"""
    out = bytearray()
    index = 0
    while index < len(body):
        end = body.find(b"\r\n", index)
        if end < 0:
            return None if strict else bytes(out)
        try:
            size = int(body[index:end].split(b";")[0], 16)
        except ValueError:
            return None if strict else bytes(out)
        if size == 0:
            return bytes(out)                      # 结束块，收工
        if end + 2 + size + 2 > len(body):
            return None if strict else bytes(out)
        out += body[end + 2:end + 2 + size]
        index = end + 2 + size + 2
    return None if strict else bytes(out)


def _read_http_response(sock):
    """读一个完整的 HTTP 响应，返回 (head 文本, body 字节)。

    ⚠️ 不能"读到 EOF 为止"：Chromium 的 /json/* 端点**不理会
    Connection: close**，会一直保持连接，那样读下去只会卡到 socket 超时
    （实测 Chrome 152 就是这样，一开始把 cdp_up() 卡成了 False）。
    正确做法是按 Content-Length 判断读完，chunked 则等结束块。
    """
    buffer = bytearray()
    head_end = -1
    content_length = None
    chunked = False
    while True:
        if head_end < 0:
            index = buffer.find(b"\r\n\r\n")
            if index >= 0:
                head_end = index + 4
                head = bytes(buffer[:index]).decode("latin-1")
                match = re.search(r"(?im)^Content-Length:\s*(\d+)", head)
                content_length = int(match.group(1)) if match else None
                chunked = re.search(r"(?im)^Transfer-Encoding:\s*chunked", head) is not None
        if head_end >= 0:
            body = bytes(buffer[head_end:])
            if content_length is not None and len(body) >= content_length:
                return bytes(buffer[:head_end]).decode("latin-1"), body[:content_length]
            if content_length is None and not chunked:
                pass                                # 没有长度信息，只能等 EOF
            elif chunked:
                done = _dechunk(body, strict=True)
                if done is not None:
                    return bytes(buffer[:head_end]).decode("latin-1"), done
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            break                                   # 超时也当作读完了，交给调用方判断
        if not chunk:
            break
        buffer += chunk

    if head_end < 0:
        raise CdpError("HTTP 响应不完整（没读到响应头）")
    body = bytes(buffer[head_end:])
    return bytes(buffer[:head_end]).decode("latin-1"), (_dechunk(body) if chunked else body)


def http_get_json(host, port, path, timeout=5.0):
    """直连 socket 的 HTTP GET —— 完全绕开 http_proxy 环境变量。"""
    request = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nAccept: application/json\r\nConnection: close\r\n\r\n"
               % (path, host, port)).encode("ascii")
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(request)
        head, body = _read_http_response(sock)
    status_line = head.split("\r\n")[0]
    if " 200 " not in status_line:
        raise CdpError("HTTP %s" % status_line)
    return json.loads(body.decode("utf-8", "replace"))


def renderer_targets(port, host="127.0.0.1", all_types=False):
    """列出渲染进程 target。

    默认只返回 `type == "page"` 的那个主文档（本地 app.asar/renderer/index.html）。
    `all_types=True` 时**不过滤类型** —— 用来拿「资料库」那种跨域 OOPIF
    （`<iframe class="space-panel-iframe" src="https://www.workbuddy.cn/space/home">`）：
    它在 CDP 里是一个独立 target，`type == "iframe"`，父文档的 CSS 进不去，
    只能 attach 到它自己这个 target 才能注入。
    """
    try:
        listing = http_get_json(host, port, "/json/list")
    except Exception:
        return []
    if not isinstance(listing, list):
        return []
    out = []
    for item in listing:
        url = item.get("url") or ""
        if not item.get("webSocketDebuggerUrl"):
            continue
        if all_types:
            out.append(item)
            continue
        if item.get("type") != "page":
            continue
        pathname = url.split("?", 1)[0].split("#", 1)[0]
        if pathname.endswith(RENDERER_SUFFIX):
            out.append(item)
    return out


def _recv_exact(sock, count):
    buffer = bytearray()
    while len(buffer) < count:
        chunk = sock.recv(count - len(buffer))
        if not chunk:
            raise CdpError("WebSocket 连接被对端关闭")
        buffer += chunk
    return bytes(buffer)


def _ws_send_frame(sock, opcode, payload):
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", length)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", length)
    mask = os.urandom(4)
    header += mask
    masked = bytearray(payload)
    for index in range(length):
        masked[index] ^= mask[index & 3]
    sock.sendall(bytes(header) + bytes(masked))


def _ws_recv_message(sock):
    fragments = []
    opcode = None
    while True:
        b1, b2 = _recv_exact(sock, 2)
        fin = bool(b1 & 0x80)
        op = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", _recv_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack(">Q", _recv_exact(sock, 8))[0]
        mask = _recv_exact(sock, 4) if masked else None
        payload = _recv_exact(sock, length) if length else b""
        if mask:
            payload = bytes(payload[i] ^ mask[i & 3] for i in range(len(payload)))
        if op == 0x9:                       # ping → pong
            _ws_send_frame(sock, 0xA, payload)
            continue
        if op == 0xA:                       # pong
            continue
        if op == 0x8:                       # close
            raise CdpError("WebSocket 收到 close 帧")
        if op in (0x1, 0x2):
            opcode = op
            fragments.append(payload)
        elif op == 0x0:
            fragments.append(payload)
        if fin:
            return opcode, b"".join(fragments)


def _ws_handshake(sock, host, port, path):
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    # 故意不发 Origin：Chromium 对没有 Origin 的本地 WS 连接是放行的
    request = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, host, port, key)).encode("ascii")
    sock.sendall(request)
    buffer = bytearray()
    while b"\r\n\r\n" not in buffer:
        chunk = sock.recv(4096)
        if not chunk:
            raise CdpError("WebSocket 握手被关闭")
        buffer += chunk
        if len(buffer) > 65536:
            raise CdpError("WebSocket 握手响应异常")
    head = bytes(buffer).split(b"\r\n\r\n", 1)[0].decode("latin-1")
    if " 101 " not in head.split("\r\n")[0]:
        raise CdpError("WebSocket 握手失败：%s" % head.split("\r\n")[0])


def cdp_evaluate(ws_url, expression, timeout=90.0):
    match = re.match(r"^wss?://([^/:]+):(\d+)(/.*)$", ws_url)
    if not match:
        raise CdpError("无法解析 webSocketDebuggerUrl: %s" % ws_url)
    host, port, path = match.group(1), int(match.group(2)), match.group(3)
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    try:
        _ws_handshake(sock, host, port, path)
        _ws_send_frame(sock, 0x1, json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
                "allowUnsafeEvalBlockedByCSP": True,
            },
        }).encode("utf-8"))
        deadline = time.time() + timeout
        while time.time() < deadline:
            op, payload = _ws_recv_message(sock)
            if op != 0x1:
                continue
            message = json.loads(payload.decode("utf-8", "replace"))
            if message.get("id") != 1:
                continue
            if "error" in message:
                raise CdpError(str(message["error"]))
            result = message.get("result") or {}
            if result.get("exceptionDetails"):
                details = result["exceptionDetails"]
                description = (details.get("exception") or {}).get("description") or details.get("text")
                raise CdpError(str(description))
            return (result.get("result") or {}).get("value")
        raise CdpError("等待 CDP 响应超时")
    finally:
        try:
            sock.close()
        except Exception:
            pass


def cdp_command(ws_url, method, params=None, timeout=30.0, session_id=None):
    """发送任意 CDP 命令并返回 result（cdp_evaluate 的通用版）。

    为什么需要它：资料库 iframe 会**自己重新加载**（路由跳转/HMR/重新登录），
    单纯往它 DOM 里插 <style> 会被整个冲掉。
    要持久化必须用 `Page.addScriptToEvaluateOnNewDocument`
    —— 那条命令不是 Runtime.evaluate，所以需要这个通用发送器。

    session_id：从 **browser 端点**连过去时，命令要带 sessionId 才会落到
    那个具体 target 上（Target.attachToTarget 的返回值）。连 page/iframe
    自己的 ws 端点时不用传。
    """
    match = re.match(r"^wss?://([^/:]+):(\d+)(/.*)$", ws_url)
    if not match:
        raise CdpError("无法解析 webSocketDebuggerUrl: %s" % ws_url)
    host, port, path = match.group(1), int(match.group(2)), match.group(3)
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    try:
        _ws_handshake(sock, host, port, path)
        payload = {"id": 1, "method": method, "params": params or {}}
        if session_id:
            payload["sessionId"] = session_id
        _ws_send_frame(sock, 0x1, json.dumps(payload).encode("utf-8"))
        deadline = time.time() + timeout
        while time.time() < deadline:
            op, payload = _ws_recv_message(sock)
            if op != 0x1:
                continue
            message = json.loads(payload.decode("utf-8", "replace"))
            if message.get("id") != 1:
                continue
            if "error" in message:
                raise CdpError(str(message["error"]))
            return message.get("result") or {}
        raise CdpError("等待 CDP 响应超时（%s）" % method)
    finally:
        try:
            sock.close()
        except Exception:
            pass


def browser_ws_url(port, host="127.0.0.1"):
    """拿 **browser 级** CDP 端点。

    为什么需要：iframe（OOPIF）自己的 ws 端点上，
    `Page.addScriptToEvaluateOnNewDocument` 返回 identifier 却**永不触发**
    （Chromium 对 OOPIF 的已知限制，实测确认）。
    但 browser 端点可以用 `Target.attachToTarget` 拿到 sessionId，
    再借 sessionId 让命令落到 iframe 上 —— 这是唯一可能"原生持久化"的路。
    """
    info = http_get_json(host, port, "/json/version")
    return info.get("webSocketDebuggerUrl")


class CdpConnection(object):
    """**长连接**的 CDP 客户端，支持 sessionId 路由。

    为什么不能用 cdp_command 凑：`Target.attachToTarget` 返回的 sessionId
    只在**创建它的那条 WebSocket 连接**上有效。cdp_command 每次调用都
    新建连接、发一条、关掉 —— sessionId 一跨连接就是
    `Session with given id not found`（实测踩过）。
    所以要走 browser 端点做 session 级操作，必须自己维持一条长连接。
    """

    def __init__(self, ws_url, timeout=30.0):
        match = re.match(r"^wss?://([^/:]+):(\d+)(/.*)$", ws_url)
        if not match:
            raise CdpError("无法解析 webSocketDebuggerUrl: %s" % ws_url)
        self.host, self.port, self.path = match.group(1), int(match.group(2)), match.group(3)
        self.timeout = timeout
        self.sock = None
        self._next_id = 0
        self.events = []          # 收命令响应时顺手攒下的事件

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def open(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        _ws_handshake(self.sock, self.host, self.port, self.path)
        return self

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def send(self, method, params=None, session_id=None, timeout=None):
        """发一条命令并等它的响应（期间收到的事件存到 self.events）。"""
        if self.sock is None:
            raise CdpError("连接还没打开")
        self._next_id += 1
        request_id = self._next_id
        payload = {"id": request_id, "method": method, "params": params or {}}
        if session_id:
            payload["sessionId"] = session_id
        _ws_send_frame(self.sock, 0x1, json.dumps(payload).encode("utf-8"))
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            op, raw = _ws_recv_message(self.sock)
            if op != 0x1:
                continue
            message = json.loads(raw.decode("utf-8", "replace"))
            if message.get("id") != request_id:
                if message.get("method"):
                    self.events.append(message)
                continue
            if "error" in message:
                raise CdpError(str(message["error"]))
            return message.get("result") or {}
        raise CdpError("等待 CDP 响应超时（%s）" % method)

    def evaluate(self, expression, session_id=None, timeout=None):
        result = self.send("Runtime.evaluate", {
            "expression": expression,
            "awaitPromise": True,
            "returnByValue": True,
            "allowUnsafeEvalBlockedByCSP": True,
        }, session_id=session_id, timeout=timeout)
        return (result.get("result") or {}).get("value")


def cdp_up(port=DEFAULT_PORT, host="127.0.0.1"):
    try:
        http_get_json(host, port, "/json/version", timeout=2.0)
        return True
    except Exception:
        return False


def wait_for_renderers(port=DEFAULT_PORT, timeout=90.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        targets = renderer_targets(port)
        if targets:
            return targets
        time.sleep(0.5)
    return []


TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def _kernel32():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.restype = wintypes.BOOL
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32NextW.restype = wintypes.BOOL
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    k32.TerminateProcess.restype = wintypes.BOOL
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    return k32


def _process_image_path(k32, pid):
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if k32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return None
    finally:
        k32.CloseHandle(handle)


def list_workbuddy_processes(exe_path):
    """返回 exe 路径与目标一致的进程 (pid, path)。"""
    k32 = _kernel32()
    expected = os.path.normcase(os.path.abspath(exe_path))
    exe_name = os.path.basename(exe_path).lower()
    snapshot = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return []
    found = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not k32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return []
        while True:
            if entry.szExeFile.lower() == exe_name:
                path = _process_image_path(k32, entry.th32ProcessID)
                if path and os.path.normcase(os.path.abspath(path)) == expected:
                    found.append((int(entry.th32ProcessID), path))
            if not k32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        k32.CloseHandle(snapshot)
    return found


def kill_process(pid):
    k32 = _kernel32()
    handle = k32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        return False
    try:
        return bool(k32.TerminateProcess(handle, 1))
    finally:
        k32.CloseHandle(handle)


def force_quit(exe_path, timeout=15.0):
    processes = list_workbuddy_processes(exe_path)
    if not processes:
        return {"wasRunning": False, "stopped": True, "pids": []}
    killed = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = list_workbuddy_processes(exe_path)
        if not current:
            break
        for pid, _ in current:
            if kill_process(pid):
                killed.append(pid)
        time.sleep(0.15)
    remaining = list_workbuddy_processes(exe_path)
    if remaining:
        raise CdpError("有 WorkBuddy 进程杀不掉，仍在运行: %s" % [pid for pid, _ in remaining])
    time.sleep(2.0)   # 等进程族彻底退出，避免端口/单实例锁冲突
    if list_workbuddy_processes(exe_path):
        raise CdpError("WorkBuddy 在关闭后自己又起来了")
    return {"wasRunning": True, "stopped": True, "pids": killed}


def launch_with_cdp(exe_path, port=DEFAULT_PORT):
    """带 CDP 端口启动 WorkBuddy。

    `--remote-allow-origins=*` 是**必须的**（2026-09-17 加）：
    Chromium 从 111 起，任何带 Origin 头的 CDP WebSocket 连接都会 403
    （实测：不带 Origin → 101；带 file:// 或 https://… → 403 Forbidden）。
    我们 Python 侧手写 WS 握手、故意不发 Origin，所以一直没踩到；
    但**渲染进程里的 JS** 用 WebSocket 连 CDP 时浏览器会自动加 Origin，
    于是必然 403。加上这个开关后，渲染进程就能自己连 CDP 了 ——
    这条能力是「资料库 iframe 透明化不再依赖常驻进程」的关键：
    父文档能监听 iframe 的 load 事件，重载后由它自己连 CDP 补注入。
    """
    creationflags = 0
    for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
        creationflags |= getattr(subprocess, name, 0)
    process = subprocess.Popen(
        [exe_path,
         "--remote-debugging-address=127.0.0.1",
         "--remote-debugging-port=%d" % port,
         "--remote-allow-origins=*"],
        close_fds=True, creationflags=creationflags,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return {"pid": process.pid, "port": port, "executable": exe_path}


ALLOWED_EXE_NAMES = ("workbuddy.exe", "workbuddyai.exe")


def _reg_value(key, name):
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def registry_candidates():
    out = []
    targets = (
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_32KEY),
    )
    for hive, sub, flags in targets:
        try:
            root = winreg.OpenKey(hive, sub, 0, winreg.KEY_READ | flags)
        except OSError:
            continue
        try:
            count = winreg.QueryInfoKey(root)[0]
            for index in range(count):
                try:
                    name = winreg.EnumKey(root, index)
                    key = winreg.OpenKey(root, name)
                except OSError:
                    continue
                try:
                    display = _reg_value(key, "DisplayName")
                    if not display or "workbuddy" not in str(display).lower():
                        continue
                    icon = _reg_value(key, "DisplayIcon")
                    if icon:
                        cleaned = re.sub(r",\s*-?\d+\s*$", "", str(icon)).strip().strip('"')
                        if cleaned:
                            out.append(cleaned)
                    location = _reg_value(key, "InstallLocation")
                    if location:
                        for exe_name in ("WorkBuddy.exe", "WorkBuddyAI.exe"):
                            out.append(os.path.join(str(location), exe_name))
                finally:
                    winreg.CloseKey(key)
        finally:
            winreg.CloseKey(root)
    return out


def other_drive_candidates(local):
    """把 %LOCALAPPDATA% 的相对尾部套到其它盘符上再试一遍。

    为什么需要：应用可能被装在非系统盘（本机实测 WorkBuddy 就在 D 盘），
    而 %LOCALAPPDATA% 只指向系统盘。写死某个盘符既不可移植、又会把
    开发者的用户目录带进公开仓库，所以改成「扫已存在的盘符 + 复用同一段
    相对路径」—— 换台机器同样适用。
    """
    drive, tail = os.path.splitdrive(os.path.abspath(local))
    if not drive:
        return []
    tail = tail.lstrip("\\/")
    out = []
    for letter in "DEFGHIJK":
        root = "%s:\\" % letter
        if root.lower() == drive.lower() or not os.path.isdir(root):
            continue
        base = os.path.join(root, tail, "Programs")
        out.append(os.path.join(base, "WorkBuddyAI", "WorkBuddyAI.exe"))
        out.append(os.path.join(base, "WorkBuddy", "WorkBuddy.exe"))
    return out


def candidate_paths():
    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    program_files = os.environ.get("ProgramFiles") or r"C:\Program Files"
    program_files_x86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    out = [
        os.environ.get("WORKBUDDY_EXE"),
        os.path.join(local, "workbuddy", "WorkBuddy.exe"),
        os.path.join(local, "Programs", "workbuddy", "WorkBuddy.exe"),
        os.path.join(local, "Programs", "WorkBuddyAI", "WorkBuddyAI.exe"),
        os.path.join(local, "Programs", "WorkBuddy AI", "WorkBuddyAI.exe"),
        os.path.join(program_files, "WorkBuddy", "WorkBuddy.exe"),
        os.path.join(program_files_x86, "WorkBuddy", "WorkBuddy.exe"),
    ]
    out.extend(other_drive_candidates(local))
    out.extend(registry_candidates())
    return out


def running_exe_paths():
    """兜底：从正在运行的 WorkBuddy 进程里拿 exe 路径。"""
    k32 = _kernel32()
    snapshot = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return []
    out = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not k32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return []
        while True:
            if entry.szExeFile.lower() in ALLOWED_EXE_NAMES:
                path = _process_image_path(k32, entry.th32ProcessID)
                if path:
                    out.append(path)
            if not k32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        k32.CloseHandle(snapshot)
    return out


def locate_exe():
    seen = set()
    for candidate in candidate_paths() + running_exe_paths():
        if not candidate:
            continue
        path = os.path.abspath(str(candidate))
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        if os.path.basename(path).lower() not in ALLOWED_EXE_NAMES:
            continue
        if os.path.isfile(path):
            return path
    return None


# ---------------------------------------------------------------------------
# 变体识别与目标选择
#
# 同一台机器上**可以同时装国际版和国内版**（实测：WorkBuddyAI.exe 国际版 +
# WorkBuddy.exe 国内版，互斥量名不同，能同时跑）。这时"挑哪个"就成了必须显式
# 处理的问题 —— 否则按候选顺序撞上哪个算哪个，很容易把用户正在用的那个关掉。
#
# 变体判定不靠文件名猜，而是读安装目录里的
#   resources/app.asar.unpacked/cli/product.json
# 官方字段：国际版 `isOversea: true`；国内版没有这个字段。
# ---------------------------------------------------------------------------
VARIANT_OVERSEA = "oversea"
VARIANT_CN = "cn"
VARIANT_UNKNOWN = "unknown"

VARIANT_LABELS = {
    VARIANT_OVERSEA: "国际版（WorkBuddy AI）",
    VARIANT_CN: "国内版（WorkBuddy）",
    VARIANT_UNKNOWN: "未知变体",
}


def read_variant(exe_path):
    """读 product.json 判断这个安装是国际版还是国内版。"""
    base = os.path.dirname(os.path.abspath(exe_path))
    manifest = os.path.join(base, "resources", "app.asar.unpacked", "cli", "product.json")
    try:
        with open(manifest, "rb") as handle:
            data = json.loads(handle.read().decode("utf-8"))
    except Exception:
        return VARIANT_UNKNOWN, None
    variant = VARIANT_OVERSEA if data.get("isOversea") is True else VARIANT_CN
    return variant, data


def discover_installs():
    """列出所有能找到的安装：{规范化的 exe 路径: (原始路径, 变体, productName)}。"""
    found = {}
    for candidate in candidate_paths() + running_exe_paths():
        if not candidate:
            continue
        path = os.path.abspath(str(candidate))
        key = os.path.normcase(path)
        if key in found:
            continue
        if os.path.basename(path).lower() not in ALLOWED_EXE_NAMES:
            continue
        if not os.path.isfile(path):
            continue
        variant, data = read_variant(path)
        found[key] = (path, variant, (data or {}).get("productName") or os.path.basename(path))
    return found


def running_counts():
    """{规范化的 exe 路径: 进程数}。"""
    counts = {}
    for path in running_exe_paths():
        key = os.path.normcase(os.path.abspath(path))
        counts[key] = counts.get(key, 0) + 1
    return counts


def choose_target(mode="auto"):
    """按 --target 选一个要注入的安装。

    mode:
      auto            —— 只跑了一个就用那个；两个都在跑时优先国际版
      oversea / cn    —— 按变体指定
      其它            —— 当作 exe 路径
    返回 (exe 路径, 说明文字) 或 (None, 原因)
    """
    installs = discover_installs()
    if not installs:
        return None, "没有找到任何 WorkBuddy 安装"

    counts = running_counts()

    if mode not in ("auto", VARIANT_OVERSEA, VARIANT_CN):
        key = os.path.normcase(os.path.abspath(mode))
        if key not in installs:
            return None, "指定的路径不是已安装的 WorkBuddy: %s" % mode
        return installs[key][0], "显式指定"

    if mode in (VARIANT_OVERSEA, VARIANT_CN):
        matched = [(k, v) for k, v in installs.items() if v[1] == mode]
        if not matched:
            return None, "没找到%s的安装" % VARIANT_LABELS[mode]
        # 同变体有多个就优先正在运行的那个
        matched.sort(key=lambda kv: -counts.get(kv[0], 0))
        return matched[0][1][0], "按变体指定"

    live = [k for k in installs if counts.get(k)]
    if len(live) == 1:
        return installs[live[0]][0], "唯一在运行的实例"
    if len(live) > 1:
        live.sort(key=lambda k: (installs[k][1] != VARIANT_OVERSEA, -counts.get(k, 0)))
        return installs[live[0]][0], "两个都在运行 → 默认取国际版"
    oversea = [k for k, v in installs.items() if v[1] == VARIANT_OVERSEA]
    pool = oversea or list(installs)
    pool.sort(key=lambda k: -counts.get(k, 0))
    return installs[pool[0]][0], "没有实例在运行 → 取国际版"


def describe_installs():
    installs = discover_installs()
    counts = running_counts()
    lines = []
    for key, (path, variant, product) in sorted(installs.items(), key=lambda kv: kv[1][1]):
        lines.append("      %-10s %-22s %s  （%d 个进程在跑）"
                     % (variant, product, path, counts.get(key, 0)))
    return lines


# ---------------------------------------------------------------------------
# 端口归属判定
#
# 两个版本都想用 9347，但端口只能被一个进程占。所以"端口通"不等于"通的是我要
# 注入的那个" —— 不判断就热注入，会注到另一个应用里去。
#
# 判定办法很简单也很可靠：渲染进程的 URL 里带着自己的安装目录，例如
#   file:///D:/.../Programs/WorkBuddyAI/resources/app.asar/renderer/index.html
#   file:///D:/.../Programs/WorkBuddy/resources/app.asar/renderer/index.html
# 拿安装目录名去匹配（带前后斜杠，避免 workbuddy 命中 workbuddyai）即可。
# ---------------------------------------------------------------------------
def install_marker(exe_path):
    return os.path.basename(os.path.dirname(os.path.abspath(exe_path))).lower()


def cdp_belongs_to(port, exe_path):
    marker = "/" + install_marker(exe_path) + "/"
    for target in renderer_targets(port):
        url = (target.get("url") or "").replace("\\", "/").lower()
        if marker in url:
            return True
    return False


def port_is_free(port, host="127.0.0.1"):
    with socket.socket() as probe:
        try:
            probe.bind((host, port))
            return True
        except OSError:
            return False


def pick_port(preferred=DEFAULT_PORT, tries=12):
    """优先用默认端口；被占了就往后找一个空的。"""
    for port in range(preferred, preferred + tries):
        if port_is_free(port):
            return port
    return preferred


def find_live_port(exe_path, first=DEFAULT_PORT, tries=12):
    """在端口区间里找"已经属于这个安装"的 CDP 端口。

    为什么需要：两个版本共存时，国际版占着 9347，国内版只能在 9348/9349…
    如果只看 9347，就会误判成"目标没开调试端口" → 白白把用户的国内版重启一遍。
    实测踩过：国内版明明已经在 9348 上跑着且皮肤已注入，脚本还是重启了它。
    """
    for port in range(first, first + tries):
        if cdp_up(port) and cdp_belongs_to(port, exe_path):
            return port
    return None


SNAPSHOT_EXPR = """(() => {
  const out = {};
  for (let i = 0; i < localStorage.length; i += 1) {
    const k = localStorage.key(i);
    if (k && k.indexOf(%s) === 0) out[k] = localStorage.getItem(k);
  }
  return JSON.stringify(out);
})()""" % json.dumps(PREFIX)

RESTORE_EXPR = """(() => {
  const snap = %s;
  let added = 0;
  for (const k of Object.keys(snap)) {
    if (localStorage.getItem(k) === null) { localStorage.setItem(k, snap[k]); added += 1; }
  }
  return added;
})()"""


def snapshot_local_storage(targets):
    for target in targets:
        try:
            value = cdp_evaluate(target["webSocketDebuggerUrl"], SNAPSHOT_EXPR, timeout=20.0)
            if value:
                return value
        except Exception:
            continue
    return None


def restore_local_storage(targets, snapshot_json):
    if not snapshot_json:
        return None
    expression = RESTORE_EXPR % json.dumps(snapshot_json)
    for target in targets:
        try:
            return cdp_evaluate(target["webSocketDebuggerUrl"], expression, timeout=20.0)
        except Exception:
            continue
    return None


# 为什么需要单独一段：
#   资料库面板真正的渲染体是一个**跨域 iframe**
#     <iframe class="space-panel-iframe" src="https://www.workbuddy.cn/space/home?...">
#   父文档拿它的 contentDocument 得到 null（同源策略），
#   所以 ambient.css 里无论怎么写选择器都盖不住它 —— 它不是权重问题，是进不去。
#   （用户截图「进入后的资料库页面全白」就是这么来的。）
#
# 好在 Electron 把这个 OOPIF 暴露成了**独立的 CDP target**（type="iframe"），
#   可以 attach 到它自己的 target，往它自己的 document 插 <style>。
#
# 时机问题：iframe 是**懒加载**的 —— 用户不点开资料库就根本不存在，
#   而注入流程结束之后用户随时可能点开。应对方式（按可靠性排序）：
#     1) **自愈器**（install_space_selfheal，首选）：
#        往主渲染进程注入一小段 JS，它监听 iframe 的 load 事件，
#        发现样式没了就自己 fetch /json/list 找到 target、WebSocket 连上去补注入。
#        主渲染进程不 reload，所以这个哨兵一直活着 —— **不需要常驻外部进程**。
#        前提：应用要以 --remote-allow-origins=* 启动（见 launch_with_cdp）。
#     2) apply_space_css：注入时如果 iframe 恰好开着，立刻注入一次。
#     3) watch_space_css / spawn_space_watcher：外部轮询守护（旧方案，兜底）。
#
#   历史（2026-09-17 推翻的旧结论）：
#     * 曾经认为"父文档侧感知不到 iframe 的加载时机" —— 错的。
#       实测父文档给 iframe 挂 load 监听，reload 后**确实会触发**（ticks 0→2）。
#     * 曾经认为"父文档无法把 <style> 递进 iframe" —— 部分对的：
#       直接摸 contentDocument/contentWindow 确实 SecurityError；
#       但**通过本地 CDP WebSocket** 可以，因为 WebSocket 不受同源策略约束。
#     * 曾经以为 iframe 会"自己 reload"（当时看到 navigation.type == "reload"）——
#       那是用户导航触发的，静置 60 秒实测 age 单调递增、样式一直在。

SPACE_INSTALL_EXPR = """(() => {
  document.documentElement.classList.add(%s);
  let style = document.getElementById(%s);
  if (!style) {
    style = document.createElement('style');
    style.id = %s;
    (document.head || document.documentElement).appendChild(style);
  }
  style.textContent = %s;
  return JSON.stringify({ installed: true, bytes: style.textContent.length,
                          html: document.documentElement.className });
})()"""


def read_space_selfheal():
    with open(SPACE_SELFHEAL_PATH, "rb") as handle:
        return handle.read().decode("utf-8")


def space_iframe_targets(port, host="127.0.0.1"):
    """取出所有 type == 'iframe' 的 CDP target（资料库面板）。"""
    return [t for t in renderer_targets(port, host=host, all_types=True)
            if t.get("type") == "iframe" and t.get("webSocketDebuggerUrl")]


def read_space_css():
    with open(SPACE_CSS_PATH, "rb") as handle:
        return handle.read().decode("utf-8")


def apply_space_css(port, host="127.0.0.1", log=None, quiet=False):
    """给所有已存在的资料库 iframe 注入玻璃样式。

    返回成功注入的个数。iframe 没打开时返回 0（**不是错误**）。

    ⚠️ 必须**可重复调用**：资料库 iframe 会自己 reload，一 reload 我们插进去的
    `<style>` 和 `html.wbas-space` 就全没了。
    所以这个函数要能被 watcher 反复调用，且重复调用要幂等、不要刷日志。

    ⚠️ 试过但**不可行**的持久化方案：`Page.addScriptToEvaluateOnNewDocument`。
    OOPIF target 上这条命令**会被接受**（返回 identifier），但**永远不触发**；
    同 target 的 `Page.reload` 直接报
    `Command can only be executed on top-level targets`
    —— 说明这个 iframe target 是一个受限的 CDP 视图，
    page 级「注入到新文档」的能力对它无效。
    （2026-09-17 又用 browser 端点 + Target.attachToTarget + sessionId 试了一遍，
      同样无效。所以持久化只能靠"有人在旁边看着" —— 见 install_space_selfheal。）
    """
    try:
        css = read_space_css()
    except OSError as error:
        if log:
            log("      （读不到 space-glass.css：%s）" % error)
        return 0
    expression = SPACE_INSTALL_EXPR % (
        json.dumps(SPACE_HTML_CLASS), json.dumps(SPACE_STYLE_ID),
        json.dumps(SPACE_STYLE_ID), json.dumps(css))
    installed = 0
    for target in space_iframe_targets(port, host=host):
        try:
            value = cdp_evaluate(target["webSocketDebuggerUrl"], expression, timeout=30.0)
            if value:
                installed += 1
                if log and not quiet:
                    log("      资料库 iframe 已注入：%d 字节" % len(css))
        except Exception as error:
            if log and not quiet:
                log("      （资料库 iframe 注入失败：%s）" % error)
    return installed


def install_space_selfheal(port, host="127.0.0.1", log=None, quiet=False):
    """把「资料库 iframe 自愈器」装进**主渲染进程**。

    这是让资料库页面持久透明的首选方案：主渲染进程不会 reload，
    装进去的哨兵一直活着；它监听 iframe 的 load 事件，
    一发现样式被冲掉就自己连本地 CDP 补注入 —— **不需要任何外部常驻进程**。

    前提：应用要以 --remote-allow-origins=* 启动（launch_with_cdp 已加），
    否则渲染进程的 WebSocket 会被 CDP 以 403 拒掉（Origin 头）。
    如果目标实例是旧的、没带这个参数，这里会装成功但补注入会失败；
    返回的 dict 里 `verified` 会是 False，调用方据此提示用户重启。

    返回 dict：{'installed': bool, 'healed': bool, 'port': int|None}
    """
    try:
        script = read_space_selfheal()
    except OSError as error:
        if log and not quiet:
            log("      （读不到 space-selfheal.js：%s）" % error)
        return {"installed": False, "healed": False, "port": None}
    try:
        css = read_space_css()
    except OSError as error:
        if log and not quiet:
            log("      （读不到 space-glass.css：%s）" % error)
        return {"installed": False, "healed": False, "port": None}

    # 自愈器需要知道端口；主文档是 file://…index.html，没法从 location 推出来，
    # 所以把端口直接钉进脚本里（它自己也会扫常见端口兜底）。
    script = script.replace("const PORTS = [", "const PORTS = [\n    %d," % port, 1)
    targets = [t for t in renderer_targets(port, host=host)] or \
              [t for t in renderer_targets(port, host=host, all_types=True)
               if t.get("type") == "page"]
    if not targets:
        if log and not quiet:
            log("      （主渲染进程 target 还没就绪）")
        return {"installed": False, "healed": False, "port": None}

    installed = False
    for target in targets:
        try:
            value = cdp_evaluate(target["webSocketDebuggerUrl"], script, timeout=30.0)
            if value:
                installed = True
                break
        except Exception as error:
            if log and not quiet:
                log("      （自愈器安装失败：%s）" % error)
    if not installed:
        return {"installed": False, "healed": False, "port": port}

    # 把 CSS 文本递给自愈器，并让它立刻做一次「校验 + 必要时补注入」。
    # 这一步同时充当**能力探测**：如果实例没带 --remote-allow-origins，
    # 这里的 healed 就是 False，调用方据此给出"重启一次"的提示。
    call = "window.%s.install(%s)" % (SPACE_SELFHEAL_MARK, json.dumps(css))
    healed = False
    for target in targets:
        try:
            value = cdp_evaluate(target["webSocketDebuggerUrl"],
                                 "JSON.stringify(%s)" % call, timeout=40.0)
            if not value:
                continue
            info = json.loads(value)
            healed = bool(info.get("ok"))
            break
        except Exception as error:
            if log and not quiet:
                log("      （自愈器首轮执行失败：%s）" % error)
    return {"installed": True, "healed": healed, "port": port}


def watch_space_css(port, host="127.0.0.1", interval=2.0, log=None, stop_flag=None):
    """常驻守护：一旦发现资料库 iframe 的样式被冲掉，立刻补注入。

    为什么要常驻：资料库 iframe 会自己 reload，而 OOPIF target 上
    `Page.addScriptToEvaluateOnNewDocument` 不生效（见 apply_space_css 注释），
    所以只能靠外部轮询。轮询**很便宜** —— 只是读一次 /json/list，
    再在 iframe 里跑一句查 `document.getElementById(...)` 的表达式；
    没开资料库时连 iframe target 都没有，等于空转。

    返回 (检查次数, 补注入次数)。
    """
    check_expr = """(() => document.getElementById(%s) ? 1 : 0)()""" % json.dumps(SPACE_STYLE_ID)
    probes = 0
    repairs = 0
    while not (stop_flag and stop_flag()):
        probes += 1
        for target in space_iframe_targets(port, host=host):
            ws = target["webSocketDebuggerUrl"]
            try:
                alive = cdp_evaluate(ws, check_expr, timeout=10.0)
                if not alive:
                    if apply_space_css(port, host=host, log=None):
                        repairs += 1
                        if log:
                            log("      检测到资料库样式被重置，已补注入（第 %d 次）" % repairs)
            except Exception:
                # iframe 正在导航/销毁 —— 下次循环自然重试
                continue
        time.sleep(interval)
    return probes, repairs


def _watcher_command(port, exe):
    """构造「重新调起我自己去跑守护」的命令。

    返回 (interpreter, args, workdir)，三者都是 None 表示找不到可用入口。

    未打包：`pythonw.exe <scripts/inject.py> --space-watch ...`
    已打包：没有 .py 也没有 pythonw 可调，改为重新调起**本 exe 自己**，
            由 launcher 的 main() 把 --space-watch 转发回 inject.main()。

    抽成一个函数是因为有两处要用同一套判断（spawn 和开机自启条目），
    分散写迟早会漂移。
    """
    if getattr(sys, "frozen", False):
        exe_path = os.path.abspath(sys.executable)
        return (sys.executable,
                ["--space-watch", "--port", str(port), "--exe", exe],
                os.path.dirname(exe_path))
    interpreter = sys.executable or "python"
    script = os.path.abspath(__file__)
    if not os.path.exists(script):
        return None, None, None
    # 首选 pythonw.exe：没有控制台窗口，最不容易被"窗口关闭"之类的事件波及
    pythonw = os.path.join(os.path.dirname(interpreter), "pythonw.exe")
    if os.path.exists(pythonw):
        interpreter = pythonw
    return (interpreter,
            [script, "--space-watch", "--port", str(port), "--exe", exe],
            os.path.dirname(script))


def _clean_child_env():
    """给要「脱离我们进程树」的子进程用的环境：剥掉 PyInstaller 注入的 _PYI_* 变量。

    为什么必须剥：onefile 的引导程序用 _PYI_APPLICATION_HOME_DIR 等变量告诉
    子进程「app 已经解压在哪个目录」，子进程于是**跳过自己的解压、直接复用
    父进程的临时目录**。我们 spawn 的资料库守护（重新调起 exe 自身）就会占着
    父进程的 _MEI 目录 —— 父进程退出时删不掉它，弹出
    「Failed to remove temporary directory」警告，还留下一堆残留目录。
    剥掉这些变量后，子进程会自己解压到独立目录，互不干扰。
    （实测需要剥 3 个：_PYI_APPLICATION_HOME_DIR / _PYI_ARCHIVE_FILE /
    _PYI_PARENT_PROCESS_LEVEL；为稳妥起见凡是 _PYI_ 开头的全剥。）
    """
    env = os.environ.copy()
    for key in list(env):
        if key.upper().startswith("_PYI_"):
            del env[key]
    return env


def spawn_space_watcher(exe, port, log=None):
    """把「资料库 iframe 守护」作为一个**后台游离进程**拉起来。

    为什么用游离进程而不是线程：`一键美化.bat` 注入成功后**会立刻退出**，
    线程会跟着进程一起死，守护就没了。必须 detached，且不要继承父进程的
    控制台（否则 bat 的 `exit` 会把它一起带走）。

    幂等性：用 lock 文件记录当前守护的 pid + 端口，已经有一个活着的就跳过，
    避免用户反复双击 bat 攒出一堆守护进程。
    """
    lock_path = os.path.join(_state_root(), "space-watch.lock")
    # 已有存活守护？检测它管的是不是同一个端口
    try:
        with open(lock_path, "r", encoding="utf-8") as handle:
            info = json.loads(handle.read())
        pid = int(info.get("pid") or 0)
        if pid and info.get("port") == port and _pid_alive(pid):
            return True
    except Exception:
        pass

    # 守护进程的 stdout/stderr 必须落盘 —— 否则它一崩就是「进程不见了」，
    # 完全看不到原因（第一版就是这么哑掉的）。
    watcher_log = os.path.join(_state_root(), "space-watch.log")

    # 解释器绝对路径：DETACHED_PROCESS 的子进程不走 PATH 解析，
    # 而且 bat 环境里可能根本没有 python 在 PATH 上。
    interpreter, args, workdir = _watcher_command(port, exe)
    if interpreter is None:
        if log:
            log("      （找不到可用于拉起守护的入口）")
        return False

    # 每次注入都刷新「开机自启」条目 —— 这样即使用户本次没把守护拉起来，
    # 下次开机（或重新登录）Windows 也会替我们拉一个，不再依赖 bat 的进程树。
    _install_startup_entry(interpreter, args, log)

    # Windows 上没有 Unix 的 setsid/double-fork。单靠 DETACHED_PROCESS 的
    # 子进程仍留在父进程的 Job Object 里，父进程一退就被连坐。
    # 所以这里按「成功率从高到低」依次尝试，谁先成功用谁：
    #   1) WScript 中转：WScript 由系统 COM 激活，天然在别的进程树里
    #   2) runas 无关的 schtasks：计划任务由服务创建，最彻底（沙箱里会被拦）
    #   3) 裸 DETACHED：最后兜底
    flags = 0x00000008 | 0x00000200 | 0x08000000 if os.name == "nt" else 0
    try:
        with open(watcher_log, "a", encoding="utf-8") as sink:
            sink.write("\n=== spawn %s  port=%d  ip=%s ===\n"
                       % (time.strftime("%Y-%m-%d %H:%M:%S"), port, interpreter))
            sink.flush()
    except Exception:
        pass

    proc = None
    launcher = _wscript_launcher(interpreter, args, watcher_log, workdir)
    if launcher and _try_wscript(launcher, flags):
        proc = None
    else:
        try:
            with open(watcher_log, "a", encoding="utf-8") as sink:
                proc = subprocess.Popen(
                    [interpreter] + args,
                    creationflags=flags,
                    stdin=subprocess.DEVNULL,
                    stdout=sink,
                    stderr=subprocess.STDOUT,
                    close_fds=True,
                    cwd=workdir,
                    env=_clean_child_env(),
                )
        except Exception as error:
            if log:
                log("      （守护进程启动失败：%s）" % error)
            return False

    # WScript 路径下拿不到子进程 pid，改为按「谁在写 pid 文件」认领。
    # 先清掉旧 lock，守护起来后自己回写真实 pid（见 main 的 --space-watch 分支）。
    if proc is None:
        try:
            if os.path.exists(lock_path):
                os.remove(lock_path)
        except Exception:
            pass

    def _lock_pid():
        try:
            with open(lock_path, "r", encoding="utf-8") as handle:
                data = json.loads(handle.read())
            candidate = int(data.get("pid") or 0)
            return candidate if data.get("port") == port and candidate and _pid_alive(candidate) else 0
        except Exception:
            return 0

    if proc is not None:
        with open(lock_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": proc.pid, "port": port}))

    # 关键：不能「spawn 了就报成功」。等它真的活着再下结论，
    # 否则用户看到的是「已启动后台守护」，实际几毫秒后就死了。
    for _ in range(40):  # 最多等 4 秒
        time.sleep(0.1)
        if proc is not None and proc.poll() is not None:
            # 已经退出了 —— 把日志尾部带回来
            tail = ""
            try:
                with open(watcher_log, "r", encoding="utf-8", errors="replace") as handle:
                    tail = handle.read()[-600:].strip()
            except Exception:
                pass
            if log:
                log("      （守护进程启动后立刻退出，返回码 %s）" % proc.returncode)
                if tail:
                    for line in tail.splitlines()[-8:]:
                        log("        | %s" % line)
                log("        日志：%s" % watcher_log)
            return False
        if proc is not None:
            if _pid_alive(proc.pid):
                return True
        elif _lock_pid():
            return True
    return bool(_lock_pid())


def _wscript_launcher(interpreter, args, log_path, workdir):
    """生成一个只做「起进程」的 .vbs，让 WScript 帮我们把守护拉起来。

    为什么绕这一道：Windows 上 `subprocess.Popen` 出来的子进程永远在父进程的
    Job Object 里。WorkBuddy 的注入是从 bat 里跑的，bat 一 `exit`，Job 销毁，
    子进程被连带 kill —— 实测守护进程 spawn 后 1 秒内就没了。
    WScript.exe 由 explorer/svchost 通过 COM 激活，天然在别的进程树里，
    它 `WshShell.Run(..., 0, False)` 起的 python 也就脱离了我们。

    返回 .vbs 路径；失败返回 None。
    """
    if os.name != "nt":
        return None
    try:
        vbs = os.path.join(_state_root(), "space-watch-launch.vbs")
        # VBS 字符串：双引号要写两遍
        quoted = " ".join('"%s"' % str(a).replace('"', '""') for a in args)
        # wscript 会继承我们的环境，再把 _PYI_* 原样传给它起的 exe ——
        # 那样守护又会复用父进程的 _MEI 目录（见 _clean_child_env），
        # 所以这里在 .vbs 里先把这些变量 Remove 掉。
        removals = "".join(
            'sh.Environment("PROCESS").Remove("%s")\r\n' % key
            for key in sorted(os.environ)
            if key.upper().startswith("_PYI_")
        )
        body = (
            'Set sh = CreateObject("WScript.Shell")\r\n'
            '%s'
            'sh.CurrentDirectory = "%s"\r\n'
            'sh.Run """%s"" %s", 0, False\r\n'
        ) % (removals,
             str(workdir).replace('"', '""'),
             str(interpreter).replace('"', '""'),
             quoted)
        with open(vbs, "w", encoding="mbcs" if os.name == "nt" else "utf-8") as handle:
            handle.write(body)
        return vbs
    except Exception:
        return None


def _try_wscript(launcher, flags):
    """用 wscript 起那个 .vbs。起得来就 True。

    沙箱里 wscript 属于被拦的外部程序，会直接抛异常 —— 那就返回 False，
    让调用方退回裸 DETACHED 路径。真实环境下这条才是主力。
    """
    try:
        subprocess.Popen(["wscript.exe", "//B", "//NoLogo", launcher],
                         creationflags=flags,
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         close_fds=True)
        return True
    except Exception:
        return False


def _install_startup_entry(interpreter, args, log=None):
    """在「启动」文件夹放一个 .vbs，让 Windows 开机/登录时自动拉起守护。

    为什么值得做：守护进程需要长期存活，但注入器可能是从任意父进程
    （bat / 终端 / 应用内）跑起来的，父进程一退就可能被连坐 —— 实测
    Bash 工具的沙箱会把整个进程树回收掉。开机自启绕开了这个问题：
    进程由 explorer 创建，跟我们的调用方毫无关系，且天然幂等
    （守护自己有 lock，多起一个会自动退出）。

    放 HKCU 的 Startup 目录，不需要管理员权限。
    """
    if os.name != "nt":
        return False
    try:
        appdata = os.environ.get("APPDATA")
        if not appdata:
            # 沙箱/精简环境里 APPDATA 可能没设。用 USERPROFILE 推，
            # 再退回 ~/AppData/Roaming —— 三种都不通才放弃。
            profile = (os.environ.get("USERPROFILE")
                       or (os.environ.get("HOMEDRIVE", "") + os.environ.get("HOMEPATH", ""))
                       or os.path.expanduser("~"))
            appdata = os.path.join(profile, "AppData", "Roaming")
        startup = os.path.join(appdata, "Microsoft", "Windows",
                               "Start Menu", "Programs", "Startup")
        if not os.path.isdir(startup):
            return False
        target = os.path.join(startup, "WorkBuddy资料库透明化守护.vbs")
        quoted = " ".join('"%s"' % str(a).replace('"', '""') for a in args)
        body = (
            'Set sh = CreateObject("WScript.Shell")\r\n'
            'sh.Run """%s"" %s", 0, False\r\n'
        ) % (str(interpreter).replace('"', '""'), quoted)
        # 内容没变就别重写，免得每次注入都碰一次启动目录
        try:
            with open(target, "r", encoding="mbcs") as handle:
                if handle.read() == body:
                    return True
        except Exception:
            pass
        with open(target, "w", encoding="mbcs") as handle:
            handle.write(body)
        return True
    except Exception as error:
        if log:
            log("      （写开机自启失败：%s）" % error)
        return False



def _state_root():
    try:
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        root = os.path.join(base, "WorkBuddyAmbientSkin")
        os.makedirs(root, exist_ok=True)
        return root
    except Exception:
        return os.path.expanduser("~")


def _pid_alive(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False
    try:
        k32 = _kernel32()
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        k32.CloseHandle(handle)
        return True
    except Exception:
        return False


def user_themes_root():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "WorkBuddyAmbientSkin", "themes")


def main(argv=None):
    """返回退出码：0 成功，其它为失败原因编号。

    --check  只做只读自检（找 exe / 探端口 / 读主题 / 构建表达式），
             **不重启、不注入**，用来在动手前确认环境没问题。
    --target auto|oversea|cn|<exe 路径>
             指定注入哪一个安装。同一台机器可以同时装国际版和国内版，
             默认 auto = 只跑了一个就用那个，两个都在跑时优先国际版。
    --space-watch
             只做「资料库 iframe 守护」：常驻轮询，一旦发现 iframe 重载
             把我们的样式冲掉就立刻补注入。**不碰主窗口、不重启**。
             一般由主流程自动拉起（见 spawn_space_watcher），不用手动跑。
    --port N 配合 --space-watch：指定要守护的 CDP 端口。
    --exe P  配合 --space-watch：指定目标 exe（用于日志/校验）。
    --video P
             开启**视频背景**：P 是本地视频路径（mp4 / webm）。
             ⚠️ 用 **Windows 盘符写法**，例如 `--video "D:/wallpapers/bg.mp4"`；
                写成 Git Bash 的 `/d/...` 会报 MEDIA_ELEMENT_ERROR（实测踩过）。
                反斜杠也能吃（内部会规范化成 file:///D:/...）。
    --video-off
             关闭视频背景，恢复静态壁纸。
    """
    global LOG_PATH
    argv = list(sys.argv[1:] if argv is None else argv)
    check_only = "--check" in argv
    space_watch = "--space-watch" in argv
    video_path = None
    video_off = "--video-off" in argv
    for index, item in enumerate(argv):
        if item == "--video" and index + 1 < len(argv):
            video_path = argv[index + 1]
        elif item.startswith("--video="):
            video_path = item.split("=", 1)[1]
    forced_port = None
    forced_exe = None
    for index, item in enumerate(argv):
        if item == "--port" and index + 1 < len(argv):
            try:
                forced_port = int(argv[index + 1])
            except ValueError:
                pass
        elif item == "--exe" and index + 1 < len(argv):
            forced_exe = argv[index + 1]
    target_mode = "auto"
    for index, item in enumerate(argv):
        if item == "--target" and index + 1 < len(argv):
            target_mode = argv[index + 1]
        elif item.startswith("--target="):
            target_mode = item.split("=", 1)[1]

    try:
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        state_root = os.path.join(base, "WorkBuddyAmbientSkin")
        os.makedirs(state_root, exist_ok=True)
        LOG_PATH = os.path.join(state_root, "inject-py.log")
    except Exception:
        LOG_PATH = None

    log("=" * 62)
    log(" WorkBuddy 美化注入（Python）" + ("  [只读自检]" if check_only else ""))
    log("=" * 62)

    # ---- 1) 找出所有安装，并选定目标 --------------------------------------
    installs = describe_installs()
    if not installs:
        log("[失败] 找不到 WorkBuddy 的可执行文件。")
        log("       已尝试：注册表 DisplayIcon / InstallLocation、常见安装路径、正在运行的进程。")
        log("       可以手动指定：set WORKBUDDY_EXE=<完整路径> 或 --target <exe 路径>")
        return 2
    log("[1/5] 找到的安装：")
    for line in installs:
        log(line)

    exe, why = choose_target(target_mode)
    if not exe:
        log("[失败] 选定注入目标失败：%s" % why)
        return 2
    variant, manifest = read_variant(exe)
    log("      注入目标：%s" % exe)
    log("      变体    ：%s（%s）" % (VARIANT_LABELS.get(variant, variant), why))
    if len(installs) > 1 and target_mode == "auto":
        log("      提示    ：本机装了多个版本，可用 `--target cn` / `--target oversea` 指定。")

    # ---- --space-watch：只做资料库 iframe 守护 ----------------------------
    if space_watch:
        live = forced_port if forced_port else find_live_port(exe, DEFAULT_PORT)
        if live is None:
            log("[失败] 目标没有开 CDP 端口，无法守护。")
            log("       先正常跑一次「一键美化.bat」，或直接跑 `inject.py --target cn`。")
            return 2
        log("[守护] 目标端口 %d" % live)
        log("       资料库 iframe 每次 reload 都会把我们的样式冲掉，")
        log("       这里常驻轮询、发现被冲就补注入。")
        # 自报家门：WScript 那条路径下父进程拿不到我们的 pid，靠这里回写 lock，
        # 父进程才能确认"守护真的起来了"。
        try:
            with open(os.path.join(_state_root(), "space-watch.lock"), "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"pid": os.getpid(), "port": live}))
        except Exception:
            pass
        log("")
        stop = {"flag": False}
        try:
            probes, repairs = watch_space_css(
                live,
                interval=1.5,
                log=log,
                stop_flag=lambda: stop["flag"],
            )
        except KeyboardInterrupt:
            probes = repairs = 0
        log("")
        log("守护结束：轮询 %d 次，补注入 %d 次。" % (probes, repairs))
        return 0

    # ---- 2) 探 CDP，并确认端口归属 -----------------------------------------
    # 先在 9347..9358 里找"已经属于目标"的端口 —— 两个版本共存时，
    # 目标很可能在 9348/9349 上跑着，只看 9347 会误判成需要重启。
    port = DEFAULT_PORT
    already_up = False
    live = find_live_port(exe, DEFAULT_PORT)
    if live is not None:
        port = live
        already_up = True
        log("[2/5] 目标已在端口 %d 上开着 CDP → 直接热注入（不重启）" % port)
    elif cdp_up(DEFAULT_PORT):
        port = pick_port(DEFAULT_PORT + 1)
        log("[2/5] CDP 端口 %d 被**另一个** WorkBuddy 占着（不是注入目标）" % DEFAULT_PORT)
        log("      目标将改用端口 %d 重启" % port)
    else:
        log("[2/5] CDP 端口 %d 未开启 → 需要带调试端口重启" % DEFAULT_PORT)

    # ---- 3) 准备注入素材 ---------------------------------------------------
    # 刻意放在重启**之前**：素材有问题就先失败，不要白白把用户的 WorkBuddy 关掉。
    try:
        css = read_css()
    except OSError as error:
        log("[失败] 读不到 ambient.css: %s" % error)
        return 5

    try:
        themes = list_themes([BUNDLED_THEMES_ROOT, user_themes_root()])
    except Exception as error:
        log("[失败] 读取主题失败: %s" % error)
        return 5

    try:
        expression = build_install_expression(css, themes, active_id="")
    except Exception as error:
        log("[失败] 构建注入表达式失败: %s" % error)
        return 5
    log("[3/5] 素材就绪：主题 %d 个（%s），CSS %d 字节，表达式 %d 字节"
        % (len(themes), ", ".join(t["manifest"]["id"] for t in themes), len(css), len(expression)))

    if check_only:
        log("[4/5] --check：跳过重启与注入")
        log("")
        log("自检通过 ✅ 环境没问题，直接双击「一键美化.bat」即可注入。")
        if already_up:
            log("      注意：当前已有 CDP 端口，注入时不会重启 WorkBuddy。")
        else:
            log("      注意：注入时会关闭并重启 WorkBuddy（包括当前对话窗口）。")
        return 0

    # ---- 4) 需要时重启 -----------------------------------------------------
    snapshot = None
    if already_up:
        snapshot = snapshot_local_storage(renderer_targets(port))
        if snapshot:
            try:
                log("      已快照 localStorage：%d 个键" % len(json.loads(snapshot)))
            except Exception:
                pass
        log("[4/5] 跳过重启")
    else:
        log("[4/5] 正在关闭 WorkBuddy ...")
        try:
            result = force_quit(exe)
        except CdpError as error:
            log("[失败] %s" % error)
            return 3
        log("      已关闭：%s" % (result["pids"] if result["pids"] else "（本来就没在运行）"))

        log("      正在以 --remote-debugging-port=%d 启动 ..." % port)
        try:
            launch_with_cdp(exe, port)
        except OSError as error:
            log("[失败] 启动 WorkBuddy 失败: %s" % error)
            return 4

        targets = wait_for_renderers(port, timeout=90.0)
        if not targets:
            log("[失败] 等了 90 秒也没等到渲染进程暴露 CDP。")
            log("       可能原因：WorkBuddy 启动失败，或安全软件拦截了本地调试端口。")
            log("       可以先手动打开 WorkBuddy 确认它能正常启动。")
            return 4
        log("      渲染进程已就绪（%d 个）" % len(targets))
        if snapshot:
            restore_local_storage(targets, snapshot)
            log("      已回填 localStorage 快照")

    # ---- 5) 注入 -----------------------------------------------------------
    log("[5/5] 正在注入皮肤 ...")
    targets = renderer_targets(port) or wait_for_renderers(port, timeout=30.0)
    if not targets:
        log("[失败] 拿不到渲染进程目标。")
        return 6

    installed = 0
    last_error = None
    for target in targets:
        try:
            value = cdp_evaluate(target["webSocketDebuggerUrl"], expression, timeout=120.0)
            if isinstance(value, dict) and value.get("installed"):
                installed += 1
                log("      注入成功：theme=%s mode=%s" % (value.get("themeId"), value.get("mode")))
            else:
                last_error = "渲染进程返回了非预期结果: %r" % (value,)
        except Exception as error:
            last_error = str(error)

    if installed == 0:
        log("[失败] 注入未生效。%s" % (last_error or ""))
        return 7

    # ---- 5b) 视频背景（可选）----------------------------------------------
    if video_path or video_off:
        if video_off:
            call = "window[%s].setVideo({enabled:false})" % json.dumps(STATE_KEY)
            log("[5b] 关闭视频背景 ...")
        else:
            # ⚠️ 路径规范化交给 JS 侧的 normalizeVideoSrc 做（和渲染进程同一份逻辑），
            #    这里只负责把用户输入原样递过去。
            call = "window[%s].setVideo({enabled:true, src:%s})" % (
                json.dumps(STATE_KEY), json.dumps(video_path))
            log("[5b] 开启视频背景：%s" % video_path)
        try:
            result = cdp_evaluate(targets[0]["webSocketDebuggerUrl"], call, timeout=30.0)
            if isinstance(result, dict):
                if result.get("enabled"):
                    log("      视频背景已开启 ✅ src=%s" % result.get("src"))
                    # 给视频一点时间起播，再把真实状态报出来
                    time.sleep(1.5)
                    status = cdp_evaluate(
                        targets[0]["webSocketDebuggerUrl"],
                        "JSON.stringify((function(){var v=document.getElementById('wbas-video-layer');"
                        "return v?{rs:v.readyState,ct:v.currentTime,err:v.error?v.error.code:null,"
                        "vw:v.videoWidth}:{none:true};})())", timeout=15.0)
                    log("      播放状态：%s" % status)
                else:
                    log("      视频背景已关闭。")
            else:
                log("      视频设置返回了非预期结果：%r" % (result,))
        except Exception as error:
            log("      ⚠️ 视频背景设置失败：%s" % error)
            log("         提示：路径要用 Windows 盘符写法（如 D:/videos/bg.mp4），")
            log("               不要用 Git Bash 的 /d/... 写法。")

    # ---- 校验 -------------------------------------------------------------
    try:
        verify = cdp_evaluate(
            targets[0]["webSocketDebuggerUrl"],
            "(() => { const s = window[%s]; const el = document.getElementById(%s);"
            " return JSON.stringify({ themeId: s && s.themeId, bytes: el ? el.textContent.length : 0 }); })()"
            % (json.dumps(STATE_KEY), json.dumps(STYLE_ID)),
            timeout=20.0)
        info = json.loads(verify) if isinstance(verify, str) else verify
    except Exception as error:
        info = None
        log("      （校验查询失败，但注入已成功：%s）" % error)

    if info:
        log("      校验通过：样式表 %s 字节，当前主题 %s" % (info.get("bytes"), info.get("themeId")))

    # ---- 6) 「资料库」跨域 iframe -----------------------------------------
    # 它是懒加载的：用户没点开资料库时这里拿不到 target，属正常情况，不报错。
    log("[6/6] 处理「资料库」面板（跨域 iframe）...")

    # 6a) 先装自愈器 —— 这是让资料库页面**长期**保持透明的关键。
    #     它住进主渲染进程（不会 reload），监听 iframe 的 load 事件，
    #     发现样式被冲掉就自己连本地 CDP 补注入。
    heal = install_space_selfheal(port, log=log)
    if heal["installed"] and heal["healed"]:
        log("      已启用「资料库自愈」：面板每次重载都会自动补透明 ✅")
    elif heal["installed"]:
        # 装上了但首轮没成功 —— 最可能的原因是实例没带 --remote-allow-origins。
        log("      自愈器已装入，但首轮补注入没成功。")
        log("      原因通常是当前实例没带 --remote-allow-origins 参数启动的")
        log("      （渲染进程连 CDP 会被 403 拒掉）。")
        log("      解决：手动关掉 WorkBuddy 再跑一次本脚本，让它带参数重启即可。")
    else:
        log("      自愈器没装上（拿不到主渲染进程）。")

    # 6b) 再对**当前已经开着**的资料库 iframe 立刻注入一次，让此刻就变透明。
    space_installed = apply_space_css(port, log=log)
    if space_installed:
        log("      资料库页面透明化已生效 ✅")
    else:
        log("      资料库面板当前没打开（懒加载，点开后由自愈器自动处理）。")

    # 6c) 兜底：旧版实例（没带 --remote-allow-origins）上自愈器用不了，
    #     还留一个外部轮询守护。能起来就用，起不来就如实说。
    if not heal["healed"]:
        if spawn_space_watcher(exe, port, log=log):
            log("      已启动后台守护作为兜底：资料库重载后由它补注入。")
        else:
            log("      提示：兜底守护也没起来。若资料库页面过一会儿又变白，")
            log("            可手动跑 `inject.py --space-watch --target <cn|oversea>`。")

    log("")
    log("完成 ✅  当前壁纸和 ◐ 调参都已保留。")
    log("      之后点 WorkBuddy 右上角的浮球即可随时换主题。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:  # 兜底，保证 .bat 能拿到非 0 退出码
        print("[异常] %s: %s" % (type(exc).__name__, exc))
        sys.exit(99)
