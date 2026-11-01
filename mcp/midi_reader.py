# -*- coding: utf-8 -*-
"""
midi_reader.py —— 极简 MIDI 解析器（只用标准库）

用途：把 FL 钢琴卷帘「导出为 MIDI」出来的文件读回来，
这样 AI 能核对「写进去的和我想写的是不是一样」，不用依赖 FL 内部脚本。
"""

from __future__ import annotations

import struct


def _vlq(data: bytes, i: int):
    """读可变长度量"""
    val = 0
    while True:
        b = data[i]
        i += 1
        val = (val << 7) | (b & 0x7F)
        if not (b & 0x80):
            return val, i


def parse(path: str) -> dict:
    """返回 {ppq, tempo, tracks:[{index, name, channel, notes:[(start,dur,pitch,vel)]}]}"""
    with open(path, "rb") as f:
        data = f.read()

    if data[:4] != b"MThd":
        raise ValueError("不是标准 MIDI 文件")
    hlen = struct.unpack(">I", data[4:8])[0]
    fmt, ntrks, ppq = struct.unpack(">HHH", data[8:14])
    i = 8 + hlen

    tracks = []
    tempo = 120.0
    for ti in range(ntrks):
        if data[i:i + 4] != b"MTrk":
            break
        tlen = struct.unpack(">I", data[i + 4:i + 8])[0]
        body = data[i + 8:i + 8 + tlen]
        i += 8 + tlen

        name = f"track{ti}"
        notes = []
        active = {}
        tick = 0
        j = 0
        status = 0
        channel = 0
        while j < len(body):
            delta, j = _vlq(body, j)
            tick += delta
            if body[j] & 0x80:
                status = body[j]
                j += 1
            hi = status & 0xF0
            ch = status & 0x0F

            if hi in (0x80, 0x90):
                pitch = body[j]
                vel = body[j + 1]
                j += 2
                if hi == 0x90 and vel > 0:
                    active.setdefault((ch, pitch), []).append((tick, vel))
                    channel = ch
                else:
                    stack = active.get((ch, pitch))
                    if stack:
                        st, v = stack.pop(0)
                        notes.append((st / ppq, max(1, tick - st) / ppq, pitch, v))
            elif hi in (0xC0, 0xD0):
                j += 1
            elif hi == 0xFF:
                mtype = body[j]
                j += 1
                v, j = _vlq(body, j)
                payload = body[j:j + v]
                j += v
                if mtype == 0x03 and payload:
                    try:
                        name = payload.decode("latin-1").strip() or name
                    except Exception:
                        pass
                elif mtype == 0x51 and len(payload) == 3:
                    us = int.from_bytes(payload, "big")
                    if us:
                        tempo = 60_000_000 / us
            else:
                meta = body[j]
                j += 1
                v, j = _vlq(body, j)
                j += v

        notes.sort(key=lambda n: (n[0], n[2]))
        tracks.append({"index": ti, "name": name, "channel": channel,
                       "notes": [(round(a, 4), round(b, 4), c, d) for a, b, c, d in notes]})

    return {"format": fmt, "ppq": ppq, "tempo": tempo, "tracks": tracks}


def summarize(path: str) -> dict:
    d = parse(path)
    out = {"ppq": d["ppq"], "tempo": round(d["tempo"], 1), "tracks": []}
    for t in d["tracks"]:
        if not t["notes"]:
            continue
        ps = [n[2] for n in t["notes"]]
        out["tracks"].append({"name": t["name"], "channel": t["channel"],
                              "notes": len(t["notes"]), "range": [min(ps), max(ps)],
                              "first": t["notes"][:3]})
    out["total_notes"] = sum(t["notes"] for t in out["tracks"])
    return out


if __name__ == "__main__":
    import json
    import sys

    print(json.dumps(summarize(sys.argv[1]), ensure_ascii=False, indent=1))
