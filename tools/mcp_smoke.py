# -*- coding: utf-8 -*-
"""
mcp_smoke.py —— MCP 协议冒烟测试

真的把服务器当子进程拉起来，走完整 JSON-RPC 握手：
  initialize -> tools/list -> tools/call(fl_status) -> tools/call(fl_compose)

用法：python tools/mcp_smoke.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVER = os.path.join(ROOT, "mcp", "flstudio_mcp_server.py")


def rpc(proc, obj):
    proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError("服务器没有响应（进程退出了？）")
    return json.loads(line)


def main() -> int:
    ok = True

    def check(name, cond, extra=""):
        nonlocal ok
        print(("  ✔ " if cond else "  ✘ ") + name + (("  " + str(extra)) if extra else ""))
        if not cond:
            ok = False

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, SERVER], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", bufsize=1, env=env)
    try:
        print("【1】握手")
        r = rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2024-11-05",
                                  "capabilities": {},
                                  "clientInfo": {"name": "smoke", "version": "0"}}})
        check("initialize 返回 serverInfo",
              r.get("result", {}).get("serverInfo", {}).get("name") == "flstudio-arranger",
              r.get("result", {}).get("serverInfo"))
        check("声明 tools 能力", "tools" in r.get("result", {}).get("capabilities", {}))

        print("【2】工具列表")
        r = rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = [t["name"] for t in r.get("result", {}).get("tools", [])]
        check(f"暴露 {len(names)} 个工具", len(names) >= 9, names)
        for need in ("fl_setup", "fl_write_voice", "fl_read_score", "fl_push_voice"):
            check(f"包含 {need}", need in names)

        print("【3】调 fl_status")
        r = rpc(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "fl_status", "arguments": {}}})
        txt = r.get("result", {}).get("content", [{}])[0].get("text", "{}")
        st = json.loads(txt)
        check("探测到 FL 安装", st.get("fl_found") is True, st.get("fl_exe"))
        check("识别 FL 版本", isinstance(st.get("fl_version"), str), st.get("fl_version"))
        check("找到脚本目录", bool(st.get("script_dir")), st.get("script_dir"))
        check("桥接已安装", st.get("bridge_installed") is True)

        print("【4】调 fl_compose（六声部）")
        r = rpc(proc, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                       "params": {"name": "fl_compose",
                                  "arguments": {"style": "pop", "key": "C", "bars": 8,
                                                "bpm": 96, "export_midi": True}}})
        txt = r.get("result", {}).get("content", [{}])[0].get("text", "{}")
        res = json.loads(txt)
        check("compose 成功", res.get("ok") is True, res.get("error"))
        v = (res.get("detail") or {}).get("voices") or {}
        check("六个声部齐全", set(v) >= {"piano", "drums", "egtr", "agtr", "bass", "strings"},
              sorted(v))
        check("导出了 MIDI 文件", bool(res.get("midi")) and os.path.exists(res["midi"]),
              res.get("midi"))

        print("\n结论：" + ("MCP 协议层全部通过 ✔" if ok else "存在失败项 ✘"))
        return 0 if ok else 1
    finally:
        try:
            proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
