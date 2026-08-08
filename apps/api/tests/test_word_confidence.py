from app.learning.word_confidence import score_word_confidence


def test_hit_returns_word_probability():
    scene = {"word_loaf_n_1": "loaf", "word_jar_n_1": "jar"}
    words = [{"word": "loaf", "start": 0.4, "end": 0.9, "probability": 0.95}]
    out = score_word_confidence(words, scene, "I want a loaf")
    assert abs(out["word_loaf_n_1"] - 0.95) < 1e-6
    assert "word_jar_n_1" not in out          # 用户没说 jar → 无词级信号


def test_misrecognized_low_score():
    scene = {"word_loaf_n_1": "loaf"}
    words = [{"word": "roof", "start": 0.4, "end": 0.9, "probability": 0.9}]
    out = score_word_confidence(words, scene, "I want a loaf")   # 说了 loaf，ASR 听成 roof
    assert out["word_loaf_n_1"] == 0.15


def test_word_not_in_user_text_no_evidence():
    scene = {"word_loaf_n_1": "loaf", "word_jar_n_1": "jar"}
    words = [{"word": "loaf", "start": 0.4, "end": 0.9, "probability": 0.9}]
    out = score_word_confidence(words, scene, "show me the jar")
    assert "word_loaf_n_1" not in out           # loaf 未在 user_text → 无词级信号
    assert out["word_jar_n_1"] == 0.15          # jar 在 user_text 但 words 未检出 → 0.15（说但误识）


def test_no_words_returns_empty():
    scene = {"word_loaf_n_1": "loaf"}
    assert score_word_confidence([], scene, "I want a loaf") == {}
    assert score_word_confidence(None, scene, "I want a loaf") == {}
