# -*- coding: utf-8 -*-
"""
fl_voices.py —— 六大声部写作器（和声学专家内核 → 可执行的编曲）

这是「专家能力真正落到 MCP 上」的地方：
  · 和声骨架由 harmony_engine 生成（斯波索宾功能体系：T → S → D → T，禁止 D→S 逆行）
  · 每个声部的音域、排列、重复音、声部进行都要过引擎/校验器
  · 不是「随机撒音符」，是「先定和声，再按乐器法分配声部」

六声部：piano / drums / egtr / agtr / bass / strings
"""

from __future__ import annotations

import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harmony_engine as H

# ============================================================
# 一、乐器法：音域 / GM 音色 / FL 通道颜色
# ============================================================

VOICE_SPECS = {
    "piano":   {"zh": "钢琴",   "range": (36, 96), "program": 0,  "color": 3},
    "drums":   {"zh": "鼓组",   "range": (35, 59), "program": None, "color": 1},
    "egtr":    {"zh": "电吉他", "range": (40, 86), "program": 30, "color": 5},
    "agtr":    {"zh": "木吉他", "range": (40, 84), "program": 24, "color": 4},
    "bass":    {"zh": "贝斯",   "range": (28, 55), "program": 33, "color": 2},
    "strings": {"zh": "弦乐",   "range": (36, 93), "program": 48, "color": 6},
}
VOICE_ORDER = ["piano", "bass", "strings", "egtr", "agtr", "drums"]

# GM 鼓位（FL 的 FPC / Sampler 默认映射与 GM 一致）
DRUM = {"kick": 36, "snare": 38, "rim": 37, "clap": 39, "hat": 42,
        "ohat": 46, "ride": 51, "crash": 49, "tom_h": 50, "tom_m": 47, "tom_l": 45}

# ============================================================
# 二、风格配方（和声走向 + 各声部律动 + 织体）
# ============================================================

STYLES = {
    "pop": {
        "zh": "流行", "prog": [1, 5, 6, 4], "prog_minor": [1, 6, 3, 7],
        "seventh": False, "bass": "root5_8th", "egtr": "offbeat",
        "agtr": "strum8", "strings": "pad", "drums": "pop_basic", "melody": True,
    },
    "rock": {
        "zh": "摇滚", "prog": [1, 4, 5, 4], "prog_minor": [1, 6, 7, 7],
        "seventh": False, "bass": "root8_8th", "egtr": "power8",
        "agtr": "strum8", "strings": "none", "drums": "rock_basic", "melody": True,
    },
    "ballad": {
        "zh": "抒情", "prog": [1, 6, 4, 5], "prog_minor": [1, 6, 3, 7],
        "seventh": True, "bass": "root_half", "egtr": "none",
        "agtr": "finger", "strings": "pad_full", "drums": "ballad_soft", "melody": True,
    },
    "edm": {
        "zh": "电子", "prog": [6, 4, 1, 5], "prog_minor": [1, 6, 3, 7],
        "seventh": False, "bass": "octave_8th", "egtr": "stab16",
        "agtr": "none", "strings": "pad", "drums": "edm_four", "melody": True,
    },
    "orchestral": {
        "zh": "管弦", "prog": [1, 4, 5, 1], "prog_minor": [1, 4, 5, 1],
        "seventh": False, "bass": "root_whole", "egtr": "none",
        "agtr": "none", "strings": "divisi", "drums": "orchestral", "melody": True,
    },
    "jazz": {
        "zh": "爵士", "prog": [2, 5, 1, 6], "prog_minor": [1, 4, 5, 1],
        "seventh": True, "bass": "walking", "egtr": "comp",
        "agtr": "finger", "strings": "none", "drums": "jazz_ride", "melody": True,
    },
    "lofi": {
        "zh": "Lo-Fi", "prog": [1, 6, 2, 5], "prog_minor": [1, 6, 3, 7],
        "seventh": True, "bass": "root_half", "egtr": "none",
        "agtr": "finger", "strings": "pad", "drums": "lofi_boom", "melody": True,
    },
    "chinese": {
        "zh": "国风", "prog": [1, 5, 6, 3], "prog_minor": [1, 3, 5, 6],
        "seventh": False, "bass": "root_half", "egtr": "none",
        "agtr": "finger", "strings": "pad_high", "drums": "chinese", "melody": True,
    },
}

# 鼓组律动：[(拍偏移, 鼓件, 力度)]
GROOVES = {
    "pop_basic": [  # 每小节 4 拍
        (0, "kick", 110), (2, "kick", 104), (4, "kick", 108),
        (1, "snare", 100), (3, "snare", 100),
    ] + [(i * 0.5, "hat", 62 + (10 if i % 2 else 0)) for i in range(8)],
    "rock_basic": [
        (0, "kick", 118), (2, "kick", 112), (2.5, "kick", 96), (4, "kick", 116),
        (1, "snare", 112), (3, "snare", 112), (3.75, "snare", 92),
    ] + [(i * 0.5, "hat", 78 if i % 2 else 88) for i in range(8)],
    "ballad_soft": [
        (0, "kick", 92), (2.5, "kick", 84),
        (2, "snare", 78),
        (0, "ride", 60), (1.5, "ride", 54), (2, "ride", 58), (3.5, "ride", 52),
    ],
    "edm_four": [
        (0, "kick", 122), (1, "kick", 118), (2, "kick", 122), (3, "kick", 118),
        (1, "clap", 96), (3, "clap", 96),
    ] + [(i * 0.5 + 0.25, "ohat", 72) for i in range(4)],
    "orchestral": [
        (0, "kick", 100), (4, "kick", 96), (0, "crash", 70),
        (2, "tom_l", 70),
    ],
    "jazz_ride": [
        (0, "ride", 72), (1.5, "ride", 56), (2, "ride", 66), (3.5, "ride", 58),
        (1, "hat", 48), (3, "hat", 48), (0, "kick", 70), (2.75, "kick", 66),
        (3, "snare", 58),
    ],
    "lofi_boom": [
        (0, "kick", 104), (2.75, "kick", 92),
        (2, "snare", 88), (1, "rim", 52),
        (0, "hat", 56), (1, "hat", 48), (2, "hat", 54), (3, "hat", 46), (3.5, "hat", 50),
    ],
    "chinese": [
        (0, "kick", 106), (2, "kick", 98), (2.5, "kick", 84),
        (1, "wood", 0), (3, "wood", 0),  # 木鱼占位，映射到 tom_h
    ] + [(i * 0.5, "hat", 52) for i in range(8)],
}


# ============================================================
# 三、和声骨架
# ============================================================

def parse_key(text: str) -> H.Key:
    """"C" / "Am" / "F#m" / "Bbm" → H.Key"""
    s = (text or "C").strip()
    m = re.match(r"^([A-Ga-g][#b]?)\s*(m|min|minor|小调)?$", s)
    if not m:
        raise ValueError(f"无法解析调性: {text!r}（写法：C / Am / F#m / Bbm）")
    tonic = m.group(1).upper()
    mode = "minor" if m.group(2) else "major"
    return H.Key(tonic, mode)


def build_progression(key: H.Key, style: str, bars: int, seed: int = 7) -> list:
    """功能序进 + 终止式收束（T→S→D→T，禁止 D→S 逆行）

    小调注意：和声小调的 3 级是增三（III+）、7 级是减三（vii°），
    这是古典写法；流行/摇滚/电子要的是自然小调的 III / VII。
    所以按风格切换——不是「随便改」，是「同主音大小调交替（借用）」。
    """
    cfg = STYLES[style]
    base = cfg["prog"] if key.mode == "major" else cfg["prog_minor"]
    degrees = [base[i % len(base)] for i in range(bars)]

    # 终止式：最后两小节做成 D→T（大调 V-I，小调 V-i）
    if bars >= 4:
        degrees[-1] = 1
        degrees[-2] = 5
        if bars >= 6:
            degrees[-3] = 4
    elif bars == 2:
        degrees = [1, 5]

    modern = style in ("pop", "rock", "edm", "ballad", "lofi", "chinese")
    chords = []
    for i, d in enumerate(degrees):
        sev = bool(cfg["seventh"]) and (i % 4 == 3)
        if modern and key.mode == "minor" and d in (3, 7):
            # 借用同主音大调的 III / VII：避免和声小调的增三 III+ 与减三 vii°
            root = key.degree_pc(3) if d == 3 else (key.tonic_pc + 10) % 12
            roman = "III" if d == 3 else "VII"
            chords.append(H.Chord(root, "M", 0, d, "D", roman, key))
            continue
        chords.append(H.diatonic_chord(key, d, sev))
    return chords


# ============================================================
# 四、排列与声部进行校验
# ============================================================

def _stacks(chord: H.Chord, lo: int, hi: int, size: int) -> list[list[int]]:
    """枚举 [lo,hi] 内所有「升序、含全部和弦音」的排列（候选织体池）。"""
    pcs = list(chord.pcs)
    size = max(size, len(pcs))
    cands = []
    for start in range(lo, hi + 1):
        if start % 12 not in pcs:
            continue
        idx = pcs.index(start % 12)
        order = pcs[idx + 1:] + pcs[: idx + 1]
        seq, cur, ok = [start], start, True
        while len(seq) < size:
            need = order[(len(seq) - 1) % len(order)]
            nxt = None
            for p in range(cur + 1, hi + 1):
                if p % 12 == need:
                    nxt = p
                    break
            if nxt is None:
                ok = False
                break
            seq.append(nxt)
            cur = nxt
        if ok:
            cands.append(seq)
    if not cands:
        root = min([p for p in range(lo, hi + 1) if p % 12 == chord.bass_pc] or [lo])
        cands.append([root + 12 * i for i in range(size)])
    return cands


def _is_perfect(iv: int) -> str | None:
    iv = abs(iv) % 12
    return {0: "八度", 7: "五度"}.get(iv)


def _parallel_cost(a: list[int], b: list[int]) -> tuple[float, list[tuple]]:
    """两个相邻织体之间的平行进行代价。
    不同和弦音同向进入纯五/纯八 = 硬伤（权重 3）；
    同一和弦音的八度重复 = 允许的加倍（权重 0.2）。"""
    cost, hits = 0.0, []
    if len(a) != len(b):
        return 0.0, []
    for x in range(len(a)):
        for y in range(x + 1, len(a)):
            p1, p2 = _is_perfect(a[y] - a[x]), _is_perfect(b[y] - b[x])
            if not (p1 and p1 == p2):
                continue
            d1, d2 = b[x] - a[x], b[y] - a[y]
            if not (d1 and d2 and (d1 > 0) == (d2 > 0)):
                continue
            doubled = (a[x] % 12) == (a[y] % 12) and (b[x] % 12) == (b[y] % 12)
            if doubled:
                cost += 0.2
            else:
                cost += 3.0
                hits.append((x, y, p1, a[x], a[y], b[x], b[y]))
    return cost, hits


def voicing(chord: H.Chord, lo: int, hi: int, size: int | None = None, prev=None) -> list[int]:
    """平滑排列：在候选织体里挑「平行进行最少、声部移动最小」的那个。
    这就是斯波索宾式的「排列法 + 声部进行」联合优选，而不是逐和弦凑。"""
    cands = _stacks(chord, lo, hi, size or len(chord.pcs))
    if not cands:
        return []
    if not prev:
        return min(cands, key=lambda c: (abs(c[0] - (lo + hi) // 2), c[0]))

    def score(c):
        cost, _ = _parallel_cost(prev, c)
        motion = sum(abs(x - y) for x, y in zip(c, prev))
        leap = sum(1 for x, y in zip(c, prev) if abs(x - y) > 12) * 2
        return cost + motion * 0.05 + leap

    return min(cands, key=score)


def check_parallels(seq: list[list[int]], label: str = "") -> list[str]:
    """相邻和弦之间扫描真正的平行五度/八度（同和弦音的八度加倍不计为硬伤）。"""
    issues = []
    for i in range(len(seq) - 1):
        _, hits = _parallel_cost(seq[i], seq[i + 1])
        for x, y, kind, a1, a2, b1, b2 in hits:
            issues.append(
                f"{label}第 {i + 1}→{i + 2} 和弦：声部 {x + 1}/{y + 1} "
                f"同向进入平行{kind}（{a1}/{a2} → {b1}/{b2}）")
    return issues


def _nearest(pc_: int, ref: int, lo: int, hi: int) -> int:
    best, bestd = None, 999
    for p in range(lo, hi + 1):
        if p % 12 == pc_ % 12:
            d = abs(p - ref)
            if d < bestd:
                best, bestd = p, d
    return best if best is not None else ref


# ============================================================
# 五、六大声部写作
# ============================================================

def part_piano(chords, key, cfg, bar_len=4.0, rng=None, melody=True):
    """钢琴：左手根音+五度/八度，右手密集和弦；副旋律走高音区。返回 (notes, voicings)"""
    notes, voicings = [], []
    prev = None
    for i, ch in enumerate(chords):
        v = voicing(ch, 55, 84, size=min(4, max(3, len(ch.pcs))), prev=prev)
        prev = v
        voicings.append(v)
        base = i * bar_len
        # 左手：根音 + 五度（低音区，和贝斯错开八度）
        root = _nearest(ch.bass_pc, 43, 36, 52)
        notes.append((base, bar_len * 0.96, root, 84))
        fifth = _nearest((ch.bass_pc + 7) % 12, root + 7, 36, 55)
        notes.append((base, bar_len * 0.96, fifth, 68))
        # 右手：整小节持续（抒情）或半拍切分（流行/摇滚）
        if cfg["zh"] in ("流行", "摇滚", "电子"):
            for hit, dur in ((0, 0.45), (0.5, 0.45), (1.5, 0.45), (2.5, 0.45), (3.5, 0.45)):
                for p in v:
                    notes.append((base + hit, dur, p, 70 + rng.randint(-6, 6)))
        else:
            for p in v:
                notes.append((base, bar_len * 0.98, p, 72))
    # 旋律：取和弦音为骨架 + 级进，走最高声部
    if melody and cfg.get("melody"):
        mel = _melody_line(chords, key, bar_len, rng, top=True)
        notes.extend(mel)
    return notes, voicings


def part_strings(chords, key, cfg, bar_len=4.0, rng=None):
    """弦乐：大提琴根音 / 中提琴内声部 / 小提琴外声部。
    先算一条完整四部（或五部）织体，再按乐器切分——这样声部进行是整体优化的，
    不是三层各自为政（那是平行八度的主要来源）。"""
    style = cfg["strings"]
    if style == "none":
        return [], []
    notes, voicings = [], []
    prev = None
    for i, ch in enumerate(chords):
        base = i * bar_len
        size = 4 if style != "pad_full" else 5
        st = voicing(ch, 36, 88, size=size, prev=prev)
        if not st:
            continue
        prev = st
        voicings.append(st)
        dur = bar_len * (0.98 if style == "divisi" else 1.02)
        notes.append((base, dur, st[0], 74))              # 大提琴
        for p in st[1:-1]:                                # 中提琴（内声部）
            notes.append((base, dur, p, 62))
        notes.append((base, dur, st[-1], 70))             # 小提琴（外声部）
        if style == "pad_high":                           # 高八度叠层加亮
            notes.append((base, dur, min(st[-1] + 12, 93), 56))
    return notes, voicings


def part_bass(chords, key, cfg, bar_len=4.0, rng=None):
    """贝斯：根音为骨，五度/经过音为肉；最后半拍向下一和弦的根音做级进连接。"""
    notes = []
    rhythm = cfg["bass"]
    for i, ch in enumerate(chords):
        base = i * bar_len
        root = _nearest(ch.bass_pc, 38, 28, 50)
        nxt = chords[i + 1] if i + 1 < len(chords) else chords[0]
        nxt_root = _nearest(nxt.bass_pc, root, 28, 50)
        fifth = _nearest((ch.bass_pc + 7) % 12, root + 7, 28, 55)
        if rhythm == "root_whole":
            notes.append((base, bar_len * 0.98, root, 96))
        elif rhythm == "root_half":
            notes.append((base, 1.9, root, 96))
            notes.append((base + 2, 1.9, root, 90))
        elif rhythm == "root5_8th":
            for k, p in enumerate([root, root, fifth, root]):
                notes.append((base + k, 0.9, p, 92))
        elif rhythm == "root8_8th":
            for k in range(8):
                notes.append((base + k * 0.5, 0.44, root, 96 if k % 2 == 0 else 84))
        elif rhythm == "octave_8th":
            for k in range(8):
                notes.append((base + k * 0.5, 0.44, root if k % 2 == 0 else root + 12, 94))
        elif rhythm == "walking":
            third = _nearest(ch.pcs[1], root + 4, 28, 55)
            approach = nxt_root - 1 if nxt_root > root else nxt_root + 1
            for k, p in enumerate([root, third, fifth, approach]):
                notes.append((base + k, 0.92, p, 94))
        else:
            notes.append((base, bar_len * 0.98, root, 94))
    return notes


def part_egtr(chords, key, cfg, bar_len=4.0, rng=None):
    """电吉他：强力和弦（根音 + 五度 + 八度），闷音八分/十六分切分。"""
    style = cfg["egtr"]
    if style == "none":
        return []
    notes = []
    for i, ch in enumerate(chords):
        base = i * bar_len
        root = _nearest(ch.bass_pc, 45, 40, 57)
        pcs = [root, root + 7, root + 12]
        if style == "power8":                       # 摇滚：八分推进 + 切分重音
            hits = [(0, .44, 108), (.5, .44, 92), (1, .44, 100), (1.5, .44, 92),
                    (2, .44, 104), (2.5, .44, 92), (3, .44, 100), (3.5, .44, 96)]
        elif style == "offbeat":                    # 流行：反拍切分
            hits = [(0, .3, 92), (.75, .3, 82), (1.5, .3, 88), (2.25, .3, 82), (3, .3, 90)]
        elif style == "stab16":                     # 电子：十六分短促
            hits = [(k * 0.25, .18, 96 if k % 4 == 0 else 74) for k in range(16)]
        else:                                       # 爵士 comp
            hits = [(0, .35, 88), (1.5, .35, 80), (2.75, .35, 84)]
        for hit, dur, vel in hits:
            for p in pcs:
                notes.append((base + hit, dur, p, vel))
    return notes


def part_agtr(chords, key, cfg, bar_len=4.0, rng=None):
    """木吉他：扫弦（strum8）或分解和弦（finger），保持低音交替（Travis 手法雏形）。"""
    style = cfg["agtr"]
    if style == "none":
        return []
    notes = []
    for i, ch in enumerate(chords):
        base = i * bar_len
        root = _nearest(ch.bass_pc, 45, 40, 57)
        voice = voicing(ch, 48, 76, size=min(4, max(3, len(ch.pcs))))
        if style == "strum8":
            for k in range(8):
                dur = .40
                vel = 84 if k % 2 == 0 else 70
                for j, p in enumerate(voice):
                    # 扫弦：每根弦错开 12ms 的听感靠力度递减模拟
                    notes.append((base + k * 0.5, dur, p, max(40, vel - j * 4)))
                notes.append((base + k * 0.5, dur, root, 88))
        else:  # finger：低音与高音交替的分解
            seq = [root, voice[1], voice[2 % len(voice)], voice[-1],
                   root, voice[2 % len(voice)], voice[1], voice[-1]]
            for k, p in enumerate(seq):
                notes.append((base + k * 0.5, .46, p, 78 if k % 2 == 0 else 66))
    return notes


def part_drums(chords, key, cfg, bars=8, bar_len=4.0, rng=None):
    """鼓组：按风格律动铺满，第 4/8 小节末加过门（fill）。"""
    groove = GROOVES.get(cfg["drums"], GROOVES["pop_basic"])
    notes = []
    for b in range(bars):
        base = b * bar_len
        for off, name, vel in groove:
            pitch = DRUM["tom_h"] if name == "wood" else DRUM[name]
            notes.append((base + off, 0.24, pitch, vel))
        # 每 4 小节末拍加 fill
        if bars >= 4 and (b + 1) % 4 == 0:
            for k, name in enumerate(["snare", "snare", "tom_h", "tom_m", "tom_l"]):
                notes.append((base + 3 + k * 0.2, 0.18, DRUM[name], 96 - k * 4))
    return notes


def _melody_line(chords, key, bar_len=4.0, rng=None, top=True):
    """用和弦音做骨架、级进做连接的旋律线（起—承—转—合四小节动机）。"""
    rng = rng or random.Random(11)
    notes = []
    prev = None
    for i, ch in enumerate(chords):
        base = i * bar_len
        tones = [p for p in range(64, 86) if p % 12 in ch.pcs]
        if not tones:
            continue
        if prev is None:
            pick = min(tones, key=lambda p: abs(p - 74))
        else:
            # 优先就近，其次避免连续同音
            cand = sorted(tones, key=lambda p: abs(p - prev))
            pick = cand[1] if len(cand) > 1 and cand[0] == prev else cand[0]
        prev = pick
        rhythm = rng.choice([(0, 1.5), (0, 1.0), (0.5, 1.0), (1, 1.5), (2, 1.5)])
        notes.append((base + rhythm[0], rhythm[1], pick, 92))
        # 后半小节补一个经过音（必须在调内）
        if rng.random() > 0.45:
            passing = pick + rng.choice([-2, -1, 1, 2])
            in_scale = {(key.tonic_pc + s) % 12 for s in key.scale}
            if (passing % 12) in in_scale:
                notes.append((base + 2.5, 0.9, passing, 80))
    return notes


# ============================================================
# 六、总装
# ============================================================

MIN_NOTE = 0.05


def fl_safe(notes: list[tuple]) -> tuple[list[tuple], int]:
    """把音符修到 FL 钢琴卷帘能原样接受：**同一音高不允许重叠**。

    为什么必须做：旋律和织体会撞车（同一音高、同一时刻各叠一个），
    FL 导入 MIDI 时对重叠的同音高音符会「切开」，导回来的音符数就比写进去的多。
    这让「写入 N 个音 → 回读 N 个音」的闭环假失败。

    规则：按音高分组、按起点排序，先到先得；后来的同音高音符
      - 起点完全相同 → 合并（留长的，力度取大）
      - 起点在前一个里面 → 把前一个截断到后一个的起点；截完短于 MIN_NOTE 就丢掉
    返回 (处理后的音符, 被合并/裁掉的个数)。
    """
    by_pitch: dict[int, list[tuple]] = {}
    for n in notes:
        by_pitch.setdefault(n[2], []).append(n)

    out, fixed = [], 0
    for _p, group in by_pitch.items():
        group.sort(key=lambda x: (x[0], -x[1]))
        kept: list[tuple] = []
        for n in group:
            st, du, pp, ve = n
            while kept:
                ps, pd, _, pv = kept[-1]
                if st >= ps + pd - 1e-6:                     # 不冲突
                    break
                if abs(st - ps) < 1e-6:                      # 同起点：合并
                    if du > pd + 1e-6 or (abs(du - pd) < 1e-6 and ve > pv):
                        kept[-1] = (st, du, pp, max(ve, pv))
                    fixed += 1
                    n = None
                    break
                room = st - ps                               # 截断前一个
                if room >= MIN_NOTE:
                    kept[-1] = (ps, round(room, 4), pp, pv)
                    fixed += 1
                    break
                kept.pop()                                   # 太短，直接删
                fixed += 1
            if n is not None:
                kept.append(n)
        out.extend(kept)
    out.sort(key=lambda x: (x[0], x[2]))
    return out, fixed


def compose(style: str = "pop", key_text: str = "C", bars: int = 8, bpm: int = 96,
            voices: list[str] | None = None, seed: int = 7) -> dict:
    """完整编曲：返回 tracks / markers / report，全是「拍」为单位，落地时再换成 tick。"""
    style = (style or "pop").lower()
    if style not in STYLES:
        raise ValueError(f"未知风格 {style}，可选：{', '.join(STYLES)}")
    cfg = STYLES[style]
    key = parse_key(key_text)
    rng = random.Random(seed)
    chords = build_progression(key, style, bars, seed)
    bar_len = 4.0
    voices = voices or VOICE_ORDER

    tracks, report = [], {"style": cfg["zh"], "key": key.name(), "bars": bars,
                          "bpm": bpm, "chords": [], "rule_checks": [], "voices": {}}

    for i, ch in enumerate(chords):
        report["chords"].append({"bar": i + 1, "beat": i * bar_len + 1,
                                 "label": ch.label, "function": getattr(ch, "function", "")})

    def add(voice, notes, extra_voicings=None, label=""):
        if not notes:
            return
        spec = VOICE_SPECS[voice]
        lo, hi = spec["range"]
        out = []
        for st, du, p, ve in notes:
            if p is None:
                continue
            while p < lo:
                p += 12
            while p > hi:
                p -= 12
            out.append((round(st, 4), round(max(0.05, du), 4), int(p), int(ve)))
        out.sort(key=lambda x: (x[0], x[2]))
        out, fixed = fl_safe(out)          # FL 不接受同音高重叠，先修干净
        if fixed:
            report.setdefault("note_fixes", {})[voice] = fixed
        tracks.append({"voice": voice, "name": f"{spec['zh']} {voice}", "channel": len(tracks),
                       "program": spec["program"], "notes": out})
        ps = [n[2] for n in out]
        report["voices"][voice] = {"zh": spec["zh"], "notes": len(out),
                                   "range": [min(ps), max(ps)],
                                   "range_name": f"{H.pitch_name(min(ps))}-{H.pitch_name(max(ps))}"}
        if extra_voicings:
            issues = check_parallels(extra_voicings, f"{spec['zh']}")
            report["rule_checks"].extend(issues)

    if "piano" in voices:
        n, v = part_piano(chords, key, cfg, bar_len, rng)
        add("piano", n, v)
    if "strings" in voices:
        n, v = part_strings(chords, key, cfg, bar_len, rng)
        add("strings", n, v)
    if "bass" in voices:
        add("bass", part_bass(chords, key, cfg, bar_len, rng))
    if "egtr" in voices:
        add("egtr", part_egtr(chords, key, cfg, bar_len, rng))
    if "agtr" in voices:
        add("agtr", part_agtr(chords, key, cfg, bar_len, rng))
    if "drums" in voices:
        add("drums", part_drums(chords, key, cfg, bars, bar_len, rng))

    markers = [(i * bar_len, ch.label) for i, ch in enumerate(chords)]
    report["total_notes"] = sum(t["notes"].__len__() for t in tracks)
    report["parallel_warnings"] = report["rule_checks"]
    return {"tracks": tracks, "markers": markers, "report": report,
            "key": key, "cfg": cfg, "chords": chords, "bars": bars, "bpm": bpm}


def voice_notes_beats(result: dict, voice: str) -> list[dict]:
    """取某声部，转成 FL 桥接需要的 {number,time,length,velocity}（单位：拍）"""
    for t in result["tracks"]:
        if t["voice"] == voice:
            return [{"number": p, "time": st, "length": max(0.06, du),
                     "velocity": ve, "color": VOICE_SPECS[voice]["color"]}
                    for st, du, p, ve in t["notes"]]
    return []


def report_text(result: dict) -> str:
    r = result["report"]
    lines = [f"【{r['style']}】{r['key']} · {r['bars']} 小节 · {r['bpm']} BPM",
             "和声："]
    row = []
    for c in r["chords"]:
        row.append(f"第{c['bar']}小节 {c['label']}")
        if len(row) == 4:
            lines.append("  " + " | ".join(row))
            row = []
    if row:
        lines.append("  " + " | ".join(row))
    lines.append("声部：")
    for v, info in r["voices"].items():
        lines.append(f"  {info['zh']:<4} {info['notes']:>4} 音  音域 {info['range_name']}")
    if r["parallel_warnings"]:
        lines.append(f"⚠ 声部进行警示 {len(r['parallel_warnings'])} 条：")
        lines.extend("  · " + x for x in r["parallel_warnings"][:8])
    else:
        lines.append("✔ 平行五/八度检查：未发现")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="六声部编曲生成器")
    ap.add_argument("--style", default="pop")
    ap.add_argument("--key", default="C")
    ap.add_argument("--bars", type=int, default=8)
    ap.add_argument("--bpm", type=int, default=96)
    ap.add_argument("--voices", default=",".join(VOICE_ORDER))
    ap.add_argument("--midi", default=None)
    a = ap.parse_args()

    vs = [v.strip() for v in a.voices.split(",") if v.strip()]
    res = compose(a.style, a.key, a.bars, a.bpm, vs)
    print(report_text(res))
    if a.midi:
        from midi_writer import write_midi
        write_midi(a.midi, res["tracks"], tempo=a.bpm, markers=res["markers"])
        print("MIDI ->", a.midi)
