# -*- coding: utf-8 -*-
"""
fl_gui.py —— 让 AI 能「看见并点击」FL Studio

设计原则（这和 GitHub 上那些硬编码坐标的脚本不一样）：
  · 所有坐标都相对 FL 主窗口的客户区左上角，不写死屏幕绝对坐标
  · 每一步动作后都用「状态文件 / 窗口变化 / 截图」验证，不盲点
  · 只做 FL 没有 API 的那两件事：打开钢琴卷帘、跑一次脚本

依赖：Pillow（仅截图用），其余全是 ctypes + 标准库。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import time

import fl_core

_u = ctypes.windll.user32

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", fl_core._KEYBDINPUT)]
    _anonymous_ = ("i",)
    _fields_ = [("type", wt.DWORD), ("i", _I)]


INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

# 扳手图标相对钢琴卷帘窗口左上角的偏移（实测标定，与窗口布局无关）
WRENCH_DX, WRENCH_DY = 36, 15


def _norm(x: int, y: int) -> tuple[int, int]:
    """屏幕像素 → SendInput 归一化坐标（0..65535）"""
    vx = _u.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
    vy = _u.GetSystemMetrics(77)
    vw = _u.GetSystemMetrics(78)
    vh = _u.GetSystemMetrics(79)
    return (int((x - vx) * 65535 / (vw - 1)), int((y - vy) * 65535 / (vh - 1)))


def _mouse(flags: int, x: int | None = None, y: int | None = None, data: int = 0):
    inp = _INPUT(type=INPUT_MOUSE)
    inp.i.mi.dwFlags = flags
    inp.i.mi.mouseData = data
    if x is not None and y is not None:
        nx, ny = _norm(x, y)
        inp.i.mi.dx, inp.i.mi.dy = nx, ny
        inp.i.mi.dwFlags |= MOUSEEVENTF_ABSOLUTE
    _u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


# ---------------------------------------------------------------- 基础动作

def move(x: int, y: int):
    _mouse(MOUSEEVENTF_MOVE, x, y)
    time.sleep(0.03)


def click(x: int, y: int, button: str = "left"):
    move(x, y)
    down = MOUSEEVENTF_LEFTDOWN if button == "left" else MOUSEEVENTF_RIGHTDOWN
    up = MOUSEEVENTF_LEFTUP if button == "left" else MOUSEEVENTF_RIGHTUP
    _mouse(down)
    time.sleep(0.05)
    _mouse(up)
    time.sleep(0.12)


def double_click(x: int, y: int):
    move(x, y)
    for _ in range(2):
        _mouse(MOUSEEVENTF_LEFTDOWN)
        time.sleep(0.03)
        _mouse(MOUSEEVENTF_LEFTUP)
        time.sleep(0.06)
    time.sleep(0.25)


def hover(x: int, y: int):
    move(x, y)
    time.sleep(0.35)


def key(vk: int, times: int = 1):
    for _ in range(times):
        _send_key(vk, False)
        time.sleep(0.03)
        _send_key(vk, True)
        time.sleep(0.06)


def _send_key(vk: int, up: bool):
    inp = _INPUT(type=INPUT_KEYBOARD)
    inp.i.ki.wVk = vk
    inp.i.ki.dwFlags = fl_core.KEYEVENTF_KEYUP if up else 0
    _u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


VK = {"ESC": 0x1B, "ENTER": 0x0D, "TAB": 0x09, "SPACE": 0x20, "F5": 0x74,
      "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27, "HOME": 0x24,
      "BACKSPACE": 0x08, "DELETE": 0x2E, "A": 0x41, "CTRL": 0x11, "ALT": 0x12,
      "SHIFT": 0x10, "M": 0x4D, "Y": 0x59}


KEYEVENTF_UNICODE = 0x0004


def type_text(text: str):
    """按 Unicode 直接输入文字（不受输入法/键盘布局影响），路径里的中文也没问题。"""
    for ch in text:
        code = ord(ch)
        inp = _INPUT(type=INPUT_KEYBOARD)
        inp.i.ki.wVk = 0
        inp.i.ki.wScan = code
        inp.i.ki.dwFlags = KEYEVENTF_UNICODE
        _u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        inp2 = _INPUT(type=INPUT_KEYBOARD)
        inp2.i.ki.wVk = 0
        inp2.i.ki.wScan = code
        inp2.i.ki.dwFlags = KEYEVENTF_UNICODE | fl_core.KEYEVENTF_KEYUP
        _u.SendInput(1, ctypes.byref(inp2), ctypes.sizeof(_INPUT))
        time.sleep(0.004)
    time.sleep(0.08)


def hotkey(mods: list[str], key_name: str):
    for m in mods:
        _send_key(VK[m], False)
        time.sleep(0.03)
    _send_key(VK[key_name], False)
    time.sleep(0.03)
    _send_key(VK[key_name], True)
    for m in reversed(mods):
        _send_key(VK[m], True)
        time.sleep(0.02)


def press(name: str, times: int = 1):
    key(VK[name.upper()], times)


# ---------------------------------------------------------------- 窗口 & 坐标

def main_rect() -> tuple[int, int, int, int] | None:
    w = fl_core.find_windows()
    if not w["main"]:
        return None
    r = wt.RECT()
    _u.GetWindowRect(w["main"]["hwnd"], ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def origin() -> tuple[int, int]:
    """FL 主窗口左上角。所有相对坐标都从这里算。"""
    r = main_rect()
    if not r:
        raise RuntimeError("FL Studio 没在运行")
    return (r[0], r[1])


def rel(dx: int, dy: int) -> tuple[int, int]:
    ox, oy = origin()
    return (ox + dx, oy + dy)


# ---------------------------------------------------------------- 截图

def shot(path: str | None = None) -> str:
    from PIL import ImageGrab
    path = path or os.path.join(
        os.environ.get("TEMP", "."), f"fl_shot_{int(time.time())}.png")
    img = ImageGrab.grab(all_screens=True)
    img.save(path)
    return path


def focus_fl() -> bool:
    w = fl_core.find_windows()
    if not w["main"]:
        return False
    return fl_core._focus(w["main"]["hwnd"])


def close_welcome(timeout: float = 1.0) -> bool:
    """关掉启动欢迎页（如果弹出来了）。ESC 一次就好，没有就无副作用。"""
    focus_fl()
    time.sleep(0.3)
    press("ESC")
    time.sleep(timeout)
    return True


# ---------------------------------------------------------------- 状态校验

def wait_state_change(before_mtime: float, timeout: float = 6.0) -> bool:
    """等桥接脚本回执（FL 每跑一次脚本都会重写 state 文件）"""
    h = fl_core.find_fl()
    sd = fl_core.find_script_dir(h["dir"])
    if not sd:
        return False
    p = os.path.join(sd, fl_core.STATE_NAME)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(p) and os.path.getmtime(p) > before_mtime + 0.01:
            return True
        time.sleep(0.2)
    return False


def state_mtime() -> float:
    h = fl_core.find_fl()
    sd = fl_core.find_script_dir(h["dir"])
    if not sd:
        return 0.0
    p = os.path.join(sd, fl_core.STATE_NAME)
    return os.path.getmtime(p) if os.path.exists(p) else 0.0


# ================================================================
# 菜单识别（自适应，不写死坐标）
#
# 为什么不用硬编码坐标：GitHub 上那几个 AHK/pyautogui 脚本都写死像素，
# FL 一改主题或窗口尺寸就全废。这里改成「截图 → 找菜单面板 → 数文字行 → 点第 N 行」，
# 窗口缩放、换主题都不影响。
# ================================================================

def _grab():
    from PIL import ImageGrab
    return ImageGrab.grab(all_screens=True).convert("RGB")


def _close(a, b, tol=6) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def find_panel(img, seed: tuple[int, int], tol: int = 8):
    """从 seed 点做「亮度连通域」填充，返回整个弹出菜单的矩形。

    不用纯色匹配：FL 的菜单有渐变和分隔线，纯色扩张会立刻断掉。
    菜单是浅色的、背景是深色的，用亮度阈值做连通域最稳。
    """
    from collections import deque

    scale = 4
    sm = img.convert("L").resize((max(1, img.width // scale), max(1, img.height // scale)))
    px = sm.load()
    w, h = sm.size
    sx, sy = seed[0] // scale, seed[1] // scale
    if not (0 <= sx < w and 0 <= sy < h):
        return None
    THR = 140
    if px[sx, sy] <= THR:
        return None

    x1 = x2 = sx
    y1 = y2 = sy
    seen = {(sx, sy)}
    q = deque([(sx, sy)])
    steps = 0
    while q and steps < 120000:
        x, y = q.popleft()
        steps += 1
        if x < x1:
            x1 = x
        if x > x2:
            x2 = x
        if y < y1:
            y1 = y
        if y > y2:
            y2 = y
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen and px[nx, ny] > THR:
                seen.add((nx, ny))
                q.append((nx, ny))

    if (x2 - x1) < 10 or (y2 - y1) < 8:
        return None
    return (x1 * scale, y1 * scale, min(img.width, (x2 + 1) * scale),
            min(img.height, (y2 + 1) * scale))


def text_rows(img, rect, dark: int = 120):
    """在面板里按行统计深色像素，返回每一行文字的 (y_center, 左起 x)"""
    x1, y1, x2, y2 = rect
    px = img.load()
    counts = []
    for y in range(y1, y2):
        n = 0
        for x in range(x1 + 4, x2 - 4, 2):
            r, g, b = px[x, y]
            if (r + g + b) / 3 < dark:
                n += 1
        counts.append(n)

    rows, start = [], None
    for i, n in enumerate(counts):
        if n >= 3 and start is None:
            start = i
        elif n < 3 and start is not None:
            if i - start >= 4:
                rows.append((y1 + (start + i) // 2, y1 + start, y1 + i))
            start = None
    if start is not None and len(counts) - start >= 4:
        rows.append((y1 + (start + len(counts)) // 2, y1 + start, y1 + len(counts)))
    return rows


def ensure_pianoroll() -> bool:
    """确保钢琴卷帘可见并且在前台。

    F7 是「显示/隐藏」开关：没开就按一次；已经开着就按两次（隐藏再显示），
    这样它会被顶到最前——不然和播放列表叠在同一个停靠区时会点错窗口。
    """
    focus_fl()
    time.sleep(0.2)
    r = pianoroll_rect()
    if r:
        key(0x76)
        time.sleep(0.6)
        key(0x76)
        time.sleep(1.0)
    else:
        key(0x76)
        time.sleep(1.2)
    return pianoroll_rect() is not None


def open_pr_menu() -> bool:
    """打开钢琴卷帘的扳手菜单。

    坐标不锚在屏幕或主窗口上，而是锚在 **钢琴卷帘自己的窗口**（TEventEditForm）上：
    FL 内部怎么拖动面板都不影响。扳手是该窗口标题栏下第 2 个图标，偏移固定。
    """
    if not ensure_pianoroll():
        return False
    r = pianoroll_rect()
    click(r[0] + WRENCH_DX, r[1] + WRENCH_DY)
    time.sleep(0.9)
    return True


def _diff_bbox(a, b, tol: int = 12):
    """两张截图的差异区域（用来定位刚弹出的菜单，不用猜坐标）"""
    from PIL import ImageChops
    d = ImageChops.difference(a.convert("L"), b.convert("L"))
    d = d.point(lambda v: 255 if v > tol else 0)
    return d.getbbox()


def script_rows(img, popup):
    """弹出菜单是两栏：左栏是工具，右栏是脚本。只取右栏数行。"""
    x1, y1, x2, y2 = popup
    col = max(110, min(230, int((x2 - x1) * 0.36)))
    sub = (x2 - col, y1, x2, y2)
    return text_rows(img, sub), sub


def run_pr_script(script_index: int = 5, tries: int = 3) -> dict:
    """开菜单 -> 定位脚本栏 -> 按行号点 -> 用「状态文件是否更新」验证。

    script_index 是右栏行号：0 = 再次运行上一个脚本，1 = 打开脚本文件夹，
    2 起是脚本（按名字排序）。点错会按 ±1 重试，最终以 FL 写的回执为准。
    """
    before_state = state_mtime()
    if not ensure_pianoroll():
        return {"ok": False, "stage": "open", "detail": "FL 里没有钢琴卷帘窗口"}

    for _ in range(tries):
        if not open_pr_menu():
            return {"ok": False, "stage": "open", "detail": "没能打开钢琴卷帘菜单"}
        time.sleep(0.4)
        img = _grab()
        r = pianoroll_rect()
        wx, wy = r[0] + WRENCH_DX, r[1] + WRENCH_DY
        popup = find_panel(img, (wx + 45, wy + 50))
        if not popup:
            press("ESC")
            time.sleep(0.3)
            continue

        rows, sub = script_rows(img, popup)
        if len(rows) < 3:
            press("ESC")
            time.sleep(0.3)
            continue

        order = [script_index]
        for d in (1, -1, 2, -2):
            if 0 <= script_index + d < len(rows):
                order.append(script_index + d)
        for idx in order:
            click(sub[0] + (sub[2] - sub[0]) // 2, rows[idx][0])
            time.sleep(1.1)
            if state_mtime() > before_state + 0.01:
                return {"ok": True, "row": idx, "y": rows[idx][0],
                        "rows": len(rows), "popup": popup, "col": sub}
            close_dialogs()
            time.sleep(0.2)
            open_pr_menu()
            time.sleep(0.4)

    press("ESC")
    return {"ok": False, "stage": "click",
            "detail": "菜单点了几次都没触发脚本（菜单结构可能变了）"}


def close_dialogs() -> int:
    """关掉 FL 弹出来的模态小窗（脚本自带的参数面板之类）。

    做法：枚举 FL 进程的顶层窗口，排除主窗，点它右上角的关闭按钮。
    """
    n = 0
    main = fl_core.find_windows()["main"]
    if not main:
        return 0
    pid = wt.DWORD()
    _u.GetWindowThreadProcessId(main["hwnd"], ctypes.byref(pid))
    found = []

    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        if not _u.IsWindowVisible(hwnd):
            return True
        p = wt.DWORD()
        _u.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value != pid.value or int(hwnd) == main["hwnd"]:
            return True
        cls = ctypes.create_unicode_buffer(128)
        _u.GetClassNameW(hwnd, cls, 128)
        if cls.value.startswith("T"):
            r = wt.RECT()
            _u.GetWindowRect(hwnd, ctypes.byref(r))
            if r.right - r.left > 120 and r.bottom - r.top > 60:
                found.append((r.left, r.top, r.right, r.bottom))
        return True

    _u.EnumWindows(CB(cb), 0)
    for (l, t, r_, b) in found:
        click(r_ - 18, t + 14)
        time.sleep(0.35)
        n += 1
    return n


PR_CAPTIONS = ("钢琴卷帘", "Piano roll", "Piano Roll")


def _child_caption(hwnd) -> str:
    """取 FL 面板的标题条文字（TNewCaption 子窗口）"""
    out = []
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(h, _):
        cls = ctypes.create_unicode_buffer(128)
        _u.GetClassNameW(h, cls, 128)
        if cls.value == "TNewCaption":
            out.append(_title(h))
        return True

    _u.EnumChildWindows(hwnd, CB(cb), 0)
    return out[0] if out else ""


def pianoroll_rect():
    """钢琴卷帘窗口的屏幕矩形。

    注意：FL 的「播放列表」和「钢琴卷帘」用的是同一个窗口类 TEventEditForm，
    只按类名抓会抓到播放列表（我就踩过这个坑）。用标题条文字区分。
    """
    main = fl_core.find_windows()["main"]
    if not main:
        return None
    out = []
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        cls = ctypes.create_unicode_buffer(128)
        _u.GetClassNameW(hwnd, cls, 128)
        if cls.value == "TEventEditForm" and _u.IsWindowVisible(hwnd):
            cap = _child_caption(hwnd)
            if any(k in cap for k in PR_CAPTIONS):
                r = wt.RECT()
                _u.GetWindowRect(hwnd, ctypes.byref(r))
                if r.right - r.left > 200 and r.bottom - r.top > 150:
                    out.append((r.left, r.top, r.right, r.bottom))
        return True

    _u.EnumChildWindows(main["hwnd"], CB(cb), 0)
    return out[0] if out else None


# ================================================================
# MIDI 导入路线（不需要装脚本、不需要点菜单）
#
# FL 的钢琴卷帘菜单里有官方快捷键 Import MIDI file = Ctrl+M。
# 走这条路只要「一个文件对话框 + 一个确认框」，全程键盘可达，
# 比点菜单稳得多，而且任何版本的 FL 都通用。
# ================================================================

def focus_pianoroll() -> bool:
    if not ensure_pianoroll():
        return False
    r = pianoroll_rect()
    # 只能点左侧的钢琴键盘条：点网格会用绘制工具**画出一个音符**（力度 100），
    # 而且这音符会跟着后面的导入一起留下来，让「写入 N 音 / 回读 N+1 音」对不上。
    # 键盘条上点一下只会试听，不会改谱。
    click(r[0] + 16, r[1] + int((r[3] - r[1]) * 0.5))
    time.sleep(0.25)
    return True


def clear_pianoroll() -> bool:
    """清空当前钢琴卷帘（Ctrl+A 全选 → Delete）。

    注意：其他通道的「幽灵音符」删不掉，它们是同一个 Pattern 里别的通道，
    只在当前钢琴卷帘里显示为半透明。要彻底干净就换一个空 Pattern。
    """
    if not focus_pianoroll():
        return False
    hotkey(["CTRL"], "A")
    time.sleep(0.2)
    press("DELETE")
    time.sleep(0.25)
    press("DELETE")
    time.sleep(0.2)
    return True


def _fl_dialogs() -> list:
    """FL 进程里主窗以外、尺寸够大的可见顶层窗口（文件对话框 / 导入对话框）"""
    main = fl_core.find_windows()["main"]
    if not main:
        return []
    pid = wt.DWORD()
    _u.GetWindowThreadProcessId(main["hwnd"], ctypes.byref(pid))
    out = []
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        if not _u.IsWindowVisible(hwnd):
            return True
        p = wt.DWORD()
        _u.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value != pid.value or int(hwnd) == main["hwnd"]:
            return True
        r = wt.RECT()
        _u.GetWindowRect(hwnd, ctypes.byref(r))
        if r.right - r.left > 200 and r.bottom - r.top > 100:
            cls = ctypes.create_unicode_buffer(128)
            _u.GetClassNameW(hwnd, cls, 128)
            out.append({"hwnd": int(hwnd), "cls": cls.value,
                        "rect": (r.left, r.top, r.right, r.bottom),
                        "title": _title(hwnd)})
        return True

    _u.EnumWindows(CB(cb), 0)
    return out


def _title(hwnd) -> str:
    n = _u.GetWindowTextLengthW(hwnd)
    b = ctypes.create_unicode_buffer(n + 1)
    _u.GetWindowTextW(hwnd, b, n + 1)
    return b.value


def import_midi_to_pianoroll(file_name: str, timeout: float = 12.0,
                             clear_first: bool = True) -> dict:
    """把 Scores 目录下的一个 .mid 导入当前钢琴卷帘。

    file_name 只要文件名（FL 的对话框默认就停在 Scores 目录）。
    clear_first: 导入前先 Ctrl+A/Delete 清空。FL 的导入是**追加**，不清就会叠加。
    """
    if not focus_pianoroll():
        return {"ok": False, "stage": "focus", "detail": "没有钢琴卷帘窗口"}

    if clear_first:
        hotkey(["CTRL"], "A")
        time.sleep(0.2)
        press("DELETE")
        time.sleep(0.35)

    before = _grab()
    hotkey(["CTRL"], "M")
    time.sleep(1.4)

    dlg = _fl_dialogs()
    if not dlg:
        return {"ok": False, "stage": "dialog", "detail": "Ctrl+M 没弹出文件对话框"}

    # 文件名框默认已聚焦：全选后直接打字
    hotkey(["CTRL"], "A")
    time.sleep(0.15)
    type_text(file_name)
    time.sleep(0.35)
    press("ENTER")
    time.sleep(1.6)

    # 「导入 MIDI 数据」确认框：点右下角的「接受」
    end = time.time() + timeout
    accepted = False
    while time.time() < end:
        dlg = _fl_dialogs()
        if not dlg:
            break
        target = max(dlg, key=lambda d: (d["rect"][2] - d["rect"][0]) * (d["rect"][3] - d["rect"][1]))
        l, t, r, b = target["rect"]
        click(r - 41, b - 24)
        time.sleep(1.0)
        if not _fl_dialogs():
            accepted = True
            break
        press("ENTER")
        time.sleep(0.8)

    time.sleep(0.6)
    after = _grab()
    rr = pianoroll_rect()
    changed = 0
    if rr:
        changed = _changed_pixels(before, after, rr)
    return {"ok": True, "accepted": accepted, "changed_px": changed,
            "dialog_left": bool(_fl_dialogs())}


def _changed_pixels(a, b, rect, tol: int = 24) -> int:
    """数一下钢琴卷帘区域有多少像素变了——用来判断「音符真的进去了」"""
    l, t, r, bo = rect
    l, t = max(0, l), max(0, t)
    box = (l, t, min(a.width, r), min(a.height, bo))
    ca = a.crop(box).convert("L")
    cb2 = b.crop(box).convert("L")
    from PIL import ImageChops
    d = ImageChops.difference(ca, cb2)
    px = d.point(lambda v: 255 if v > tol else 0).load()
    w, h = d.size
    n = 0
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            if px[x, y]:
                n += 1
    return n


# ================================================================
# 回读：把钢琴卷帘导出成 MIDI 再解析
# 这样 AI 能核对「写进去的」和「想写的」是否一致，不用装任何脚本。
# ================================================================

def export_pianoroll_midi(name: str = "flmcp_readback.mid", timeout: float = 8.0) -> dict:
    """钢琴卷帘菜单：导出为 MIDI 文件（Shift+Ctrl+M）"""
    if not focus_pianoroll():
        return {"ok": False, "stage": "focus"}

    scores = fl_core.fl_scores_dir(fl_core.find_fl()["dir"])
    if not scores:
        return {"ok": False, "stage": "scores", "detail": "找不到 Scores 目录"}
    path = os.path.join(scores, name)
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass

    hotkey(["CTRL", "SHIFT"], "M")
    time.sleep(1.3)
    if not _fl_dialogs():
        return {"ok": False, "stage": "dialog", "detail": "没弹出导出对话框"}
    hotkey(["CTRL"], "A")
    time.sleep(0.15)
    type_text(name)
    time.sleep(0.3)
    press("ENTER")
    time.sleep(0.8)
    # 覆盖确认
    if _fl_dialogs():
        press("ENTER")
        time.sleep(0.6)

    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            time.sleep(0.3)
            return {"ok": True, "path": path}
        time.sleep(0.2)
    return {"ok": False, "stage": "wait", "detail": "导出文件没出现"}
