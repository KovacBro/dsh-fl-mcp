# -*- coding: utf-8 -*-
"""probe.py —— 交互式探针，用来一步步确认 FL 界面坐标

用法：
  python tools/probe.py esc
  python tools/probe.py click 420 14      # 相对 FL 主窗口左上角
  python tools/probe.py dclick 300 120
  python tools/probe.py shot out.png
  python tools/probe.py rect
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
import fl_gui  # noqa: E402

cmd = sys.argv[1] if len(sys.argv) > 1 else "rect"

if cmd == "rect":
    print(fl_gui.main_rect())
elif cmd == "esc":
    fl_gui.focus_fl()
    fl_gui.press("ESC")
    print("esc sent")
elif cmd == "click":
    x, y = int(sys.argv[2]), int(sys.argv[3])
    fl_gui.focus_fl()
    fl_gui.click(*fl_gui.rel(x, y))
    print("clicked", fl_gui.rel(x, y))
elif cmd == "dclick":
    x, y = int(sys.argv[2]), int(sys.argv[3])
    fl_gui.focus_fl()
    fl_gui.double_click(*fl_gui.rel(x, y))
    print("double clicked", fl_gui.rel(x, y))
elif cmd == "hover":
    x, y = int(sys.argv[2]), int(sys.argv[3])
    fl_gui.focus_fl()
    fl_gui.hover(*fl_gui.rel(x, y))
    print("hovered", fl_gui.rel(x, y))
elif cmd == "key":
    fl_gui.focus_fl()
    for k in sys.argv[2:]:
        fl_gui.press(k)
    print("keys sent")
elif cmd == "shot":
    print(fl_gui.shot(sys.argv[2] if len(sys.argv) > 2 else None))
else:
    print("unknown", cmd)
