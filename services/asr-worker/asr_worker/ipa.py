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
IPA_TO_ESPEAK: dict[str, str] = {
    'aɪ': 'aɪ',
    'aʊ': 'aʊ',
    'b': 'b',
    'd': 'd',
    'dʒ': 'dʒ',
    'e': 'e',
    'eɪ': 'eɪ',
    'f': 'f',
    'h': 'h',
    'iː': 'iː',
    'j': 'j',
    'k': 'k',
    'l': 'l',
    'm': 'm',
    'n': 'n',
    'oʊ': 'oʊ',
    'p': 'p',
    'r': 'r',
    's': 's',
    't': 't',
    'tʃ': 'tʃ',
    'uː': 'uː',
    'v': 'v',
    'w': 'w',
    'z': 'z',
    'æ': 'æ',
    'ð': 'ð',
    'ŋ': 'ŋ',
    'ɑː': 'ɑː',
    'ɒ': 'ɒ',
    'ɔɪ': 'ɔɪ',
    'ɔː': 'ɔː',
    'ə': 'ə',
    'ɚ': 'ɚ',
    'ɛ': 'ɛ',
    'ɜː': 'ɜː',
    'ɡ': 'ɡ',
    'ɪ': 'ɪ',
    'ʃ': 'ʃ',
    'ʊ': 'ʊ',
    'ʌ': 'ʌ',
    'ʒ': 'ʒ',
    'θ': 'θ',
}

# 模型词表中「英语音素」子集（GOP margin 分母用；排除其他语言结构性异类音素与 blank）。
# Task 8 覆盖率门 dump 后回填。
ENGLISH_ESPEAK_SYMBOLS: frozenset[str] = frozenset(['1', '??', 'N', 'S', 'X', 'a', 'a.', 'a.ː', 'a1', 'a2', 'a4', 'a5', 'ai', 'ai2', 'ai5', 'aiɜ', 'au', 'aɜ', 'aɨ', 'aɪ', 'aɪə', 'aɪɚ', 'aʊ', 'aː', 'ã', 'b', 'bʰ', 'bʲ', 'bː', 'c', 'cʰ', 'cʰcʰ', 'cː', 'd', 'dZ', 'd[', 'd^', 'dzː', 'dʑ', 'dʑʲ', 'dʒ', 'dʒʲ', 'dʒː', 'dʰ', 'dʰː', 'dʲ', 'dʲʲ', 'dː', 'dˤ', 'dˤdˤ', 'e', 'e:', 'ee', 'ei2', 'ei5', 'eiɜ', 'eɑ', 'eə', 'eɪ', 'eʊ', 'eː', 'ẽ', 'ẽː', 'e̞', 'e̞e̞', 'f', 'fʲ', 'h', 'i', 'i.', 'i.1', 'i.2', 'i.4', 'i.5', 'i.ɜ', 'i.ː', 'i1', 'i2', 'i4', 'i5', 'i:', 'ie', 'iou1', 'iou2', 'iou4', 'iou5', 'iouɜ', 'iɑ1', 'iɑ2', 'iɑ5', 'iɑɜ', 'iə', 'iɛ1', 'iɛ2', 'iɛ4', 'iɛ5', 'iɛɜ', 'iɜ', 'iʊ', 'iː', 'iː1', 'iːː', 'ĩ', 'i̪1', 'i̪2', 'i̪4', 'i̪5', 'i̪ɜ', 'j', 'ja', 'ju', 'k', 'kh', 'kʰ', 'kʰː', 'kʲ', 'kː', 'l', 'lː', 'l̩', 'm', 'mʲ', 'n', 'nʲ', 'nʲʲ', 'n̩', 'o', 'o1', 'o2', 'o4', 'o5', 'o:', 'oe', 'oe:', 'onɡ2', 'onɡ5', 'onɡɜ', 'ou1', 'ou2', 'ou5', 'ouɜ', 'oɜ', 'oɪ', 'oʊ', 'oː', 'oːɹ', 'õ', 'o̞', 'o̞o̞', 'p', 'pf', 'ph', 'pʰ', 'pʲ', 'pː', 'q', 'qː', 'r', 'r.', 'rʲ', 'r̝', 'r̝̊', 'r̩', 's', 's.', 's^', 'sx', 'sʲ', 's̪', 's̪ː', 't', 'tS', 't[', 't^', 't^ː', 'th', 'ts', 'ts.', 'ts.h', 'tsh', 'tsʲ', 'tsː', 'tɕ', 'tɕh', 'tɕʲ', 'tʃ', 'tʃʰ', 'tʃʲ', 'tʃː', 'tʰ', 'tʲ', 'tː', 't̪', 't̪ː', 'u', 'u"', 'u.', 'u.ː', 'u1', 'u2', 'u4', 'u5', 'u:', 'ua1', 'ua2', 'ua4', 'ua5', 'uai5', 'uaiɜ', 'uaɜ', 'uei2', 'uei5', 'ueiɜ', 'ui', 'uo', 'uo1', 'uo2', 'uo5', 'uoɜ', 'uə2', 'uə5', 'uəɜ', 'uɜ', 'uɨ', 'uɪ', 'uː', 'ũ', 'v', 'vʲ', 'w', 'x', 'xʲ', 'y', 'y1', 'y2', 'y5', 'y:', 'yi', 'yiɜ', 'yu2', 'yu5', 'yuɜ', 'yæ2', 'yæ5', 'yæɜ', 'yə2', 'yə5', 'yəɜ', 'yɛ2', 'yɛ5', 'yɛ5ʲ', 'yɛɜ', 'yɜ', 'yː', 'z', 'ä', 'ää', 'æ', 'æi', 'æiː', 'æː', 'ç', 'ð', 'ø', 'øi', 'øː', 'ħ', 'ŋ', 'œ', 'œː', 'œ̃', 'ũ', 'ɐ', 'ɐɐ', 'ɐ̃', 'ɐ̃ʊ̃', 'ɑ', 'ɑ1', 'ɑ2', 'ɑ4', 'ɑ5', 'ɑ:', 'ɑu2', 'ɑu5', 'ɑuɜ', 'ɑɜ', 'ɑɨ', 'ɑː', 'ɑːɹ', 'ɑ̃', 'ɒ', 'ɔ', 'ɔø', 'ɔɨ', 'ɔɪ', 'ɔː', 'ɔːɹ', 'ɔ̃', 'ɕ', 'ɕʲ', 'ɖ', 'ɖʰ', 'ə', 'ə1', 'ə2', 'ə4', 'ə5', 'əl', 'ər1', 'ər2', 'ər4', 'ər5', 'ərɜ', 'əɜ', 'əɨ', 'əɪ', 'əʊ', 'əː1', 'ɚ', 'ɛ', 'ɛɪ', 'ɛɹ', 'ɛʊ', 'ɛː', 'ɛ̃', 'ɜ', 'ɜː', 'ɟ', 'ɟʰ', 'ɟː', 'ɡ', 'ɡʰ', 'ɡʲ', 'ɡː', 'ɣ', 'ɨ', 'ɨu', 'ɨː', 'ɪ', 'ɪ^', 'ɪu', 'ɪuː', 'ɪɹ', 'ɪː', 'ɫ', 'ɬ', 'ɭ', 'ɭʲ', 'ɯ', 'ɯɯ', 'ɯᵝ', 'ɯᵝɯᵝ', 'ɲ', 'ɳ', 'ɴ', 'ɵ', 'ɵː', 'ɸ', 'ɹ', 'ɻ', 'ɽ', 'ɾ', 'ʁ', 'ʂ', 'ʂʲ', 'ʃ', 'ʈ', 'ʈʰ', 'ʉ', 'ʊ', 'ʊə', 'ʊɹ', 'ʊː', 'ʋ', 'ʌ', 'ʎ', 'ʐ', 'ʑ', 'ʒ', 'ʒʲ', 'ʔ', 'ʕ', 'ʝ', 'ʲ', 'β', 'θ', 'χ', 'ᵻ'])

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
