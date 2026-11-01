# -*- coding: utf-8 -*-
"""
harmony_engine.py — 斯波索宾和声学引擎（核心库，纯标准库，零依赖）

设计依据：《和声学教程》斯波索宾等（伊·斯波索宾 / 杜波夫斯基 / 叶夫谢耶夫 / 索科洛夫）
          的功能和声体系 + 四部合唱写作（严格写作）规范。

本引擎提供四层能力：
  1. 基础设施：音高解析、音阶、和弦类型、罗马数字
  2. 织体生成：按斯波索宾排列法与重复音规则生成合法 SATB 四部织体
  3. 规则校验：平行五八度 / 反向五八度 / 隐伏五八度 / 声部超越 / 声部间距 /
               导音与七音解决 / 功能序进逆行 / 增音程 / 交叉 等
  4. 自动配和声：给定旋律 → 候选和弦 × 候选织体 → 动态规划全局最优

术语对照（本文件统一用中文注释，代码用英文标识）：
  S=soprano 高音部  A=alto 中音部  T=tenor 次中音部  B=bass 低音部
  T 功能=主功能  S 功能=下属功能  D 功能=属功能   （注意：S 既指声部也指下属功能，靠上下文区分）
"""

from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass, field

# ============================================================
# 一、音高基础
# ============================================================

PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
_LETTER_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def parse_pitch(text: str) -> int:
    """
    解析音名 → MIDI 号。支持 C4 / C#4 / Db4 / c4 / Bb-1。
    约定：MIDI 60 = C4（科学音高记法）= FL Studio 显示的 C5
    """
    s = str(text).strip()
    if not s:
        raise ValueError("空音名")
    letter = s[0].upper()
    if letter not in _LETTER_PC:
        raise ValueError(f"非法音名: {text}")
    i, acc = 1, 0
    while i < len(s) and s[i] in "#b♯♭":
        acc += 1 if s[i] in "#♯" else -1
        i += 1
    octave = int(s[i:]) if i < len(s) else 4
    return (_LETTER_PC[letter] + acc) + (octave + 1) * 12


def pitch_name(midi: int, flats: bool = False) -> str:
    midi = int(midi)
    tbl = FLAT_NAMES if flats else PITCH_NAMES
    return f"{tbl[midi % 12]}{midi // 12 - 1}"


def pc(midi: int) -> int:
    return int(midi) % 12


# 声部音域（严格写作，斯波索宾规定的人声常用音域）
VOICE_RANGES = {
    "S": (60, 81),   # 女高音 C4–A5
    "A": (55, 74),   # 女低音 G3–D5
    "T": (48, 67),   # 男高音 C3–G4
    "B": (40, 60),   # 男低音 E2–C4
}
VOICE_ORDER = ["S", "A", "T", "B"]

# 声部间距上限（半音）：上三声部之间不超过八度；次中音与低音之间不超过十二度
SPACING_MAX = {"SA": 12, "AT": 12, "TB": 19}

# ============================================================
# 二、音阶与调
# ============================================================

SCALES = {
    "major":        [0, 2, 4, 5, 7, 9, 11],
    "minor":        [0, 2, 3, 5, 7, 8, 11],   # 和声小调（斯波索宾的基础小调形态）
    "minor_nat":    [0, 2, 3, 5, 7, 8, 10],   # 自然小调
    "minor_mel":    [0, 2, 3, 5, 7, 9, 11],   # 旋律小调（上行）
    "dorian":       [0, 2, 3, 5, 7, 9, 10],
    "phrygian":     [0, 1, 3, 5, 7, 8, 10],
    "lydian":       [0, 2, 4, 6, 7, 9, 11],
    "mixolydian":   [0, 2, 4, 5, 7, 9, 10],
}

# 各调式下的三和弦性质（第 1–7 级）
TRIAD_QUALITY = {
    "major":     ["M", "m", "m", "M", "M", "m", "d"],
    "minor":     ["m", "d", "a", "m", "M", "M", "d"],
    "minor_nat": ["m", "d", "M", "m", "m", "M", "M"],
    "minor_mel": ["m", "m", "a", "M", "M", "d", "d"],
    "dorian":    ["m", "m", "M", "M", "m", "d", "M"],
    "phrygian":  ["m", "M", "M", "m", "d", "M", "m"],
    "lydian":    ["M", "M", "m", "d", "M", "m", "m"],
    "mixolydian": ["M", "m", "d", "M", "m", "m", "M"],
}
# 七和弦性质
SEVENTH_QUALITY = {
    "major":     ["M7", "m7", "m7", "M7", "D7", "m7", "m7b5"],
    "minor":     ["m7", "m7b5", "aM7", "m7", "D7", "M7", "d7"],
    "minor_nat": ["m7", "m7b5", "M7", "m7", "m7", "M7", "D7"],
    "minor_mel": ["m7", "m7", "aM7", "D7", "D7", "m7b5", "m7b5"],
    "dorian":    ["m7", "m7", "M7", "D7", "m7", "m7b5", "M7"],
    "phrygian":  ["m7", "M7", "D7", "m7", "m7b5", "M7", "m7"],
    "lydian":    ["M7", "D7", "m7", "m7b5", "M7", "m7", "m7"],
    "mixolydian": ["D7", "m7", "m7b5", "M7", "m7", "m7", "M7"],
}

ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII"}
ROMAN_LOWER = {1: "i", 2: "ii", 3: "iii", 4: "iv", 5: "v", 6: "vi", 7: "vii"}
INVERSION_MARK = {0: "", 1: "6", 2: "64", 3: "2"}   # 三和弦：原位/六/四六；七和弦另算


def roman_numeral(degree: int, quality: str) -> str:
    """
    生成规范的罗马数字（含和弦性质记号）：
      d→°   d7/dim7→°7   m7b5→ø7   a→+   m/m7→小写
    """
    lower = quality in ("m", "m7", "mM7", "d", "d7", "dim7", "m7b5")
    base = (ROMAN_LOWER if lower else ROMAN)[degree]
    suffix = {"d": "\u00b0", "d7": "\u00b07", "dim7": "\u00b07",
              "m7b5": "\u00f87", "a": "+"}.get(quality, "")
    return base + suffix


class Key:
    """调：主音音级 + 调式"""

    def __init__(self, tonic, mode: str = "major"):
        if isinstance(tonic, str):
            t = parse_pitch(tonic if any(c.isdigit() for c in tonic) else tonic + "4")
            tonic_pc = t % 12
        else:
            tonic_pc = int(tonic) % 12
        if mode not in SCALES:
            raise ValueError(f"未知调式: {mode}")
        self.tonic_pc = tonic_pc
        self.mode = mode
        self.scale = SCALES[mode]

    # --- 级数 ---
    def degree_pc(self, degree: int) -> int:
        return (self.tonic_pc + self.scale[(degree - 1) % 7]) % 12

    def degree_of_pc(self, p: int):
        """音级 → 最贴近的调内级数（1-7），不在调内返回 None"""
        rel = (p - self.tonic_pc) % 12
        return self.scale.index(rel) + 1 if rel in self.scale else None

    def leading_tone_pc(self) -> int:
        return (self.tonic_pc - 1) % 12          # 导音（大调 7 级 / 和声小调升 7 级）

    def is_leading_tone(self, p: int) -> bool:
        return p % 12 == self.leading_tone_pc() and self.mode != "minor_nat"

    def name(self, flats=False) -> str:
        tbl = FLAT_NAMES if flats else PITCH_NAMES
        mode_zh = {"major": "大调", "minor": "小调", "minor_nat": "自然小调",
                   "minor_mel": "旋律小调", "dorian": "多利亚", "phrygian": "弗里几亚",
                   "lydian": "利底亚", "mixolydian": "混合利底亚"}.get(self.mode, self.mode)
        return f"{tbl[self.tonic_pc]} {mode_zh}"

    def __repr__(self):
        return f"<Key {self.name()}>"


# ============================================================
# 三、和弦
# ============================================================

QUALITIES = {
    "M":     (0, 4, 7),
    "m":     (0, 3, 7),
    "d":     (0, 3, 6),
    "a":     (0, 4, 8),
    "sus4":  (0, 5, 7),
    "M7":    (0, 4, 7, 11),
    "m7":    (0, 3, 7, 10),
    "D7":    (0, 4, 7, 10),
    "m7b5":  (0, 3, 6, 10),
    "d7":    (0, 3, 6, 9),
    "aM7":   (0, 4, 8, 11),
    "mM7":   (0, 3, 7, 11),
    "M9":    (0, 4, 7, 11, 14),
    "m9":    (0, 3, 7, 10, 14),
    "D9":    (0, 4, 7, 10, 14),
    "add9":  (0, 4, 7, 14),
    "madd9": (0, 3, 7, 14),
    "6":     (0, 4, 7, 9),
    "m6":    (0, 3, 7, 9),
    "dim7":  (0, 3, 6, 9),
}

# 功能组（斯波索宾体系）
FUNCTION_GROUP = {
    "major": {1: "T", 2: "S", 3: "D", 4: "S", 5: "D", 6: "T", 7: "D"},
    "minor": {1: "T", 2: "S", 3: "D", 4: "S", 5: "D", 6: "S", 7: "D"},
}

FUNC_ZH = {"T": "主", "S": "下属", "D": "属"}


@dataclass
class Chord:
    """一个和弦：根音音级 + 性质 + 转位 + 所属功能 + 罗马数字"""
    root_pc: int
    quality: str
    inversion: int = 0
    degree: int = 1
    function: str = "T"
    roman: str = "I"
    key: Key | None = None

    def __post_init__(self):
        self.intervals = list(QUALITIES[self.quality])
        self.pcs = [(self.root_pc + i) % 12 for i in self.intervals]
        self.size = len(self.pcs)
        if self.inversion >= self.size:
            self.inversion = 0
        self.bass_pc = self.pcs[self.inversion]
        self.seventh_pc = self.pcs[3] if self.size >= 4 else None   # 七音

    @property
    def name(self) -> str:
        return self.roman

    @property
    def label(self) -> str:
        inv = ""
        if self.size == 3:
            inv = INVERSION_MARK[self.inversion]
        else:
            inv = {0: "7", 1: "65", 2: "43", 3: "2"}[self.inversion]
        return f"{self.roman}{inv}"

    def contains(self, p: int) -> bool:
        return (p % 12) in self.pcs

    def __repr__(self):
        return f"<Chord {self.label} {pitch_name(60 + self.root_pc)}-{self.quality}>"


def diatonic_chord(key: Key, degree: int, seventh: bool = False, inversion: int = 0) -> Chord:
    """按调内级数构造和弦"""
    degree = ((degree - 1) % 7) + 1
    if seventh:
        q = SEVENTH_QUALITY[key.mode][degree - 1]
    else:
        q = TRIAD_QUALITY[key.mode][degree - 1]
    roman = roman_numeral(degree, q)
    func = FUNCTION_GROUP.get(key.mode, FUNCTION_GROUP["major"]).get(degree, "D")
    return Chord(key.degree_pc(degree), q, inversion, degree, func, roman, key)


def secondary_dominant(key: Key, target_degree: int) -> Chord:
    """副属和弦 V/x：把目标和弦当作临时主音，取其上方五度的属和弦（含导音，调外音）"""
    target_pc = key.degree_pc(target_degree)
    root = (target_pc + 7) % 12
    q = "D7" if target_degree != 1 else "D7"
    roman = f"V{INVERSION_MARK[0]}/{ROMAN[target_degree]}"
    return Chord(root, q, 0, target_degree, "D", f"V/{ROMAN[target_degree]}", key)


def borrowed_chord(key: Key, degree: int, quality: str, roman: str,
                   function: str = "S", inversion: int = 0) -> Chord:
    """借用和弦 / 变和弦（那波里、增六、同名大小调交替等）"""
    return Chord(key.degree_pc(degree), quality, inversion, degree, function, roman, key)


def neapolitan(key: Key, inversion: int = 1) -> Chord:
    """那波里六和弦 N6：降 II 级大三和弦的第一转位（斯波索宾：下属功能变和弦）"""
    root = (key.tonic_pc + 1) % 12
    return Chord(root, "M", inversion, 2, "S", "N", key)


# ============================================================
# 四、四部织体生成
# ============================================================

@dataclass
class Voicing:
    """一个具体的 SATB 四部织体（MIDI 音高）"""
    s: int
    a: int
    t: int
    b: int
    chord: Chord | None = None

    def as_dict(self):
        return {"S": self.s, "A": self.a, "T": self.t, "B": self.b}

    def voices(self):
        return [self.s, self.a, self.t, self.b]

    def __repr__(self):
        return (f"[{pitch_name(self.s)} {pitch_name(self.a)} "
                f"{pitch_name(self.t)} {pitch_name(self.b)}]")


def _pitches_of(pcs, lo, hi):
    return [m for m in range(lo, hi + 1) if m % 12 in pcs]


def _doubling_candidates(ch: Chord, key: Key):
    """
    斯波索宾重复音规则：
      · 原位三和弦：重复根音（正三和弦的标准做法）
      · 六和弦：重复根音或五音（不重复三音）
      · 四六和弦：重复低音（即五音）
      · 三音一律不重复（减三和弦可重复三音）
      · 导音绝对禁止重复
      · 七和弦四音齐全，不重复
    """
    if ch.size >= 4:
        return [None]                      # 无重复
    if ch.inversion == 2:                  # 四六和弦 → 重复低音
        return [ch.bass_pc]
    if ch.quality == "d":                  # 减三和弦允许重复三音
        return [ch.pcs[0], ch.pcs[2]]
    if ch.inversion == 1:                  # 六和弦 → 根音或五音
        return [ch.pcs[0], ch.pcs[2]]
    return [ch.pcs[0]]                     # 原位 → 重复根音


def make_voicings(ch: Chord, key: Key, fixed_soprano: int | None = None,
                  max_per_chord: int = 400) -> list[Voicing]:
    """
    枚举一个和弦所有合法的 SATB 排列。

    约束：音域、声部间距、声部不交叉、重复音规则、导音不重复、三音不重复。
    """
    b_lo, b_hi = VOICE_RANGES["B"]
    t_lo, t_hi = VOICE_RANGES["T"]
    a_lo, a_hi = VOICE_RANGES["A"]
    s_lo, s_hi = VOICE_RANGES["S"]

    basses = [m for m in range(b_lo, b_hi + 1) if m % 12 == ch.bass_pc]
    doublings = _doubling_candidates(ch, key)

    out: list[Voicing] = []
    for bass in basses:
        for dbl in doublings:
            if dbl is None:
                # 七和弦：四个音各用一次
                pcs_needed = list(ch.pcs)
                need_counts = {p: 1 for p in pcs_needed}
            else:
                need_counts = {p: 0 for p in ch.pcs}
                for p in ch.pcs:
                    need_counts[p] = 1
                need_counts[dbl] += 1
            # need_counts 是「四声部合计目标用量」（含低音部）
            if need_counts.get(ch.bass_pc, 0) < 1:
                continue

            opts_t = [m for m in _pitches_of(ch.pcs, t_lo, t_hi)]
            opts_a = [m for m in _pitches_of(ch.pcs, a_lo, a_hi)]
            opts_s = ([fixed_soprano] if fixed_soprano is not None
                      else _pitches_of(ch.pcs, s_lo, s_hi))

            for t in opts_t:
                if t <= bass:
                    continue
                for a in opts_a:
                    if a <= t:
                        continue
                    for s in opts_s:
                        if s <= a:
                            continue
                        if s - a > SPACING_MAX["SA"] or a - t > SPACING_MAX["AT"]:
                            continue
                        if t - bass > SPACING_MAX["TB"]:
                            continue
                        # 音级用量核对
                        cnt = {p: 0 for p in ch.pcs}
                        for m in (s, a, t, bass):
                            if m % 12 in cnt:
                                cnt[m % 12] += 1
                            else:
                                cnt[m % 12] = 1
                        ok = True
                        for p in ch.pcs:
                            if cnt.get(p, 0) != need_counts.get(p, 0):
                                ok = False
                                break
                        if not ok:
                            continue
                        # 导音不得重复
                        lt = key.leading_tone_pc()
                        if key.mode != "minor_nat" and cnt.get(lt, 0) > 1:
                            continue
                        # 大三/小三和弦的三音不重复
                        if ch.size == 3 and ch.quality in ("M", "m"):
                            third = ch.pcs[1]
                            if cnt.get(third, 0) > 1:
                                continue
                        out.append(Voicing(s, a, t, bass, ch))
                        if len(out) >= max_per_chord * 4:
                            return out
    return out


# ============================================================
# 五、声部进行规则校验
# ============================================================

RULE_ZH = {
    "RANGE": "超出声部音域",
    "SPACING": "声部间距过大（上三声部超八度 / 次中音与低音超十二度）",
    "CROSSING": "声部交叉",
    "OVERLAP": "声部超越",
    "PARALLEL_5": "平行五度",
    "PARALLEL_8": "平行八度（含同度→八度）",
    "CONTRARY_PERFECT": "反向五度/八度",
    "HIDDEN_5": "隐伏五度",
    "HIDDEN_8": "隐伏八度",
    "AUG_MELODIC": "旋律增音程",
    "TRITONE_LEAP": "旋律三全音跳进",
    "BIG_LEAP": "超过八度的大跳",
    "LT_UNRESOLVED": "导音未上行解决到主音",
    "SEVENTH_UNRESOLVED": "和弦七音未下行级进解决",
    "FUNC_REVERSE": "功能逆行（D→S）",
    "DOUBLED_LT": "重复导音",
    "DOUBLED_3RD": "重复三音",
    "UNPREPARED_DISS": "不协和音未预备",
    "VOICE_CROSS_BASS": "低音部越过次中音",
}

# 硬规则：出现即判为不合规（严格写作）
HARD_RULES = {
    "RANGE", "SPACING", "CROSSING", "OVERLAP", "PARALLEL_5", "PARALLEL_8",
    "BIG_LEAP", "LT_UNRESOLVED", "SEVENTH_UNRESOLVED",
    "DOUBLED_LT", "DOUBLED_3RD", "FUNC_REVERSE",
}
# 软规则：扣分，允许为了音乐性妥协
SOFT_WEIGHT = {
    "HIDDEN_5": 24.0, "HIDDEN_8": 24.0, "AUG_MELODIC": 18.0,
    "TRITONE_LEAP": 8.0, "UNPREPARED_DISS": 12.0,
    # 反向五八度：严格写作中通常避免，但各教材口径不一（斯波索宾以「平行/隐伏」为主），
    # 故列为警示项而非硬伤 —— 需要绝对严格时可用 --strict-contrary 提升为硬规则
    "CONTRARY_PERFECT": 22.0,
}


def _is_perfect(interval: int) -> str | None:
    i = abs(interval) % 12
    if i == 7:
        return "5"
    if i == 0:
        return "8"
    return None


def check_voicing(v: Voicing, key: Key) -> list[str]:
    """单个织体的静态检查（音域/间距/交叉）"""
    issues = []
    d = v.as_dict()
    for name in VOICE_ORDER:
        lo, hi = VOICE_RANGES[name]
        if not (lo <= d[name] <= hi):
            issues.append(f"RANGE:{name}")
    if v.s - v.a > SPACING_MAX["SA"]:
        issues.append("SPACING:SA")
    if v.a - v.t > SPACING_MAX["AT"]:
        issues.append("SPACING:AT")
    if v.t - v.b > SPACING_MAX["TB"]:
        issues.append("SPACING:TB")
    if not (v.s > v.a > v.t > v.b):
        issues.append("CROSSING")
    return issues


def _melodic_issues(a: int, b: int, key: Key) -> list[str]:
    """两音之间的旋律进行检查"""
    out = []
    leap = abs(b - a)
    if leap > 12:
        out.append("BIG_LEAP")
    if leap == 6:
        out.append("TRITONE_LEAP")
    # 增二度：和声小调 b6→7 的进行
    if key.mode in ("minor", "minor_mel"):
        d1, d2 = key.degree_of_pc(a % 12), key.degree_of_pc(b % 12)
        if leap == 3 and {d1, d2} == {6, 7}:
            out.append("AUG_MELODIC")
    # 增四度：4→7 级
    if leap == 6:
        d1, d2 = key.degree_of_pc(a % 12), key.degree_of_pc(b % 12)
        if {d1, d2} == {4, 7}:
            out.append("AUG_MELODIC")
    return out


def transition_issues(prev: Voicing, cur: Voicing, key: Key) -> list[str]:
    """
    两和弦之间的声部进行检查（斯波索宾严格写作规则）。
    返回违规码列表，形如 ["PARALLEL_5:S-A", "LT_UNRESOLVED:S"]。
    """
    issues: list[str] = []
    p = prev.as_dict()
    c = cur.as_dict()

    # ---- 声部超越（overlap）：某声部越过相邻声部上一和弦的位置 ----
    if c["S"] < p["A"] or c["A"] > p["S"]:
        issues.append("OVERLAP:S-A")
    if c["A"] < p["T"] or c["T"] > p["A"]:
        issues.append("OVERLAP:A-T")
    if c["T"] < p["B"] or c["B"] > p["T"]:
        issues.append("OVERLAP:T-B")
    if not (c["S"] > c["A"] > c["T"] > c["B"]):
        issues.append("CROSSING")

    # ---- 平行 / 反向 / 隐伏五八度 ----
    upper = VOICE_ORDER
    for i in range(4):
        for j in range(i + 1, 4):
            v1, v2 = upper[i], upper[j]
            i1, i2 = p[v1], p[v2]
            c1, c2 = c[v1], c[v2]
            ptype = _is_perfect(i1 - i2)
            ctype = _is_perfect(c1 - c2)
            if ptype and ctype:
                d1, d2 = c1 - i1, c2 - i2
                if d1 == 0 and d2 == 0:
                    continue                        # 静止保持，允许
                same_dir = (d1 > 0 and d2 > 0) or (d1 < 0 and d2 < 0)
                if same_dir:
                    code = "PARALLEL_5" if (ptype == "5" and ctype == "5") else \
                           "PARALLEL_8" if (ptype == "8" and ctype == "8") else \
                           "PARALLEL_8"
                    issues.append(f"{code}:{v1}-{v2}")
                else:
                    issues.append(f"CONTRARY_PERFECT:{v1}-{v2}")
            # 隐伏五八度：外声部同向进入五/八度，且高音部为跳进
            if {v1, v2} == {"S", "B"} and ctype and not ptype:
                d1, d2 = c1 - i1, c2 - i2
                same_dir = (d1 > 0 and d2 > 0) or (d1 < 0 and d2 < 0)
                if same_dir and abs(d1) > 2:
                    issues.append(f"HIDDEN_{ctype}:S-B")

    # ---- 导音解决 ----
    lt = key.leading_tone_pc()
    tonic = key.tonic_pc
    for name in VOICE_ORDER:
        if p[name] % 12 == lt and prev.chord and prev.chord.contains(tonic):
            pass
        if p[name] % 12 == lt and c[name] % 12 == tonic:
            if c[name] - p[name] != 1:
                issues.append(f"LT_UNRESOLVED:{name}")
        elif p[name] % 12 == lt and c[name] % 12 != tonic:
            # 属七解决时内声部可下行到属音（省略五音的主和弦）
            inner = name in ("A", "T")
            to_dominant = c[name] % 12 == (tonic + 7) % 12
            if inner and to_dominant and prev.chord and prev.chord.quality.startswith("D"):
                continue
            issues.append(f"LT_UNRESOLVED:{name}")

    # ---- 七音解决（下行级进）----
    if prev.chord and prev.chord.seventh_pc is not None:
        sev = prev.chord.seventh_pc
        for name in VOICE_ORDER:
            if p[name] % 12 == sev:
                step = c[name] - p[name]
                if not (-2 <= step <= -1):
                    issues.append(f"SEVENTH_UNRESOLVED:{name}")

    # ---- 旋律进行 ----
    for name in VOICE_ORDER:
        for code in _melodic_issues(p[name], c[name], key):
            issues.append(f"{code}:{name}")

    # ---- 功能逆行 D→S ----
    if prev.chord and cur.chord:
        if prev.chord.function == "D" and cur.chord.function == "S":
            # 例外：D→S6（经过型）或低音级进下行的经过四六
            allowed = (cur.chord.inversion == 1 and
                       abs((cur.b - prev.b)) <= 2)
            if not allowed:
                issues.append("FUNC_REVERSE")

    return issues


def score_transition(prev: Voicing, cur: Voicing, key: Key,
                     strict: bool = True) -> tuple[float, list[str]]:
    """把违规列表折算成代价。硬规则 → 无穷大；软规则 → 加权。"""
    issues = transition_issues(prev, cur, key)
    cost = 0.0
    for it in issues:
        code = it.split(":")[0]
        if code in HARD_RULES:
            if strict:
                return math.inf, issues
            cost += 60.0
        else:
            cost += SOFT_WEIGHT.get(code, 6.0)

    # ---- 和声进行偏好（不违反规则，但决定「像不像一首曲子」）----
    if prev.chord and cur.chord:
        # 四六和弦的合法用法：终止四六 K64 → V；其余四六（经过/辅助）给出惩罚
        if prev.chord.inversion == 2:
            k64 = (prev.chord.degree == 1 and cur.chord.degree == 5
                   and cur.chord.inversion == 0)
            if not k64:
                cost += 12.0
        if cur.chord.inversion == 2 and cur.chord.degree not in (1, 5):
            cost += 8.0
        # 避免和弦原地踏步，鼓励功能推进
        if prev.chord.label == cur.chord.label:
            cost += 4.0
        elif prev.chord.degree == cur.chord.degree:
            cost += 1.0
        # 功能序进：T→S→D→T 最顺；S→T（变格）次之
        pf, cf = prev.chord.function, cur.chord.function
        if (pf, cf) == ("S", "T"):
            cost += 1.5
        # 属七→主 是强解决，给奖励
        if prev.chord.quality in ("D7", "d7") and cf == "T":
            cost -= 1.5
        # 原位优先
        if cur.chord.inversion == 0:
            cost -= 0.5

    # 声部进行平滑度：总音程越小越好（轻微偏好）
    pd, cd = prev.as_dict(), cur.as_dict()
    cost += 0.05 * sum(abs(cd[k] - pd[k]) for k in VOICE_ORDER)
    return cost, issues


# ============================================================
# 六、自动配和声（动态规划全局最优）
# ============================================================

# 斯波索宾严格写作里，七和弦主要使用 II7 / V7 / VII7；
# 主七、下属七、六级七属爵士/流行语汇，不进入严格写作的默认候选集。
DEFAULT_CHORD_SET = {
    "major": [(1, False), (2, False), (3, False), (4, False), (5, False),
              (6, False), (7, False), (2, True), (5, True), (7, True)],
    "minor": [(1, False), (2, False), (3, False), (4, False), (5, False),
              (6, False), (7, False), (2, True), (5, True), (7, True)],
}


def chord_style_weight(ch: Chord) -> float:
    """
    和弦选用的审美权重（不涉及规则对错，只影响"像不像一首正常的合唱"）。
    · 七级（ vii° / vii°7 ）是强属功能，不能当默认和弦反复用
    · 三级功能含混（大调兼 T/D，小调是增三和弦），少用
    · 非终止/非属的四六和弦重复扣分
    """
    w = 0.0
    if ch.degree == 7:
        w += 7.0
    elif ch.degree == 3:
        w += 5.0
    if ch.quality == "a":          # 增三和弦（小调 III+）色彩特殊，非必要时不用
        w += 6.0
    if ch.inversion == 2 and ch.degree not in (1, 5):
        w += 8.0
    return w


def candidate_voicings(melody_pitch: int, key: Key, seventh_ok: bool = True,
                       extra_chords: list[Chord] | None = None) -> list[Voicing]:
    """给定旋律音，列出所有可用的（和弦, 织体）候选"""
    out: list[Voicing] = []
    mset = list(DEFAULT_CHORD_SET.get(key.mode, DEFAULT_CHORD_SET["major"]))
    chords: list[Chord] = []
    for deg, sev in mset:
        if sev and not seventh_ok:
            continue
        for inv in range(4 if sev else 3):
            chords.append(diatonic_chord(key, deg, sev, inv))
    for ch in (extra_chords or []):
        chords.append(ch)
    for ch in chords:
        if not ch.contains(melody_pitch):
            continue
        out.extend(make_voicings(ch, key, fixed_soprano=melody_pitch))
    return out


def harmonize(melody, key: Key, strict: bool = True, seventh_ok: bool = True,
              cadence: str = "perfect", extra_chords=None,
              first_chord: Chord | None = None, seed: int = 0):
    """
    为旋律配四部和声。

    melody: [(pitch, duration_beats), ...]
    返回 dict(path=[Voicing], chords=[Chord], issues=[...], total_cost=float)
    """
    n = len(melody)
    if n == 0:
        return {"path": [], "chords": [], "issues": [], "total_cost": 0.0}

    # ---- 各位置候选 ----
    layers: list[list[Voicing]] = []
    for idx, (pitch, _dur) in enumerate(melody):
        cands = candidate_voicings(pitch, key, seventh_ok, extra_chords)
        # 首和弦：优先主和弦原位
        if idx == 0 and first_chord is not None:
            pref = [v for v in cands if v.chord.degree == first_chord.degree
                    and v.chord.inversion == first_chord.inversion]
            if pref:
                cands = pref
        # 末和弦：按终止式类型收敛
        if idx == n - 1:
            if cadence == "perfect":
                pref = [v for v in cands if v.chord.degree == 1
                        and v.chord.inversion == 0 and v.chord.size == 3]
                cands = pref or cands
            elif cadence == "deceptive":
                pref = [v for v in cands if v.chord.degree == 6]
                cands = pref or cands
            elif cadence == "half":
                # 半终止停在属和弦；若旋律尾音不是属和弦音，退而用终止四六 K64
                pref = [v for v in cands if v.chord.degree == 5]
                if not pref:
                    pref = [v for v in cands if v.chord.degree == 1
                            and v.chord.inversion == 2]
                cands = cands if idx == 0 else (pref or cands)
        if not cands:
            raise ValueError(f"第 {idx+1} 个旋律音 {pitch_name(pitch)} "
                             f"无法配出合法四部织体（调内和弦都不含此音）——"
                             f"请检查旋律音是否在 {key.name()} 内。")
        layers.append(cands)

    warnings: list[str] = []
    last_pitch = melody[-1][0]
    if cadence == "half" and not any(v.chord.degree == 5 for v in layers[-1]):
        warnings.append(
            f"半终止：旋律尾音 {pitch_name(last_pitch)} 不在属和弦（V/V7）内，"
            f"已用终止四六 K64 收束。严格说半终止应停在 V 上，"
            f"建议旋律最后落在 D/G/B 之一。")
    if cadence == "perfect" and last_pitch % 12 != key.tonic_pc:
        warnings.append(
            f"完满终止：旋律尾音 {pitch_name(last_pitch)} 不是主音，"
            f"只能做成不完满终止（主和弦非根位高音）。"
            f"若需要完满终止，旋律应结束在主音 {pitch_name(60 + key.tonic_pc)} 上。")

    # ---- 首层代价：偏好明亮、稳定的排列 ----
    INF = math.inf
    cost = [0.0] * len(layers[0])
    for i, v in enumerate(layers[0]):
        c = 0.0
        for code in check_voicing(v, key):
            c += INF if (strict and code.split(":")[0] in HARD_RULES) else 30.0
        if v.chord.degree == 1 and v.chord.inversion == 0:
            c -= 3.0
        c += chord_style_weight(v.chord)
        cost[i] = c
    back: list[list[int]] = [[-1] * len(l) for l in layers]

    # ---- 逐层 DP ----
    for li in range(1, len(layers)):
        prev_layer, cur_layer = layers[li - 1], layers[li]
        new_cost = [INF] * len(cur_layer)
        for j, cur in enumerate(cur_layer):
            static = 0.0
            for code in check_voicing(cur, key):
                static += INF if (strict and code.split(":")[0] in HARD_RULES) else 30.0
            if static >= INF:
                continue
            static += chord_style_weight(cur.chord)
            best, best_i = INF, -1
            for i, prev in enumerate(prev_layer):
                if cost[i] >= INF:
                    continue
                tc, _iss = score_transition(prev, cur, key, strict)
                if tc >= INF:
                    continue
                total = cost[i] + tc
                if total < best:
                    best, best_i = total, i
            new_cost[j] = best + static
            back[li][j] = best_i
        cost = new_cost
        if all(c >= INF for c in cost):
            prev_p = melody[li - 1][0]
            cur_p = melody[li][0]
            hint = ""
            if key.is_leading_tone(prev_p) and cur_p % 12 != key.tonic_pc:
                hint = (f"旋律第 {li} 个音 {pitch_name(prev_p)} 是导音，"
                        f"严格写作要求它上行半音解决到主音 "
                        f"{pitch_name(60 + key.tonic_pc)}，但第 {li+1} 个音是 "
                        f"{pitch_name(cur_p)}。")
            elif abs(cur_p - prev_p) > 12:
                hint = (f"旋律从 {pitch_name(prev_p)} 到 {pitch_name(cur_p)} "
                        f"超过八度，严格写作不允许。")
            raise RuntimeError(
                f"第 {li+1} 个和弦无可行连接。{hint}"
                f"可用 --relax 放宽为自由写作（允许软规则妥协），"
                f"或改写此处旋律。")

    # ---- 回溯 ----
    end = min(range(len(cost)), key=lambda i: cost[i])
    path_idx = [0] * len(layers)
    path_idx[-1] = end
    for li in range(len(layers) - 1, 0, -1):
        path_idx[li - 1] = back[li][path_idx[li]]

    path = [layers[li][path_idx[li]] for li in range(len(layers))]
    issues = []
    for li in range(1, len(path)):
        for it in transition_issues(path[li - 1], path[li], key):
            issues.append({"position": li + 1, "rule": it.split(":")[0],
                           "detail": it, "zh": RULE_ZH.get(it.split(":")[0], it)})
    return {
        "path": path,
        "chords": [v.chord for v in path],
        "issues": issues,
        "warnings": warnings,
        "total_cost": cost[end],
        "key": key,
        "melody": melody,
    }


# ============================================================
# 七、输出与报告
# ============================================================

def result_to_tracks(result, velocity=80, channel_map=None):
    """把配和声结果转成 MIDI 轨列表"""
    path = result["path"]
    melody = result["melody"]
    ch_map = channel_map or {"S": 0, "A": 1, "T": 2, "B": 3}
    tracks = []
    for voice in VOICE_ORDER:
        notes = []
        t = 0.0
        for v, (pitch, dur) in zip(path, melody):
            notes.append((t, dur, v.as_dict()[voice], velocity))
            t += dur
        tracks.append({"name": {"S": "Soprano 高音", "A": "Alto 中音",
                                "T": "Tenor 次中音", "B": "Bass 低音"}[voice],
                       "channel": ch_map[voice], "notes": notes})
    markers = []
    t = 0.0
    for v, (_p, dur) in zip(path, melody):
        markers.append((t, f"{v.chord.label}"))
        t += dur
    return tracks, markers


def report_text(result) -> str:
    """生成人类可读的和声报告"""
    key = result["key"]
    path, melody = result["path"], result["melody"]
    lines = [f"调性：{key.name()}　和弦数：{len(path)}", ""]
    lines.append("小节 | 和弦 | 功能 | 高音 中音 次中音 低音 | 旋律")
    lines.append("-----|------|------|----------------------|------")
    for i, (v, (p, d)) in enumerate(zip(path, melody), 1):
        ch = v.chord
        lines.append(f"{i:>4} | {ch.label:<4} | {FUNC_ZH.get(ch.function, '?')} "
                     f"| {pitch_name(v.s):<3} {pitch_name(v.a):<3} "
                     f"{pitch_name(v.t):<3} {pitch_name(v.b):<3} | {pitch_name(p)}")
    lines.append("")
    for w in result.get("warnings", []):
        lines.append(f"⚠️  {w}")
    if result.get("warnings"):
        lines.append("")
    if result["issues"]:
        lines.append(f"规则体检：{len(result['issues'])} 处待处理")
        for it in result["issues"]:
            lines.append(f"  · 第{it['position']}个和弦：{it['zh']}（{it['detail']}）")
    else:
        lines.append("规则体检：✅ 未发现违反斯波索宾严格写作规则之处")
    return "\n".join(lines)


def progression_chords(key: Key, degrees, seventh_flags=None) -> list[Chord]:
    """由级数序列生成和弦序列，degrees 可含 'V/V' 形式的副属和弦"""
    out = []
    for i, d in enumerate(degrees):
        sev = bool(seventh_flags[i]) if seventh_flags else False
        if isinstance(d, str) and "/" in d:
            target = int(d.split("/")[1].replace("V", "5").replace("I", "1")
                         .replace("IV", "4").replace("II", "2").replace("VI", "6")
                         .replace("III", "3").replace("VII", "7"))
            out.append(secondary_dominant(key, target))
        else:
            out.append(diatonic_chord(key, int(d), sev, 0))
    return out


def realize_progression(chords: list[Chord], key: Key, strict: bool = False,
                        allow_inversions: bool = True):
    """
    为一条和弦进行配上四部织体。

    allow_inversions=True 时，每个和弦允许自动选择转位（低音线更平滑），
    这是四部合唱写作的实际做法——固定全部原位会让低音线出现大量跳进，
    反而制造平行/超越问题。
    """
    layers = []
    for ch in chords:
        opts = []
        if allow_inversions:
            for inv in range(ch.size):
                opts.extend(make_voicings(
                    Chord(ch.root_pc, ch.quality, inv, ch.degree,
                          ch.function, ch.roman, key), key))
        else:
            opts = make_voicings(ch, key)
        layers.append(opts or make_voicings(ch, key))
    # 终止和弦必须是原位（四六和弦不能用来收尾）
    if len(layers) > 1:
        root_only = [v for v in layers[-1] if v.chord.inversion == 0]
        if root_only:
            layers[-1] = root_only
    for i, l in enumerate(layers):
        if not l:
            raise ValueError(f"和弦 {chords[i].label} 无合法四部排列")
    INF = math.inf
    cost = [0.0] * len(layers[0])
    back = [[-1] * len(l) for l in layers]
    for li in range(1, len(layers)):
        new_cost = [INF] * len(layers[li])
        for j, cur in enumerate(layers[li]):
            best, bi = INF, -1
            for i, prev in enumerate(layers[li - 1]):
                if cost[i] >= INF:
                    continue
                tc, _ = score_transition(prev, cur, key, strict)
                if tc >= INF:
                    continue
                if cost[i] + tc < best:
                    best, bi = cost[i] + tc, i
            new_cost[j] = best
            back[li][j] = bi
        cost = new_cost
    end = min(range(len(cost)), key=lambda i: cost[i])
    idx = [end]
    for li in range(len(layers) - 1, 0, -1):
        idx.insert(0, back[li][idx[0]])
    path = [layers[i][idx[i]] for i in range(len(layers))]
    issues = []
    for li in range(1, len(path)):
        for it in transition_issues(path[li - 1], path[li], key):
            issues.append({"position": li + 1, "rule": it.split(":")[0],
                           "detail": it, "zh": RULE_ZH.get(it.split(":")[0], it)})
    return {"path": path, "chords": chords, "issues": issues,
            "key": key, "melody": [(v.s, 1.0) for v in path]}


# ============================================================
# 八、自检
# ============================================================

if __name__ == "__main__":
    k = Key("C", "major")
    print("调:", k.name(), "导音:", pitch_name(60 + k.leading_tone_pc()))
    for deg in range(1, 8):
        ch = diatonic_chord(k, deg, False)
        print(f"  {deg} 级: {ch.label:<4} 根音 {pitch_name(60+ch.root_pc)} "
              f"性质 {ch.quality:<2} 功能 {ch.function} 织体数 {len(make_voicings(ch, k))}")

    # C 大调经典旋律（《欢乐颂》开头）
    melody = [("E4", 1), ("E4", 1), ("F4", 1), ("G4", 1),
              ("G4", 1), ("F4", 1), ("E4", 1), ("D4", 1)]
    mel = [(parse_pitch(p), d) for p, d in melody]
    res = harmonize(mel, k, cadence="half")
    print()
    print(report_text(res))
