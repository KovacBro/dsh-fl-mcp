# -*- coding: utf-8 -*-
"""
doctor.py —— 跨客户端健康检查

不猜配置、不拷贝命令：**直接读客户端自己的 MCP 配置文件**，按里面写的
command/args/env 把服务器拉起来，走完整的 JSON-RPC 握手，再跑真实工具。

用法
  python tools/doctor.py                                   # 用本包 .venv 直接测
  python tools/doctor.py --config "E:\\你的工作区\\.dsh\\mcp.servers.yml" --server flstudio
  python tools/doctor.py --config "%USERPROFILE%\\.workbuddy\\mcp.json" --server flstudio
  python tools/doctor.py --config <上面任一个> --live        # 额外跑一遍「真写进 FL」

--live 会：fl_setup（FL 没开就自己开）→ fl_compose → fl_write_voice(piano) → fl_read_score，
用「写进去多少个音 / 读回来多少个音」做闭环核对。需要 FL 真的能启动。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
DEFAULT_SCRIPT = os.path.join(ROOT, "mcp", "flstudio_mcp_server.py")


# ---------------------------------------------------------------- 配置读取
def _load_yaml(path):
    import yaml  # 只在读 yml 时需要
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_client_config(path: str, server: str) -> dict:
    """把不同客户端的配置归一成 {command, args, env}。

    支持两种：
      - deepseek harness 的 mcp.servers.yml：servers.<name>.{transport,command,args,env}
      - WorkBuddy / Claude 风格 mcp.json：mcpServers.<name>.{command,args,env}
    """
    low = path.lower()
    if low.endswith((".yml", ".yaml")):
        cfg = _load_yaml(path)
        table = cfg.get("servers") or {}
    else:
        cfg = _load_json(path)
        table = cfg.get("mcpServers") or cfg.get("servers") or {}
    if server not in table:
        raise SystemExit(f"配置里没有 {server}，现有：{list(table)}")
    item = table[server]
    transport = item.get("transport", "stdio")
    if transport != "stdio":
        raise SystemExit(f"{server} 是 {transport} 传输，本检查工具只支持 stdio")
    cmd = item["command"]
    args = [str(a) for a in item.get("args") or []]
    env = dict(item.get("env") or {})
    return {"command": cmd, "args": args, "env": env,
            "timeout": item.get("toolCallTimeoutMs", 60000)}


def default_command() -> dict:
    return {"command": DEFAULT_PY, "args": [DEFAULT_SCRIPT],
            "env": {"FL_STUDIO_DIR": r"D:\fls"}, "timeout": 60000}


# ---------------------------------------------------------------- MCP 客户端
class Client:
    def __init__(self, spec: dict, cwd: str | None = None):
        env = {k: v for k, v in os.environ.items() if k.upper() != "PYTHONPATH"}
        env.update({k: str(v) for k, v in (spec.get("env") or {}).items()})
        env.setdefault("PYTHONIOENCODING", "utf-8")
        self.spec = spec
        self.proc = subprocess.Popen(
            [spec["command"]] + spec["args"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", env=env, cwd=cwd or ROOT,
        )
        self.n = 0

    def _read(self):
        line = self.proc.stdout.readline()
        if not line:
            err = self.proc.stderr.read()[:800] if self.proc.stderr else ""
            raise SystemExit(f"服务器没响应，stderr：\n{err}")
        return json.loads(line)

    def rpc(self, method: str, params: dict | None = None):
        self.n += 1
        msg = {"jsonrpc": "2.0", "id": self.n, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        return self._read()

    def notify(self, method: str):
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def call(self, name: str, args: dict | None = None) -> dict:
        r = self.rpc("tools/call", {"name": name, "arguments": args or {}})
        if "error" in r:
            return {"ok": False, "error": r["error"]}
        text = r["result"]["content"][0]["text"]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"ok": True, "text": text}

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.terminate()
        except Exception:
            pass


# ---------------------------------------------------------------- 检查流程
def check(spec: dict, live: bool, cwd: str | None) -> int:
    print("=" * 64)
    print(" 启动命令：", spec["command"])
    print(" 参数    ：", spec["args"])
    print(" 环境    ：", spec["env"])
    print(" 工作目录：", cwd or ROOT)
    print("=" * 64)

    c = Client(spec, cwd=cwd)
    ok = True

    r = c.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                             "clientInfo": {"name": "doctor", "version": "1.0"}})
    info = r.get("result", {}).get("serverInfo", {})
    print(f"[1] initialize    -> {info.get('name')} v{info.get('version')}  "
          f"protocol={r.get('result', {}).get('protocolVersion')}")
    if not info:
        ok = False
    c.notify("notifications/initialized")

    r = c.rpc("tools/list", {})
    tools = [t["name"] for t in r.get("result", {}).get("tools", [])]
    print(f"[2] tools/list    -> {len(tools)} 个工具：{', '.join(tools)}")
    ok = ok and len(tools) >= 9

    st = c.call("fl_status")
    print(f"[3] fl_status     -> FL 找到={st.get('fl_found')} 版本={st.get('fl_version')} "
          f"运行中={st.get('fl_running')} 目录={st.get('fl_dir')}")
    ok = ok and bool(st.get("fl_found"))

    if live:
        print("[4] fl_setup      -> 正在唤起 FL（没开的话要等 20~30 秒）…")
        t0 = time.time()
        s = c.call("fl_setup")
        print(f"    {'OK' if s.get('ok') else 'FAIL'}  {round(time.time() - t0, 1)}s  "
              f"{(s.get('detail') or s.get('stage') or '')}"[:160])
        ok = ok and bool(s.get("ok"))

        s = c.call("fl_compose", {"style": "pop", "key": "C", "bars": 8, "bpm": 96})
        print(f"[5] fl_compose    -> {'OK' if s.get('ok') else 'FAIL'}  {s.get('midi', '')}")
        ok = ok and bool(s.get("ok"))

        w = c.call("fl_write_voice", {"voice": "piano", "clear": True})
        print(f"[6] fl_write_voice-> {'OK' if w.get('ok') else 'FAIL'}  写入 {w.get('notes')} 音  "
              f"{str(w.get('detail') or w.get('error') or '')[:120]}")
        ok = ok and bool(w.get("ok"))

        r = c.call("fl_read_score", {})
        got = r.get("noteCount")
        rng = None
        for t in r.get("tracks") or []:
            rg = t.get("range")
            if not rg or rg[0] is None:
                continue
            rng = [rg[0], rg[1]] if rng is None else [min(rng[0], rg[0]), max(rng[1], rg[1])]
        print(f"[7] fl_read_score -> {'OK' if r.get('ok') else 'FAIL'}  读回 {got} 音  "
              f"音域 {rng}")
        same = bool(w.get("notes")) and got == w.get("notes")
        print(f"    闭环核对：写 {w.get('notes')} / 读 {got} -> "
              f"{'一致 ✓' if same else '不一致 ✗'}")
        ok = ok and same

    c.close()
    print("=" * 64)
    print(" 结论：" + ("全部通过，这个配置可以交给客户端用" if ok else "有问题，见上面 FAIL"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", help="客户端 MCP 配置文件（.yml 或 .json）")
    ap.add_argument("--server", default="flstudio", help="配置里的服务器名")
    ap.add_argument("--cwd", help="模拟客户端的工作目录")
    ap.add_argument("--live", action="store_true", help="额外跑真实写入 FL 的闭环")
    a = ap.parse_args()

    spec = read_client_config(a.config, a.server) if a.config else default_command()
    sys.exit(check(spec, a.live, a.cwd))


if __name__ == "__main__":
    main()
