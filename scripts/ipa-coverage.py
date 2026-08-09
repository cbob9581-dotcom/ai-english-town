"""GOP 前置门：dictionary.json 全部目标词 IPA × wav2vec2 模型 alphabet → 覆盖率裁决。
<80% → A partial（缺映射词回退代理）；<50% → A defer（phase-6 只交 B+C，A 转 phase-7）。
用法:
  uv run --project services/asr-worker python scripts/ipa-coverage.py --mock          # 单测/演示（内置 mock 映射）
  uv run --project services/asr-worker python scripts/ipa-coverage.py --vocab x.json  # 用已有 dump（确定性）
  uv run --project services/asr-worker python scripts/ipa-coverage.py                  # 真机 dump 模型 alphabet
产物：把 IPA_TO_ESPEAK / ENGLISH_ESPEAK_SYMBOLS 回填进 asr_worker/ipa.py，并写 assets/wordbook/ipa-symbols.json。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 导入引导：Step 1 的 `uv sync`（把 asr_worker 装为 editable）由协调者网络门禁后执行，
# 本机尚未装。直接插入 asr-worker 包目录，使 `python scripts/ipa-coverage.py --mock`
# （单测 subprocess 路径，sys.path[0] 是 scripts/ 而非包目录）与
# `uv run --project services/asr-worker python scripts/ipa-coverage.py` 都能导入。
# sync 完成后该路径无害（asr_worker 已装时优先，重复路径无影响）。
_ASR_WORKER_PKG = ROOT / "services" / "asr-worker"
if _ASR_WORKER_PKG.is_dir() and str(_ASR_WORKER_PKG) not in sys.path:
    sys.path.insert(0, str(_ASR_WORKER_PKG))

from asr_worker.ipa import DICT_IPA_SYMBOLS, ENGLISH_ESPEAK_SYMBOLS, IPA_TO_ESPEAK, coverage_report

MODEL_ID = "facebook/wav2vec2-lv-60-espeak-cv-ft"

# 参照映射（espeak-ng 音素表惯例；仅当 dump 显示 espeak ASCII 符号集时启用。
# 若 dump 显示 Unicode IPA，则多数词典符号与模型符号同形，identity 映射即够）。
# 词典符号 → espeak ASCII（待真机核对，不臆造最终值）
_ESPEAK_ASCII_REFERENCE = {
    "iː": "i:", "ɪ": "I", "e": "e", "ɛ": "E", "æ": "a", "ɑː": "A:", "ɒ": "Q", "ɔː": "O:",
    "ʊ": "U", "uː": "u:", "ʌ": "V", "ɜː": "3:", "ə": "@", "eɪ": "eI", "aɪ": "aI",
    "ɔɪ": "OI", "aʊ": "aU", "oʊ": "@U",
    "tʃ": "tS", "dʒ": "dZ", "θ": "T", "ð": "D", "ʃ": "S", "ʒ": "Z", "ŋ": "N",
    "p": "p", "b": "b", "t": "t", "d": "d", "k": "k", "ɡ": "g", "f": "f", "v": "v",
    "s": "s", "z": "z", "h": "h", "m": "m", "n": "n", "l": "l", "r": "r", "j": "j", "w": "w",
}


def load_dictionary_words(asset_root: Path) -> list[tuple[str, str | None]]:
    raw = json.loads((asset_root / "wordbook" / "dictionary.json").read_text(encoding="utf-8"))
    return [(w["lemma"], w.get("ipa")) for w in raw["words"]]


def build_mapping_from_vocab(vocab: dict) -> tuple[dict[str, str], set[str]]:
    """第一遍：词典符号与模型词表同形 → 直接映射；不同形 → 依 _ESPEAK_ASCII_REFERENCE 尝试；
    仍未解 → 记 unmapped（人工补）。返回 (mapping, english_symbols)。"""
    mapping: dict[str, str] = {}
    for sym in DICT_IPA_SYMBOLS:
        if sym in vocab:
            mapping[sym] = sym
        elif _ESPEAK_ASCII_REFERENCE.get(sym) in vocab:
            mapping[sym] = _ESPEAK_ASCII_REFERENCE[sym]
    english = {mapping[s] for s in mapping} | set(vocab)
    # 剔除特殊 token（blank/word 分隔）出英语子集：vocab 中纯音素符号（无 < > [ ] 包裹）
    english = {s for s in english if not (s.startswith("<") or s.startswith("["))}
    return mapping, english


def dump_vocab(model_id: str) -> dict:
    """dump 模型词表 → {symbol: id}。模型未下载/加载失败 → 抛异常（调用方裁决 model_unavailable）。"""
    from transformers import Wav2Vec2Processor
    processor = Wav2Vec2Processor.from_pretrained(model_id)
    return dict(processor.tokenizer.get_vocab())


def backfill(mapping: dict[str, str], english: frozenset[str], asset_root: Path) -> None:
    """把映射/英语子集写回 asr_worker/ipa.py，并落 fixtures 供确定性测试复用。"""
    ipa_py = ROOT / "services" / "asr-worker" / "asr_worker" / "ipa.py"
    text = ipa_py.read_text(encoding="utf-8")
    import re
    mapping_literal = "{\n" + "".join(f"    {k!r}: {v!r},\n" for k, v in sorted(mapping.items())) + "}"
    english_literal = "frozenset(" + repr(sorted(english)) + ")"
    text = re.sub(r"IPA_TO_ESPEAK: dict\[str, str\] = \{\}",
                  f"IPA_TO_ESPEAK: dict[str, str] = {mapping_literal}", text)
    text = re.sub(r"ENGLISH_ESPEAK_SYMBOLS: frozenset\[str\] = frozenset\(\)",
                  f"ENGLISH_ESPEAK_SYMBOLS: frozenset[str] = {english_literal}", text)
    ipa_py.write_text(text, encoding="utf-8")
    (asset_root / "wordbook" / "ipa-symbols.json").write_text(
        json.dumps({"ipa_to_espeak": mapping, "english_symbols": sorted(english)},
                   ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vocab", help="已有 dump 的 vocab json（跳过模型加载，确定性）")
    ap.add_argument("--mock", action="store_true", help="内置 mock 词表跑覆盖率逻辑（单测）")
    ap.add_argument("--device", default="cpu")
    args = ap.parse_args(argv)
    words = load_dictionary_words(ROOT / "assets")
    if args.mock:
        # brief 原始 fixture 漏了 ə：order /ˈɔːrdər/ 分词含 ə → 未映射 → ratio 0.75 → partial，
        # 与测试断言 verdict=="ok" 冲突。按 Task 7 先例补齐（harmonize fixture，不改断言）。
        mapping = {"l": "l", "oʊ": "o", "f": "f", "b": "b", "r": "r", "e": "e",
                   "d": "d", "aɪ": "aI", "ɔː": "O:", "ə": "ə"}
        english = frozenset("l o f b r e d aI O: ə".split())
        report = coverage_report(words, mapping, english)
    elif args.vocab:
        vocab = json.loads(Path(args.vocab).read_text(encoding="utf-8"))
        mapping, english = build_mapping_from_vocab(vocab)
        report = coverage_report(words, mapping, english)
    else:
        try:
            vocab = dump_vocab(MODEL_ID)
        except Exception as e:  # noqa: BLE001 —— 模型未下载/加载失败 → 如实裁决
            print(json.dumps({"verdict": "model_unavailable", "error": str(e),
                              "total": len(words)}, ensure_ascii=False))
            return 2
        mapping, english = build_mapping_from_vocab(vocab)
        report = coverage_report(words, mapping, english)
        backfill(mapping, frozenset(english), ROOT / "assets")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["verdict"] != "defer" else 1


if __name__ == "__main__":
    raise SystemExit(main())
