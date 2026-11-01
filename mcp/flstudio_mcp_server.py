# -*- coding: utf-8 -*-
"""
flstudio_mcp_server.py —— FL Studio 编曲 MCP 服务器（Windows / FL Studio 26）

零第三方依赖，纯标准库实现的 MCP（Model Context Protocol）stdio 服务器。
和声内核来自「编曲作曲音乐家 · 斯波索宾和声引擎」。

工具一览
  fl_status         环境自检（FL 路径/版本/进程/脚本目录/桥接状态）
  fl_install_bridge 安装钢琴卷帘桥接脚本到 FL
  fl_compose        生成六声部编曲（钢琴/鼓/电吉他/木吉他/贝斯/弦乐）
  fl_push_voice     把某个声部写进 FL 当前打开的钢琴卷帘（真·落盘）
  fl_read_score     读回 FL 当前钢琴卷帘的音符
  fl_clear_score    清空当前钢琴卷帘
  fl_export_midi    导出多轨 MIDI（拖进 FL 即得 6 条轨道）

命令行
  python flstudio_mcp_server.py --status
  python flstudio_mcp_server.py --install
  python flstudio_mcp_server.py --selftest
"""

from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fl_core
import fl_voices

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "flstudio-arranger"
SERVER_VERSION = "1.0.0"

CACHE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                         "fl-studio-arranger")
CACHE_FILE = os.path.join(CACHE_DIR, "last_compose.json")


# ============================================================
# 缓存：fl_compose 的结果，供 fl_push_voice 使用
# ============================================================

def _save_cache(result: dict, params: dict):
    os.makedirs(CACHE_DIR, exist_ok=True)
    data = {
        "params": params,
        "tracks": result["tracks"],
        "markers": result["markers"],
        "report": result["report"],
        "saved": time.time(),
    }
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return CACHE_FILE


def _load_cache() -> dict | None:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ============================================================
# 桥接脚本安装
# ============================================================

BRIDGE_TEMPLATE = os.path.join(os.path.dirname(HERE), "fl_scripts", "MCP_Bridge.pyscript")


def install_bridge(script_dir: str | None = None) -> dict:
    fl = fl_core.find_fl()
    sd = script_dir or fl_core.find_script_dir(fl["dir"])
    if not sd:
        return {"ok": False, "error": "找不到 FL 的 Piano roll scripts 目录，"
                                      "请用 FL_STUDIO_DIR 指定 FL 安装目录"}
    if not os.path.exists(BRIDGE_TEMPLATE):
        return {"ok": False, "error": f"技能包内缺少模板：{BRIDGE_TEMPLATE}"}

    queue = os.path.join(sd, fl_core.QUEUE_NAME)
    state = os.path.join(sd, fl_core.STATE_NAME)
    with open(BRIDGE_TEMPLATE, "r", encoding="utf-8") as f:
        code = f.read()
    code = code.replace("__QUEUE_PATH__", queue).replace("__STATE_PATH__", state)

    target = os.path.join(sd, fl_core.BRIDGE_NAME)
    with open(target, "w", encoding="utf-8") as f:
        f.write(code)

    # 预置一个空队列，避免脚本首次运行报 no-queue
    if not os.path.exists(queue):
        fl_core._write_json(queue, {"job": "init", "status": "done", "ops": []})
    if not os.path.exists(state):
        fl_core._write_json(state, {"ok": True, "status": "idle", "noteCount": 0, "notes": []})

    return {"ok": True, "installed": target, "script_dir": sd,
            "queue": queue, "state": state}


# ============================================================
# 工具实现
# ============================================================

def tool_status(_args: dict) -> dict:
    return fl_core.status()


def tool_install(_args: dict) -> dict:
    return install_bridge()


def tool_compose(args: dict) -> dict:
    style = args.get("style", "pop")
    key = args.get("key", "C")
    bars = int(args.get("bars", 8))
    bpm = int(args.get("bpm", 96))
    seed = int(args.get("seed", 7))
    voices = args.get("voices") or list(fl_voices.VOICE_ORDER)
    if isinstance(voices, str):
        voices = [v.strip() for v in voices.split(",") if v.strip()]
    unknown = [v for v in voices if v not in fl_voices.VOICE_SPECS]
    if unknown:
        return {"ok": False, "error": f"未知声部 {unknown}，可选：{list(fl_voices.VOICE_SPECS)}"}

    res = fl_voices.compose(style, key, bars, bpm, voices, seed)
    cache = _save_cache(res, {"style": style, "key": key, "bars": bars, "bpm": bpm,
                              "voices": voices, "seed": seed})

    out = {"ok": True, "report": fl_voices.report_text(res), "detail": res["report"],
           "cache": cache, "midi": None}
    if args.get("export_midi", True):
        out_dir = args.get("out_dir") or CACHE_DIR
        os.makedirs(out_dir, exist_ok=True)
        midi_path = os.path.join(out_dir, f"arrange_{style}_{key}_{bars}bar.mid")
        from midi_writer import write_midi
        write_midi(midi_path, res["tracks"], tempo=bpm, markers=res["markers"])
        out["midi"] = midi_path
    return out


def tool_push_voice(args: dict) -> dict:
    voice = args.get("voice")
    if voice not in fl_voices.VOICE_SPECS:
        return {"ok": False, "error": f"voice 必须是 {list(fl_voices.VOICE_SPECS)} 之一"}

    cache = _load_cache()
    notes = []
    if cache:
        for t in cache["tracks"]:
            if t["voice"] == voice:
                notes = [{"number": n[2], "time": n[0], "length": n[1],
                          "velocity": n[3],
                          "color": fl_voices.VOICE_SPECS[voice]["color"]}
                         for n in t["notes"]]
                break
    inline = args.get("notes")
    if inline:
        notes = inline
    if not notes:
        return {"ok": False, "error": f"缓存里没有 {voice} 声部，先跑 fl_compose"}

    fl = fl_core.find_fl()
    sd = fl_core.find_script_dir(fl["dir"])
    if not sd:
        return {"ok": False, "error": "找不到 FL 脚本目录"}
    if not os.path.exists(os.path.join(sd, fl_core.BRIDGE_NAME)):
        return {"ok": False, "error": "桥接脚本未安装，先跑 fl_install_bridge"}

    mode = args.get("mode", "replace")
    ops = []
    if mode == "replace":
        ops.append({"type": "clear"})
    ops.append({"type": "notes", "notes": notes})
    markers = args.get("markers")
    if markers:
        ops.append({"type": "markers", "markers": markers})
    elif cache and args.get("with_markers", True):
        ops.append({"type": "markers",
                    "markers": [{"time": t, "name": name} for t, name in cache["markers"]]})

    res = fl_core.push_and_wait(ops, sd, timeout=float(args.get("wait", 8)))
    res["voice"] = voice
    res["notes_sent"] = len(notes)
    if res.get("ok"):
        st = res.get("state", {})
        res["fl_noteCount"] = st.get("noteCount")
        res["applied"] = st.get("applied")
    return res


def tool_read_score(args: dict) -> dict:
    """读回钢琴卷帘：用 FL 自己的「导出为 MIDI」（Shift+Ctrl+M）再解析。

    不需要预装任何脚本，导出的就是 FL 里真实的音符。
    """
    import fl_gui
    import midi_reader

    res = fl_gui.export_pianoroll_midi("flmcp_readback.mid")
    if not res.get("ok"):
        return res
    data = midi_reader.parse(res["path"])
    tracks = [t for t in data["tracks"] if t["notes"]]
    out = {
        "ok": True,
        "ppq": data["ppq"],
        "tempo": round(data["tempo"], 1),
        "noteCount": sum(len(t["notes"]) for t in tracks),
        "tracks": [{"channel": t["channel"], "notes": len(t["notes"]),
                    "range": [min(n[2] for n in t["notes"]), max(n[2] for n in t["notes"])]}
                   for t in tracks],
        "file": res["path"],
    }
    if args.get("full"):
        out["notes"] = [{"track": i, "start": n[0], "length": n[1],
                         "number": n[2], "velocity": n[3]}
                        for i, t in enumerate(tracks) for n in t["notes"]]
    return out


def tool_clear_score(_args: dict) -> dict:
    import fl_gui

    ok = fl_gui.clear_pianoroll()
    return {"ok": ok, "detail": "已清空当前钢琴卷帘" if ok else "没有可用的钢琴卷帘窗口"}


def tool_export_midi(args: dict) -> dict:
    cache = _load_cache()
    if not cache:
        return {"ok": False, "error": "没有可导出的编曲，先跑 fl_compose"}
    path = args.get("path") or os.path.join(CACHE_DIR, "arrangement.mid")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    from midi_writer import write_midi
    write_midi(path, cache["tracks"], tempo=int(cache["params"].get("bpm", 96)),
               markers=[tuple(m) for m in cache["markers"]])
    return {"ok": True, "path": path, "tracks": [t["name"] for t in cache["tracks"]]}


def tool_write_voice(args: dict) -> dict:
    """自动模式：生成单声部 MIDI -> 用 FL 官方的 Ctrl+M 导入当前钢琴卷帘。

    这条路不需要预先跑脚本、不需要点菜单，全程键盘+文件对话框，最省事。
    """
    import fl_gui
    from midi_writer import write_midi

    voice = args.get("voice")
    if voice not in fl_voices.VOICE_SPECS:
        return {"ok": False, "error": f"voice 必须是 {list(fl_voices.VOICE_SPECS)} 之一"}

    cache = _load_cache()
    if not cache:
        return {"ok": False, "error": "还没有编曲，先跑 fl_compose"}

    track = None
    for t in cache["tracks"]:
        if t["voice"] == voice:
            track = t
            break
    if not track:
        return {"ok": False, "error": f"上次编曲里没有 {voice} 声部，重新 fl_compose"}

    fl = fl_core.find_fl()
    if not fl["found"]:
        return {"ok": False, "error": "没找到 FL Studio"}
    if not fl_core.is_running():
        return {"ok": False, "error": "FL 没在运行，先启动 FL Studio"}

    scores = fl_core.fl_scores_dir(fl["dir"])
    if not scores:
        return {"ok": False, "error": "找不到 FL 的 Scores 目录"}

    name = f"flmcp_{voice}.mid"
    path = os.path.join(scores, name)
    write_midi(path, [track], tempo=int(cache["params"].get("bpm", 96)))

    # 导入本身会先清空（FL 的导入是追加语义，不清就叠）
    res = fl_gui.import_midi_to_pianoroll(name, clear_first=bool(args.get("clear", True)))
    res.update({"voice": voice, "midi": path, "notes": len(track["notes"])})
    if res.get("changed_px", 0) < 50:
        res["ok"] = False
        res["detail"] = "导入对话框走完了，但钢琴卷帘几乎没变化——可能当前没有选中通道或钢琴卷帘被挡"
    return res


def tool_setup(args: dict) -> dict:
    """准备环境：必要时启动 FL、确保钢琴卷帘打开。"""
    import subprocess

    import fl_gui

    notes = []
    st = fl_core.status()
    if not st["fl_found"]:
        return {"ok": False, "error": "没找到 FL Studio（可用 FL_STUDIO_DIR 指定）"}

    if not st["fl_running"] and args.get("launch", True):
        subprocess.Popen([st["fl_exe"]])
        for _ in range(60):
            time.sleep(1.0)
            if fl_core.is_running():
                break
        notes.append("已启动 FL Studio")

    time.sleep(1.0)
    fl_gui.focus_fl()
    time.sleep(0.5)
    fl_gui.press("ESC")          # 关掉可能存在的欢迎页/弹窗
    time.sleep(0.6)

    ok = fl_gui.ensure_pianoroll()
    if not ok:
        return {"ok": False,
                "error": "钢琴卷帘没打开。如果 FL 停在欢迎页，请先关掉它"
                         "（建议勾选「不再显示此内容」，以后就不用管了）",
                "status": fl_core.status()}

    r = fl_gui.pianoroll_rect()
    return {"ok": True, "pianoroll": r, "notes": notes,
            "ready": True, "status": fl_core.status()}


TOOLS = {
    "fl_status": {
        "fn": tool_status,
        "desc": "自检 FL Studio 环境：安装路径、版本、是否运行、钢琴卷帘脚本目录、桥接是否就绪。"
                "任何一次编曲前先调它，确认链路通不通。",
        "schema": {"type": "object", "properties": {}},
    },
    "fl_install_bridge": {
        "fn": tool_install,
        "desc": "把 MCP_Bridge.pyscript 安装/更新到 FL 的 Piano roll scripts 目录（幂等，可重复调用）。",
        "schema": {"type": "object", "properties": {}},
    },
    "fl_compose": {
        "fn": tool_compose,
        "desc": "用和声学引擎生成一整套编曲（功能序进 + 终止式 + 六大声部写作），"
                "返回和声表、各声部音符数/音域、平行五八度体检结果，并导出多轨 MIDI。",
        "schema": {
            "type": "object",
            "properties": {
                "style": {"type": "string",
                          "description": "风格：pop/rock/ballad/edm/orchestral/jazz/lofi/chinese",
                          "default": "pop"},
                "key": {"type": "string", "description": "调性，如 C / Am / F#m / Bbm", "default": "C"},
                "bars": {"type": "integer", "description": "小节数", "default": 8},
                "bpm": {"type": "integer", "description": "速度", "default": 96},
                "voices": {"type": "array", "items": {"type": "string"},
                           "description": "声部子集，默认全部六声部：piano/drums/egtr/agtr/bass/strings"},
                "seed": {"type": "integer", "description": "随机种子（同种子同结果）", "default": 7},
                "export_midi": {"type": "boolean", "default": True},
                "out_dir": {"type": "string", "description": "MIDI 输出目录"},
            },
        },
    },
    "fl_setup": {
        "fn": tool_setup,
        "desc": "准备环境：必要时自动启动 FL Studio，并确保钢琴卷帘窗口打开。"
                "第一次用、或 FL 没开的时候先调这个，之后所有写入就不用人工介入。",
        "schema": {
            "type": "object",
            "properties": {
                "launch": {"type": "boolean", "default": True,
                           "description": "FL 没运行是否自动启动"},
            },
        },
    },
    "fl_write_voice": {
        "fn": tool_write_voice,
        "desc": "【推荐用法】把一个声部直接写进 FL 当前打开的钢琴卷帘。"
                "走 FL 官方的 Ctrl+M 导入 MIDI 通道，全自动，不需要预装脚本、不需要点菜单。"
                "写入前会自动清空当前钢琴卷帘（clear=false 可改为叠加）。",
        "schema": {
            "type": "object",
            "properties": {
                "voice": {"type": "string", "enum": list(fl_voices.VOICE_SPECS)},
                "clear": {"type": "boolean", "default": True,
                          "description": "写入前是否清空当前钢琴卷帘"},
            },
            "required": ["voice"],
        },
    },
    "fl_push_voice": {
        "fn": tool_push_voice,
        "desc": "把某个声部真正写进 FL Studio——写到当前打开的那个钢琴卷帘里。"
                "需要 FL 正在运行、已打开钢琴卷帘、且本次会话已手动跑过一次 MCP_Bridge。",
        "schema": {
            "type": "object",
            "properties": {
                "voice": {"type": "string", "enum": list(fl_voices.VOICE_SPECS)},
                "mode": {"type": "string", "enum": ["replace", "add"], "default": "replace",
                         "description": "replace=先清空再写，add=叠加"},
                "with_markers": {"type": "boolean", "default": True,
                                 "description": "是否同时写入和弦标记（I / V / vi ...）"},
                "wait": {"type": "number", "default": 8, "description": "等待 FL 回执的秒数"},
                "notes": {"type": "array", "description": "可选：直接给音符数组覆盖缓存",
                          "items": {"type": "object"}},
            },
            "required": ["voice"],
        },
    },
    "fl_read_score": {
        "fn": tool_read_score,
        "desc": "读回 FL 当前钢琴卷帘里的真实音符（音高/起点/长度/力度），用于核对写入结果。",
        "schema": {"type": "object", "properties": {}},
    },
    "fl_clear_score": {
        "fn": tool_clear_score,
        "desc": "清空 FL 当前打开的钢琴卷帘。",
        "schema": {"type": "object", "properties": {}},
    },
    "fl_export_midi": {
        "fn": tool_export_midi,
        "desc": "把上一次 fl_compose 的编曲导出成多轨 MIDI（FL 里拖进播放列表即得 6 条轨道）。",
        "schema": {"type": "object", "properties": {"path": {"type": "string"}}},
    },
}


# ============================================================
# MCP 协议层（stdio / JSON-RPC 2.0 / 换行分隔）
# ============================================================

def _send(obj: dict):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _ok(id_, result):
    _send({"jsonrpc": "2.0", "id": id_, "result": result})


def _err(id_, code, message):
    _send({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})


def _text_result(payload, is_error=False):
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=1)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def handle(req: dict):
    method = req.get("method")
    id_ = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        _ok(id_, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": "FL Studio 编曲 MCP：用斯波索宾和声引擎生成六声部编曲，"
                            "再通过钢琴卷帘桥接把音符直接写进 FL Studio 26。"
                            "先调 fl_status 看链路，未安装则 fl_install_bridge。",
        })
        return
    if method in ("notifications/initialized", "initialized"):
        return
    if method == "ping":
        _ok(id_, {})
        return
    if method == "tools/list":
        _ok(id_, {"tools": [
            {"name": name, "description": spec["desc"], "inputSchema": spec["schema"]}
            for name, spec in TOOLS.items()
        ]})
        return
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        spec = TOOLS.get(name)
        if not spec:
            _err(id_, -32602, f"未知工具: {name}")
            return
        try:
            result = spec["fn"](args)
            ok = not (isinstance(result, dict) and result.get("ok") is False)
            _ok(id_, _text_result(result, is_error=not ok))
        except Exception as exc:  # 工具内部异常也要回给客户端，不能吞
            _ok(id_, _text_result({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                                  is_error=True))
        return
    if method in ("resources/list", "prompts/list"):
        _ok(id_, {"resources" if method == "resources/list" else "prompts": []})
        return
    if id_ is not None:
        _err(id_, -32601, f"未实现的方法: {method}")


def serve():
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(req, list):          # 批量请求
            for r in req:
                handle(r)
        else:
            handle(req)


# ============================================================
# CLI
# ============================================================

def main():
    if "--status" in sys.argv:
        print(json.dumps(fl_core.status(), ensure_ascii=False, indent=2))
        return
    if "--install" in sys.argv:
        print(json.dumps(install_bridge(), ensure_ascii=False, indent=2))
        return
    if "--selftest" in sys.argv:
        print(json.dumps(tool_compose({"style": "pop", "key": "C", "bars": 8}),
                         ensure_ascii=False, indent=2)[:1500])
        return
    if "--tools" in sys.argv:
        for n, s in TOOLS.items():
            print(f"{n:<20} {s['desc'][:60]}")
        return
    serve()


if __name__ == "__main__":
    main()
