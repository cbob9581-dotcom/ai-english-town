from app.llm.lexmatch import derive_candidate_word_ids, token_contains

ALLOWED = {"word_loaf_n_1": "loaf", "word_apple_n_1": "apple", "word_receipt_n_1": "receipt"}


def test_matches_lemma() -> None:
    assert derive_candidate_word_ids("I'd like a loaf, please.", ALLOWED) == ["word_loaf_n_1"]


def test_matches_plural_irregular() -> None:
    assert derive_candidate_word_ids("Two loaves, please.", ALLOWED) == ["word_loaf_n_1"]


def test_matches_multiple_in_scene_order() -> None:
    assert derive_candidate_word_ids("An apple and a receipt.", ALLOWED) == ["word_apple_n_1", "word_receipt_n_1"]


def test_no_match_returns_empty() -> None:
    assert derive_candidate_word_ids("Goodbye!", ALLOWED) == []


def test_case_and_punctuation_insensitive() -> None:
    assert derive_candidate_word_ids("LOAF!", ALLOWED) == ["word_loaf_n_1"]


def test_dedupe_per_word() -> None:
    assert derive_candidate_word_ids("loaf and loaves", ALLOWED) == ["word_loaf_n_1"]


def test_token_contains_helper() -> None:
    assert token_contains("The loaves are fresh.", "loaf") is True
    assert token_contains("The receipt is here.", "loaf") is False
    assert token_contains("An apple!", "apple") is True
