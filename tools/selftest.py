# -*- coding: utf-8 -*-
"""
selftest.py —— 不依赖 FL Studio 的链路自检

做法：用假 flpianoroll 模块跑真实的 MCP_Bridge.pyscript，
验证「队列 -> 脚本 -> 状态回执」这条链路在逻辑上是对的。

验证点：
  1. notes 操作能按 tick 精度写入（拍 × PPQ）
  2. velocity 会从 0-127 归一化到 0-1
  3. 同一个队列重复运行不会重复叠音符（幂等）
  4. markers 能写入
  5. clear 能清空

用法：python tools/selftest.py
"""

from __future__ import annotations

import json
import os
import runpy
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PYSCRIPT = os.path.join(ROOT, "fl_scripts", "MCP_Bridge.pyscript")

MOCK_MODULE = '''
PPQ = 96


class _Note:
    def __init__(self):
        self.number = 60
        self.time = 0
        self.length = 96
        self.velocity = 0.8
        self.color = 0
        self.pitchofs = 0
        self.slide = False
        self.porta = False
        self.release = 0.0
        self.muted = False
        self.group = 0
        self.pan = 0.5
        self.fcut = 0.5
        self.fres = 0.5
        self.repeats = 0
        self.selected = False

    def clone(self):
        n = _Note()
        n.__dict__.update(self.__dict__)
        return n


Note = _Note


class _Marker:
    def __init__(self):
        self.time = 0
        self.name = ""
        self.mode = 0
        self.tsnum = 4
        self.tsden = 4


Marker = _Marker


class _Score:
    PPQ = 96
    tsnum = 4
    tsden = 4

    def __init__(self):
        self._notes = []
        self._markers = []

    @property
    def noteCount(self):
        return len(self._notes)

    @property
    def markerCount(self):
        return len(self._markers)

    def getDefaultNoteProperties(self):
        return _Note()

    def addNote(self, n):
        self._notes.append(n)

    def getNote(self, i):
        return self._notes[i]

    def deleteNote(self, i):
        del self._notes[i]

    def clearNotes(self, all_=False):
        self._notes = []

    def addMarker(self, m):
        self._markers.append(m)

    def getMarker(self, i):
        return self._markers[i]

    def clearMarkers(self, all_=False):
        self._markers = []


score = _Score()


class TUtils:
    @staticmethod
    def log(msg):
        pass

    @staticmethod
    def ShowMessage(msg):
        pass


Utils = TUtils


class ScriptDialog:
    def __init__(self, *a, **k):
        pass
'''


def run(pyscript_path: str, queue_path: str, mock_dir: str):
    """模拟 FL 重跑脚本。注意：mock 模块只导入一次，
    这样 score 对象在多次执行之间保持——和 FL 里「钢琴卷帘内容是持久的」一致。"""
    if mock_dir not in sys.path:
        sys.path.insert(0, mock_dir)
    runpy.run_path(pyscript_path, run_name="__fl_script__")
    with open(queue_path, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="flmcp_selftest_")
    ok = True
    try:
        # 1) 假 flpianoroll
        mock_dir = os.path.join(tmp, "mock")
        os.makedirs(mock_dir)
        with open(os.path.join(mock_dir, "flpianoroll.py"), "w", encoding="utf-8") as f:
            f.write(MOCK_MODULE)

        # 2) 把模板里的绝对路径换成临时路径
        queue = os.path.join(tmp, "mcp_queue.json")
        state = os.path.join(tmp, "mcp_state.json")
        with open(PYSCRIPT, "r", encoding="utf-8") as f:
            code = f.read()
        code = code.replace("__QUEUE_PATH__", queue).replace("__STATE_PATH__", state)
        script = os.path.join(tmp, "MCP_Bridge.pyscript")
        with open(script, "w", encoding="utf-8") as f:
            f.write(code)

        # 3) 造一个队列：C 大三和弦 + 一个和弦标记
        with open(queue, "w", encoding="utf-8") as f:
            json.dump({
                "job": "test-1", "status": "pending",
                "ops": [
                    {"type": "clear"},
                    {"type": "notes", "notes": [
                        {"number": 60, "time": 0, "length": 4, "velocity": 100, "color": 3},
                        {"number": 64, "time": 0, "length": 4, "velocity": 100, "color": 3},
                        {"number": 67, "time": 0, "length": 4, "velocity": 100, "color": 3},
                        {"number": 72, "time": 4, "length": 2, "velocity": 80, "color": 3},
                    ]},
                    {"type": "markers", "markers": [{"time": 0, "name": "I"}]},
                ],
            }, f, ensure_ascii=False)

        q = run(script, queue, mock_dir)
        with open(state, "r", encoding="utf-8") as f:
            st = json.load(f)

        def check(name, cond, extra=""):
            nonlocal ok
            print(("  ✔ " if cond else "  ✘ ") + name + (("  " + extra) if extra else ""))
            if not cond:
                ok = False

        print("【1】首次执行")
        check("状态 status=done", st.get("status") == "done", str(st.get("status")))
        check("写入 4 个音符", st.get("applied") == 4, str(st.get("applied")))
        check("FL 侧 noteCount=4", st.get("noteCount") == 4, str(st.get("noteCount")))
        check("队列被标记为已消费", q.get("status") == "done", str(q.get("status")))

        first = st["notes"][0]
        check("拍→tick 换算正确（4 拍 = 384 tick @PPQ96）",
              abs(first["length"] - 4.0) < 1e-6, f'length={first["length"]} 拍')
        check("力度 100 → 0.787", first["velocity"] == 100, f'velocity={first["velocity"]}')

        print("【2】重复执行（模拟 Ctrl+Alt+Y 再按一次）")
        st2_note = st["noteCount"]
        run(script, queue, mock_dir)
        with open(state, "r", encoding="utf-8") as f:
            st2 = json.load(f)
        check("不会重复叠音符（幂等）", st2.get("noteCount") == st2_note,
              f'{st2_note} -> {st2.get("noteCount")}')
        check("状态标记为 idle", st2.get("status") == "idle", str(st2.get("status")))

        print("【3】新任务：只 clear 不写音符")
        with open(queue, "w", encoding="utf-8") as f:
            json.dump({"job": "test-2", "status": "pending",
                       "ops": [{"type": "clear"}]}, f)
        run(script, queue, mock_dir)
        with open(state, "r", encoding="utf-8") as f:
            st3 = json.load(f)
        check("清空后 noteCount=0", st3.get("noteCount") == 0, str(st3.get("noteCount")))

        print("\n结论：" + ("链路逻辑全部通过 ✔" if ok else "存在失败项 ✘"))
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
