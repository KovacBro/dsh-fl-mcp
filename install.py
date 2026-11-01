# -*- coding: utf-8 -*-
"""
install.py —— 一键安装（dsh-fl-mcp）

做四件事：
  1. 探测 FL Studio（版本 / 安装目录 / 钢琴卷帘脚本目录）
  2. 把 MCP_Bridge.pyscript 装进 FL（可选，只有写和弦标记时才用）
  3. 注册 MCP 服务器到客户端：
       - WorkBuddy 的 ~/.workbuddy/mcp.json（合并，不动别的服务器）
       - DeepSeek Harness 的 <工作区>/.dsh/mcp.servers.yml（追加，不动注释）
  4. 跑自检

用法
  python install.py                       # 只注册 WorkBuddy + 当前目录所属的 DSH 工作区
  python install.py --dsh "E:\\我的工作区"   # 指定 DSH 工作区（可重复）
  python install.py --scan-drives          # 扫各盘找 DSH 工作区（默认不扫）
  python install.py --no-dsh               # 只注册 WorkBuddy
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "mcp"))

import fl_core  # noqa: E402
import flstudio_mcp_server as srv  # noqa: E402

MCP_JSON = os.path.join(os.path.expanduser("~"), ".workbuddy", "mcp.json")
SERVER_PATH = os.path.join(HERE, "mcp", "flstudio_mcp_server.py")
VENV_PY = os.path.join(HERE, ".venv", "Scripts", "python.exe")
DSH_REL = os.path.join(".dsh", "mcp.servers.yml")


def step(n, text):
    print(f"\n[{n}] {text}")


def python_for_clients() -> str:
    """客户端该用哪个解释器：优先本包自带的 .venv（不随任何宿主的升级变动）"""
    if os.path.exists(VENV_PY):
        return VENV_PY
    return sys.executable


def find_dsh_configs(explicit: list[str] | None = None,
                     scan_drives: bool = False) -> list[str]:
    """找出 DeepSeek Harness 工作区的 mcp.servers.yml。

    DSH 的 MCP 配置是**工作区级**的，一个工作区一份。查找顺序：
      1. --dsh 给的工作区目录（或直接给文件）
      2. 当前目录往上 4 层，看有没有 .dsh/mcp.servers.yml
      3. --scan-drives 时才扫各盘根目录（默认不扫别人的盘）
    """
    out: list[str] = []

    def add(p: str):
        if p and os.path.isfile(p) and p not in out:
            out.append(p)

    for p in explicit or []:
        p = os.path.abspath(os.path.expanduser(p))
        if p.lower().endswith((".yml", ".yaml")):
            add(p)
        else:
            add(os.path.join(p, DSH_REL))

    cur = os.path.abspath(os.getcwd())
    for _ in range(4):
        add(os.path.join(cur, DSH_REL))
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent

    if scan_drives:
        for root in ("C:/", "D:/", "E:/", "F:/"):
            if not os.path.isdir(root):
                continue
            try:
                for name in os.listdir(root)[:500]:
                    add(os.path.join(root, name, DSH_REL))
            except OSError:
                continue
    return out


def register_dsh(fl_dir: str, configs: list[str]) -> list[str]:
    """把 flstudio 追加进 DSH 工作区的 mcp.servers.yml。

    只做「追加」：那个文件里可能有手写注释和其他服务器，重写会把它们弄丢。
    """
    entry = (
        "\n  # FL Studio 编曲 MCP（六声部直接写进钢琴卷帘）\n"
        "  flstudio:\n"
        "    transport: stdio\n"
        f"    command: \"{python_for_clients().replace(os.sep, '/')}\"\n"
        "    args:\n"
        f"      - \"{SERVER_PATH.replace(os.sep, '/')}\"\n"
        "    env:\n"
        f"      FL_STUDIO_DIR: \"{fl_dir.replace(os.sep, '/')}\"\n"
        "      PYTHONIOENCODING: \"utf-8\"\n"
        "    toolCallTimeoutMs: 180000\n"
    )
    done = []
    for cfg in configs:
        try:
            with open(cfg, encoding="utf-8") as f:
                text = f.read()
            if "flstudio:" in text:
                done.append(f"{cfg}（已存在，未改动）")
                continue
            if "servers:" not in text:
                done.append(f"{cfg}（⚠ 没有 servers: 段，跳过）")
                continue
            with open(cfg, "a", encoding="utf-8") as f:
                if not text.endswith("\n"):
                    f.write("\n")
                f.write(entry)
            done.append(cfg)
        except OSError as e:
            done.append(f"{cfg}（写入失败：{e}）")
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description="注册 FL Studio MCP 到本机客户端")
    ap.add_argument("--dsh", action="append", metavar="DIR",
                    help="DeepSeek Harness 工作区目录（可重复）")
    ap.add_argument("--scan-drives", action="store_true",
                    help="扫各盘根目录找 DSH 工作区（默认不扫）")
    ap.add_argument("--no-dsh", action="store_true", help="跳过 DSH 注册")
    a = ap.parse_args()

    print("=" * 62)
    print(" dsh-fl-mcp · FL Studio 编曲 MCP · 安装")
    print("=" * 62)

    step(1, "探测 FL Studio")
    st = fl_core.status()
    if not st["fl_found"]:
        print("  ✘ 没找到 FL Studio。用环境变量指定安装目录后重试：")
        print(r"     set FL_STUDIO_DIR=D:\fls")
        return 1
    print(f"  ✔ 安装目录  {st['fl_dir']}")
    print(f"  ✔ 版本      FL Studio {st['fl_version']}")
    print(f"  ✔ 脚本目录  {st['script_dir']}")
    print(f"  · FL 是否运行：{'是' if st['fl_running'] else '否'}")

    step(2, "安装钢琴卷帘桥接脚本（可选）")
    r = srv.install_bridge()
    print(("  ✔ " + r["installed"]) if r.get("ok") else "  ✘ " + str(r.get("error")))

    step(3, "注册到 WorkBuddy")
    cfg = {}
    if os.path.exists(MCP_JSON):
        try:
            with open(MCP_JSON, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    servers = cfg.setdefault("mcpServers", {})
    others = len([k for k in servers if k != "flstudio"])
    servers["flstudio"] = {
        "command": python_for_clients(),
        "args": [SERVER_PATH],
        "env": {"PYTHONIOENCODING": "utf-8", "FL_STUDIO_DIR": st["fl_dir"]},
    }
    os.makedirs(os.path.dirname(MCP_JSON), exist_ok=True)
    with open(MCP_JSON, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print("  ✔ " + MCP_JSON)
    print(f"    （已有 {others} 个其他 MCP 服务器，未改动）")

    step(4, "注册到 DeepSeek Harness")
    if a.no_dsh:
        print("  · 已按 --no-dsh 跳过")
    else:
        configs = find_dsh_configs(a.dsh, a.scan_drives)
        if not configs:
            print("  · 没找到 .dsh/mcp.servers.yml。")
            print('    指定工作区再来一次：python install.py --dsh "E:\\你的工作区"')
        for line in register_dsh(st["fl_dir"], configs):
            print("  ✔ " + line)

    step(5, "自检")
    for script, label in (("tools/selftest.py", "桥接链路"),
                          ("tools/mcp_smoke.py", "MCP 协议")):
        p = subprocess.run([python_for_clients(), os.path.join(HERE, script)],
                           capture_output=True, encoding="utf-8", errors="ignore")
        tail = [l for l in p.stdout.splitlines() if l.startswith("结论")]
        print(f"  {'✔' if p.returncode == 0 else '✘'} {label}：{tail[0] if tail else '未通过'}")

    print("\n" + "=" * 62)
    print(" 装好了。接下来是纯自动的")
    print("=" * 62)
    print(" WorkBuddy：连接器页右上角「自定义连接器」点【信任】flstudio，再重启会话")
    print(" DSH（DeepSeek Harness）：新开一个会话就能看到 9 个 fl_* 工具")
    print("")
    print(' 然后直接说：「用 pop 风格 C 大调写 8 小节，贝斯直接写进 FL」')
    print("\n AI 会自己启动 FL、自己打开钢琴卷帘、自己导入音符、自己导回来核对。")
    print("\n 想确认某个客户端的配置真能用：")
    print('   .venv\\Scripts\\python tools\\doctor.py --config <该客户端的配置> --live')
    return 0


if __name__ == "__main__":
    sys.exit(main())
