# -*- coding: utf-8 -*-
"""
fl_core.py —— FL Studio 通信核心（Windows / FL Studio 26）

三条链路，任何一条通就能干活：

  1) 探测   : 找 FL 安装目录、版本、配置目录、钢琴卷帘脚本目录
  2) 桥接   : 写 mcp_queue.json  ->  FL 里的 MCP_Bridge.pyscript 执行  ->  回写 mcp_state.json
  3) 触发   : 把 FL 窗口拉到前台，发 Ctrl+Alt+Y（官方 "Run last script again"）

不用虚拟 MIDI，不用装驱动，不用第三方库。只用 ctypes + 标准库。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import subprocess
import time
import uuid

# ---------------------------------------------------------------- 常量

QUEUE_NAME = "mcp_queue.json"
STATE_NAME = "mcp_state.json"
BRIDGE_NAME = "MCP_Bridge.pyscript"
RERUN_SCRIPT_KEYS = ("CTRL", "ALT", "Y")     # FL 官方快捷键：Run last script again

# 常见安装位置（便携版/绿色版常见 D:\fls）
_COMMON_DIRS = [
    r"C:\Program Files\Image-Line\FL Studio 2026",
    r"C:\Program Files\Image-Line\FL Studio 26",
    r"C:\Program Files\Image-Line\FL Studio 2025",
    r"C:\Program Files\Image-Line\FL Studio 21",
    r"D:\fls",
    r"E:\fls",
]

# 配置目录候选（便携版写在安装目录，标准版写在文档目录）
_CONFIG_TAILS = [
    r"System\Config\Piano roll scripts",                 # 便携版
    r"Settings\Piano roll scripts",                      # 标准版
    os.path.join(os.path.expanduser("~"), "Documents", "Image-Line",
                 "FL Studio", "Settings", "Piano roll scripts"),
]


# ---------------------------------------------------------------- 探测

def _exe_version(exe: str) -> str | None:
    """从 WhatsNew.rtf 抓版本号。注意文件头里有 Riched20 的 '10.0.26100'，
    不是 FL 版本——所以要按「主版本号 >= 12」过滤。"""
    root = os.path.dirname(exe)
    for name in ("WhatsNew.rtf", "WhatsNew.htm"):
        p = os.path.join(root, name)
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    head = f.read(4000)
                for m in re.finditer(r"(\d{1,2}\.\d{1,2}\.\d{1,3})", head):
                    major = int(m.group(1).split(".")[0])
                    if 12 <= major <= 99:
                        return m.group(1)
            except OSError:
                pass
    return None


def find_fl() -> dict:
    """找 FL 主程序。返回 {found, exe, dir, version}"""
    env = os.environ.get("FL_STUDIO_DIR")
    cands = ([env] if env else []) + _COMMON_DIRS

    # 再扫一下各盘根目录下两层（覆盖自定义安装路径）
    for drive in "CDEFG":
        root = f"{drive}:\\"
        if not os.path.isdir(root):
            continue
        try:
            for name in os.listdir(root):
                p = os.path.join(root, name)
                if os.path.isdir(p):
                    for exe in ("FL64.exe", "FL.exe"):
                        if os.path.exists(os.path.join(p, exe)):
                            cands.append(p)
        except OSError:
            continue

    seen = set()
    for d in cands:
        if not d or d in seen:
            continue
        seen.add(d)
        for exe in ("FL64.exe", "FL.exe"):
            p = os.path.join(d, exe)
            if os.path.exists(p):
                return {"found": True, "exe": p, "dir": d, "version": _exe_version(p)}
    return {"found": False, "exe": None, "dir": None, "version": None}


def find_script_dir(fl_dir: str | None = None) -> str | None:
    """定位「Piano roll scripts」目录（往里面丢 .pyscript 就能在钢琴卷帘菜单里看到）"""
    tails = []
    if fl_dir:
        tails.append(os.path.join(fl_dir, "System", "Config", "Piano roll scripts"))
    tails.append(os.path.join(documents_dir(), "Image-Line", "FL Studio",
                              "Settings", "Piano roll scripts"))
    if fl_dir:
        tails.append(os.path.join(fl_dir, "Settings", "Piano roll scripts"))
    for t in tails:
        if os.path.isdir(t):
            return t
    return None


def is_running() -> bool:
    try:
        out = subprocess.run(["tasklist"], capture_output=True,
                             timeout=10, creationflags=0x08000000).stdout
        text = out.decode("mbcs", errors="ignore")
        return bool(re.search(r"FL64\.exe|^FL\.exe", text, re.I | re.M))
    except Exception:
        return False


def status() -> dict:
    fl = find_fl()
    sd = find_script_dir(fl["dir"])
    st = {
        "fl_found": fl["found"],
        "fl_exe": fl["exe"],
        "fl_dir": fl["dir"],
        "fl_version": fl["version"],
        "fl_running": is_running(),
        "script_dir": sd,
        "bridge_installed": bool(sd and os.path.exists(os.path.join(sd, BRIDGE_NAME))),
        "queue_file": None,
        "state_file": None,
        "bridge_last_seen": None,
    }
    if sd:
        st["queue_file"] = os.path.join(sd, QUEUE_NAME)
        st["state_file"] = os.path.join(sd, STATE_NAME)
        if os.path.exists(st["state_file"]):
            st["bridge_last_seen"] = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(st["state_file"])))
    return st


def documents_dir() -> str:
    """取真正的「文档」目录。

    坑：很多机器把 Documents 重定向到别的盘（本机就是 D://Backup//Documents），
    os.path.expanduser("~/Documents") 会指到空的 C 盘目录，FL 就不认了。
    用 SHGetKnownFolderPath 拿系统认定的路径。
    """
    try:
        import ctypes
        import uuid

        class GUID(ctypes.Structure):
            _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort),
                        ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]

        g = GUID.from_buffer_copy(
            uuid.UUID("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}").bytes_le)
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(g), 0, None, ctypes.byref(out)) == 0 and out.value:
            val = out.value
            ctypes.windll.ole32.CoTaskMemFree(out)
            if os.path.isdir(val):
                return val
    except Exception:
        pass
    for cand in (os.path.join(os.path.expanduser("~"), "Documents"),
                 os.path.join(os.environ.get("USERPROFILE", ""), "Documents")):
        if os.path.isdir(cand):
            return cand
    return os.path.expanduser("~")


def fl_scores_dir(fl_dir: str | None = None) -> str | None:
    """FL 的 Scores 目录（钢琴卷帘「导入 MIDI」对话框默认就落在这里）"""
    base = os.path.join(documents_dir(), "Image-Line", "FL Studio", "Presets", "Scores")
    if os.path.isdir(base):
        return base
    if fl_dir:
        alt = os.path.join(fl_dir, "Data", "Patches", "Scores")
        if os.path.isdir(alt):
            return alt
    return None


# ---------------------------------------------------------------- 队列 / 状态

def _read_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def push(ops: list[dict], script_dir: str) -> str:
    """把一组操作写进队列，等 FL 里的脚本消费。返回 job id。"""
    job = uuid.uuid4().hex[:12]
    payload = {
        "job": job,
        "status": "pending",
        "created": time.time(),
        "ops": ops,
    }
    _write_json(os.path.join(script_dir, QUEUE_NAME), payload)
    return job


def wait_done(script_dir: str, job: str, timeout: float = 8.0) -> dict | None:
    """轮询状态文件，看这次 job 有没有被 FL 执行。"""
    deadline = time.time() + timeout
    state_path = os.path.join(script_dir, STATE_NAME)
    while time.time() < deadline:
        st = _read_json(state_path)
        if st and st.get("job") == job:
            return st
        time.sleep(0.25)
    return None


def read_state(script_dir: str) -> dict | None:
    return _read_json(os.path.join(script_dir, STATE_NAME))


# ---------------------------------------------------------------- 窗口 / 触发

_u = ctypes.windll.user32

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK = {"CTRL": 0x11, "ALT": 0x12, "SHIFT": 0x10, "Y": 0x59}


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT)]
    _anonymous_ = ("i",)
    _fields_ = [("type", wt.DWORD), ("i", _I)]


def _send_vk(vk: int, up: bool = False):
    inp = _INPUT(type=INPUT_KEYBOARD)
    inp.ki.wVk = vk
    inp.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
    _u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def find_windows() -> dict:
    """返回 {main, pianoroll, all}：FL 主窗 + 钢琴卷帘窗（分离时才是独立窗口）"""
    found = {"main": None, "pianoroll": None, "all": []}

    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        n = _u.GetWindowTextLengthW(hwnd)
        title = ""
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            _u.GetWindowTextW(hwnd, b, n + 1)
            title = b.value
        cb_buf = ctypes.create_unicode_buffer(256)
        _u.GetClassNameW(hwnd, cb_buf, 256)
        cls = cb_buf.value
        if "Fruity" in cls or title.startswith("FL Studio"):
            item = {"hwnd": int(hwnd), "title": title, "cls": cls,
                    "visible": bool(_u.IsWindowVisible(hwnd))}
            found["all"].append(item)
            if cls == "TFruityLoopsMainForm" or (title.startswith("FL Studio") and not found["main"]):
                found["main"] = item
            if "pianoroll" in cls.lower() or "Piano roll" in title:
                found["pianoroll"] = item
        return True

    _u.EnumWindows(CB(cb), 0)
    return found


def _focus(hwnd: int) -> bool:
    """把窗口拉到前台。

    Windows 有「前台锁」：非前台进程调用 SetForegroundWindow 常被静默拒绝。
    常规解法是 AttachThreadInput 挂到当前前台线程上再设，再补一次 ALT 键
    （系统认为用户有输入意图，会放开限制）。这里三招都上，最后校验。
    """
    if _u.GetForegroundWindow() == hwnd:
        return True

    if _u.IsIconic(hwnd):
        _u.ShowWindow(hwnd, 9)      # SW_RESTORE
    _u.BringWindowToTop(hwnd)

    fg = _u.GetForegroundWindow()
    tid_fg = _u.GetWindowThreadProcessId(fg, None)
    tid_tgt = _u.GetWindowThreadProcessId(hwnd, None)
    tid_cur = ctypes.windll.kernel32.GetCurrentThreadId()

    for _ in range(3):
        if tid_fg != tid_tgt:
            _u.AttachThreadInput(tid_cur, tid_fg, True)
            _u.AttachThreadInput(tid_cur, tid_tgt, True)
        # 假装按了一下 ALT，解除前台锁
        _send_vk(0x12, False)
        _send_vk(0x12, True)
        _u.SetForegroundWindow(hwnd)
        _u.SetFocus(hwnd)
        if tid_fg != tid_tgt:
            _u.AttachThreadInput(tid_cur, tid_fg, False)
            _u.AttachThreadInput(tid_cur, tid_tgt, False)
        time.sleep(0.18)
        if _u.GetForegroundWindow() == hwnd:
            return True
        fg = _u.GetForegroundWindow()
        tid_fg = _u.GetWindowThreadProcessId(fg, None)
    return _u.GetForegroundWindow() == hwnd


def trigger_rerun(prefer_pianoroll: bool = True) -> dict:
    """发 Ctrl+Alt+Y，让 FL 重跑上一个钢琴卷帘脚本（也就是我们的 MCP_Bridge）。"""
    wins = find_windows()
    target = None
    if prefer_pianoroll and wins["pianoroll"] and wins["pianoroll"]["visible"]:
        target = wins["pianoroll"]
    target = target or wins["main"]
    if not target:
        return {"ok": False, "reason": "没找到 FL Studio 窗口（FL 没启动？）"}

    focused = _focus(target["hwnd"])
    time.sleep(0.15)
    _send_vk(VK["CTRL"])
    _send_vk(VK["ALT"])
    _send_vk(VK["Y"])
    time.sleep(0.05)
    _send_vk(VK["Y"], up=True)
    _send_vk(VK["ALT"], up=True)
    _send_vk(VK["CTRL"], up=True)
    return {"ok": True, "focused": focused, "window": target["title"],
            "hwnd": hex(target["hwnd"]), "cls": target["cls"]}


def push_and_wait(ops: list[dict], script_dir: str, timeout: float = 8.0) -> dict:
    """推队列 -> 触发 -> 等回执。一条龙的完整链路。"""
    job = push(ops, script_dir)
    trig = trigger_rerun()
    if not trig.get("ok"):
        return {"ok": False, "job": job, "stage": "trigger", "detail": trig}

    st = wait_done(script_dir, job, timeout)
    if st is None:
        return {"ok": False, "job": job, "stage": "wait",
                "detail": "队列已写入但 FL 没有回执。通常是：钢琴卷帘没打开、"
                          "或本次会话还没手动跑过一次 MCP_Bridge。"}
    return {"ok": True, "job": job, "stage": "done", "state": st}
