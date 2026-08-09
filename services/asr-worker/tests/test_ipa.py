import pytest

from asr_worker.ipa import coverage_report, tokenize_ipa


def test_tokenize_loaf_real_dict_format():
    # 实测 dictionary.json IPA：带斜杠、无空格、含双元音
    assert tokenize_ipa("/loʊf/") == ["l", "oʊ", "f"]


def test_tokenize_order_real_dict_format():
    # 含重音符号 ˈ 与长音符 ː：重音剥离、长音是符号一部分
    assert tokenize_ipa("/ˈɔːrdər/") == ["ɔː", "r", "d", "ə", "r"]


def test_tokenize_bread_buy():
    assert tokenize_ipa("/bred/") == ["b", "r", "e", "d"]
    assert tokenize_ipa("/baɪ/") == ["b", "aɪ"]


def test_tokenize_unmapped_symbol_raises():
    with pytest.raises(ValueError):
        tokenize_ipa("/bɹɛd/")   # ɹ 不在符号表 → 抛错（让覆盖率为 0 而非静默）


def test_coverage_full_ok():
    words = [("loaf", "/loʊf/"), ("bread", "/bred/"), ("order", "/ˈɔːrdər/"), ("buy", "/baɪ/")]
    # order → [ɔː, r, d, ə, r]：ə 必须在 mapping 与 english 内才能全部 coverable（brief 原始 fixture 漏了 ə）
    mapping = {"l": "l", "oʊ": "o", "f": "f", "b": "b", "r": "r", "e": "e",
               "d": "d", "aɪ": "aI", "ɔː": "O:", "ə": "ə"}
    english = frozenset("l o f b r e d aI O: ə".split())
    report = coverage_report(words, mapping, english)
    assert report["ratio"] == 1.0
    assert report["verdict"] == "ok"
    # unmapped_symbols 为 sorted(list)（Task 8 需 JSON 序列化，不能是 set）；空即无未映射符号
    assert report["unmapped_symbols"] == []


def test_coverage_below_half_defers():
    # 10 个 /baɪ/（aɪ 未映射 → partial）+ 3 个代表性词（仅 loaf 可 cover）→ coverable 1/13 < 0.5 → defer。
    # brief 原始 fixture 是 10 个 /loʊf/（全可 cover → 10/13 ≈ 0.77，verdict 为 partial，自相矛盾）；已修正为真正低于半数。
    words = [(f"w{i}", "/baɪ/") for i in range(10)] + [("loaf", "/loʊf/"), ("bread", "/bred/"), ("order", "/ˈɔːrdər/")]
    mapping = {"l": "l", "oʊ": "o", "f": "f"}
    english = frozenset("l o f".split())
    report = coverage_report(words, mapping, english)
    # coverage_report 的 ratio 精确到 4 位小数，故与 round(1/13, 4) 比对
    assert report["ratio"] == round(1 / 13, 4)
    assert report["verdict"] == "defer"
    assert "aɪ" in report["unmapped_symbols"]
