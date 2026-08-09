"""IPA → 音素序列分词器 + 覆盖率纯函数（GOP A 前置门的核心）。
词典 IPA 实测格式：`/loʊf/`、`/ˈɔːrdər/` —— 带斜杠包裹、无空格、含重音(ˈ)/长音(ː)。
分词用 trie 最长匹配（非空格切分）：按长符号优先逐符号匹配；斜杠/重音/连字符为元字符剥离。
IPA_TO_ESPEAK / ENGLISH_ESPEAK_SYMBOLS 是「词典符号 ↔ 模型音素符号」的桥，由
scripts/ipa-coverage.py（Task 8）真机 dump 模型 alphabet 后回填；score_gop 运行时读取。"""
from __future__ import annotations

# 词典（CMU 风格）IPA 符号表。长符号优先保证 trie 最长匹配先命中。
# 若真实 dictionary.json 含未列符号，tokenize_ipa 抛 ValueError → 覆盖率门暴露该符号，补进表即可。
DICT_IPA_SYMBOLS: tuple[str, ...] = (
    "iː", "eɪ", "aɪ", "ɔɪ", "aʊ", "oʊ", "ɑː", "ɔː", "uː", "ɜː",        # 双元音/长元音
    "ɪ", "ɛ", "e", "æ", "ɒ", "ʊ", "ʌ", "ə", "ɚ", "ɝ",                  # 短元音/中央元音
    "tʃ", "dʒ", "θ", "ð", "ʃ", "ʒ", "ŋ",                                # 双字符/特有辅音
    "p", "b", "t", "d", "k", "ɡ", "f", "v", "s", "z", "h", "m", "n", "l", "r", "j", "w",
)

# 词典 IPA 符号 → 模型音素符号。KEY 是 tokenize_ipa 产出的词典符号；VALUE 是模型词表符号。
# Task 8 覆盖率门 dump 模型 alphabet 后回填（identity-first + 人工补全），随测试覆盖扩展。
IPA_TO_ESPEAK: dict[str, str] = {}

# 模型词表中「英语音素」子集（GOP margin 分母用；排除其他语言结构性异类音素与 blank）。
# Task 8 覆盖率门 dump 后回填。
ENGLISH_ESPEAK_SYMBOLS: frozenset[str] = frozenset()

_STRIP_CHARS = "ˈˌ-"   # 重音主次、连字符是元字符，剥离；ː 是符号组成部分，保留


def tokenize_ipa(ipa: str, *, symbols: tuple[str, ...] = DICT_IPA_SYMBOLS) -> list[str]:
    """词典 IPA → 音素符号列表。剥离 /ˈˌ- 元字符后按最长符号匹配（trie）。
    未匹配字符抛 ValueError（调用方据此判该词覆盖率 0，而非静默错分）。"""
    syms = sorted(symbols, key=len, reverse=True)
    body = ipa.strip().strip("/").translate(str.maketrans("", "", _STRIP_CHARS))
    out: list[str] = []
    i = 0
    while i < len(body):
        for s in syms:
            if body.startswith(s, i):
                out.append(s)
                i += len(s)
                break
        else:
            raise ValueError(f"unmapped IPA symbol at {body[i:]!r} in {ipa!r}")
    return out


def coverage_report(words: list[tuple[str, str | None]], mapping: dict[str, str],
                    english_symbols: frozenset[str]) -> dict:
    """对 dictionary.json 全部目标词 IPA 逐符号算覆盖率。
    规则：词全部音素符号在 mapping 且映射后 ∈ english_symbols → coverable；否则 partial。
    裁决：ratio ≥ 0.8 → ok；0.5 ≤ ratio < 0.8 → partial；< 0.5 → defer（A 转 phase-7）。"""
    total = len(words)
    coverable = partial = 0
    unmapped: set[str] = set()
    unaligned: set[str] = set()
    for _lemma, ipa in words:
        if not ipa:
            partial += 1
            continue
        try:
            toks = tokenize_ipa(ipa)
        except ValueError:
            partial += 1
            continue
        mapped: list[str] = []
        ok = True
        for t in toks:
            e = mapping.get(t)
            if e is None:
                unmapped.add(t)
                ok = False
            else:
                mapped.append(e)
        if ok and all(s in english_symbols for s in mapped):
            coverable += 1
        else:
            partial += 1
            if ok:
                unaligned.update(s for s in mapped if s not in english_symbols)
    ratio = coverable / total if total else 0.0
    verdict = "ok" if ratio >= 0.8 else ("partial" if ratio >= 0.5 else "defer")
    return {"total": total, "coverable": coverable, "partial": partial,
            "ratio": round(ratio, 4), "verdict": verdict,
            "unmapped_symbols": sorted(unmapped), "unaligned_symbols": sorted(unaligned)}
