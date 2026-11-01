# -*- coding: utf-8 -*-
"""shot.py — 截屏助手（调试用）  python tools/shot.py out.png"""
import sys
from PIL import ImageGrab

path = sys.argv[1] if len(sys.argv) > 1 else "shot.png"
img = ImageGrab.grab(all_screens=True)
img.save(path)
print(f"saved {path} {img.size}")
