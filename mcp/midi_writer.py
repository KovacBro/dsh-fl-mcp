# -*- coding: utf-8 -*-
"""
midi_writer.py — 极简 Standard MIDI File 写出器（纯标准库，零依赖）

为什么不用 mido/pretty_midi：
  FL Studio / Cubase / Logic / Ableton / Studio One / REAPER 全部能直接导入
  标准 .mid。零依赖意味着用户在任意机器上都能跑，不需要 pip install。

关键约定：
  MIDI 60 = 中央 C = 科学音高记法 C4 = FL Studio 里显示的 C5
  （FL Studio 的八度编号比 MIDI 标准多 1，做映射时务必注意）
"""

from __future__ import annotations

import struct


def _vlq(value: int) -> bytes:
    """可变长度数量编码（MIDI delta-time 用）"""
    if value < 0:
        value = 0
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.insert(0, (value & 0x7F) | 0x80)
        value >>= 7
    return bytes(out)


def _meta(kind: int, payload: bytes) -> bytes:
    return b"\xFF" + bytes([kind]) + _vlq(len(payload)) + payload


def _track_chunk(events: list[tuple[int, bytes]]) -> bytes:
    """events: [(absolute_tick, bytes)] → 单个 MTrk"""
    events = sorted(events, key=lambda e: (e[0], e[1][0] & 0xF0 == 0x90))
    body = bytearray()
    last = 0
    for tick, data in events:
        body += _vlq(max(0, tick - last))
        body += data
        last = tick
    body += _vlq(0) + _meta(0x2F, b"")
    return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


def write_midi(path: str, tracks, ppq: int = 480, tempo: int = 120,
               time_sig=(4, 4), markers=None) -> str:
    """
    写出多轨 MIDI 文件（format 1）。

    tracks: list of dict
        {
          "name": "Soprano",
          "channel": 0,
          "program": 0,          # GM 音色号（省略则不写 program change）
          "notes": [(start_beat, dur_beat, pitch, velocity 0-127), ...]
        }
    markers: [(beat, "I - T")] 可选，写成 marker meta 事件（便于在 DAW 里看和声标记）
    """
    # ---- 头部 ----
    ntrks = 1 + len(tracks)
    header = b"MThd" + struct.pack(">IHHH", 6, 1, ntrks, ppq)

    # ---- 轨道 0：速度/拍号/标记 ----
    tm = [(0, _meta(0x51, struct.pack(">I", int(60_000_000 / max(tempo, 1)))[1:]))]
    num, den = time_sig
    tm.append((0, _meta(0x58, bytes([num, {1: 0, 2: 1, 4: 2, 8: 3, 16: 4}.get(den, 2),
                                    24, 8]))))
    tm.append((0, _meta(0x03, "Tempo & Harmony Map".encode("utf-8"))))
    for beat, text in (markers or []):
        tm.append((int(round(beat * ppq)), _meta(0x06, str(text).encode("utf-8"))))  # marker
    chunks = [_track_chunk(tm)]

    # ---- 音符轨 ----
    for tr in tracks:
        ev: list[tuple[int, bytes]] = []
        name = tr.get("name", "Track")
        ev.append((0, _meta(0x03, name.encode("utf-8"))))
        ch = int(tr.get("channel", 0)) & 0x0F
        if tr.get("program") is not None:
            ev.append((0, bytes([0xC0 | ch, int(tr["program"]) & 0x7F])))
        for start, dur, pitch, vel in tr.get("notes", []):
            if dur <= 0:
                dur = 0.0625
            s = int(round(start * ppq))
            e = int(round((start + dur) * ppq))
            if e <= s:
                e = s + 1
            v = max(1, min(127, int(vel)))
            ev.append((s, bytes([0x90 | ch, max(0, min(127, int(pitch))), v])))
            ev.append((e, bytes([0x80 | ch, max(0, min(127, int(pitch))), 64])))
        chunks.append(_track_chunk(ev))

    data = header + b"".join(chunks)
    with open(path, "wb") as f:
        f.write(data)
    return path


def read_midi_notes(path: str):
    """
    极简 MIDI 读取器：只取 note on/off 与 ppq、tempo。
    返回 dict(ppq, tempo, notes=[(track_idx, start_beat, dur_beat, pitch, vel, channel)], markers=[])
    用于 check.py 对用户导入/导出的乐谱做和声体检。
    """
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:4] != b"MThd":
        raise ValueError("不是标准 MIDI 文件")
    hlen = struct.unpack(">I", raw[4:8])[0]
    fmt, ntrks, ppq = struct.unpack(">HHH", raw[8:14])
    pos = 8 + hlen

    notes, markers, tempo = [], [], 120
    for ti in range(ntrks):
        if raw[pos:pos + 4] != b"MTrk":
            break
        tlen = struct.unpack(">I", raw[pos + 4:pos + 8])[0]
        body = raw[pos + 8: pos + 8 + tlen]
        pos += 8 + tlen

        i, tick, status = 0, 0, 0
        active: dict[tuple[int, int], list] = {}
        while i < len(body):
            # delta
            delta = 0
            while True:
                b = body[i]; i += 1
                delta = (delta << 7) | (b & 0x7F)
                if not b & 0x80:
                    break
            tick += delta
            if i >= len(body):
                break
            b = body[i]
            if b & 0x80:
                status = b; i += 1
            if status == 0xFF:
                kind = body[i]; i += 1
                ln = 0
                while True:
                    c = body[i]; i += 1
                    ln = (ln << 7) | (c & 0x7F)
                    if not c & 0x80:
                        break
                payload = body[i:i + ln]; i += ln
                if kind == 0x51 and ln == 3:
                    tempo = int(60_000_000 / max(1, int.from_bytes(payload, "big")))
                elif kind in (0x06, 0x01):
                    markers.append((tick / ppq, payload.decode("utf-8", "replace")))
            elif status in (0xF0, 0xF7):
                ln = 0
                while True:
                    c = body[i]; i += 1
                    ln = (ln << 7) | (c & 0x7F)
                    if not c & 0x80:
                        break
                i += ln
            else:
                hi = status & 0xF0
                need = 1 if hi in (0xC0, 0xD0) else 2
                args = body[i:i + need]; i += need
                if len(args) < need:
                    break
                if hi == 0x90 and args[1] > 0:
                    active.setdefault((status & 0x0F, args[0]), []).append((tick, args[1]))
                elif hi == 0x80 or (hi == 0x90 and args[1] == 0):
                    key = (status & 0x0F, args[0])
                    if active.get(key):
                        st, vel = active[key].pop(0)
                        notes.append((ti, st / ppq, max((tick - st) / ppq, 0.03),
                                      args[0], vel, key[0]))
    notes.sort(key=lambda n: (n[1], n[3]))
    return {"ppq": ppq, "tempo": tempo, "format": fmt, "notes": notes, "markers": markers}
