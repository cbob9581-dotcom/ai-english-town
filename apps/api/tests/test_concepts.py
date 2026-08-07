from app.llm.concepts import resolve_word_id


def test_resolve_word_id_without_learning_items() -> None:
    assert resolve_word_id("loaf", "n") == "word_loaf_n_1"
    assert resolve_word_id("run", "v", sense=2) == "word_run_v_2"
