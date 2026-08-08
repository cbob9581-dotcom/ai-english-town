from app.llm.concepts import resolve_word_id


def test_resolve_word_id_without_learning_items() -> None:
    assert resolve_word_id("loaf", "n") == "word_loaf_n_1"
    assert resolve_word_id("run", "v", sense=2) == "word_run_v_2"


import app.llm.concepts as c


def test_resolver_cache_and_invalidate() -> None:
    c.configure_word_resolver(lambda lemma, pos, sense=1:
                              f"from_store_{lemma}" if lemma == "loaf" else None)
    c.invalidate_word_id_cache()                                    # 清掉前序测试的缓存（同 test_earliest_created_conflict）
    assert c.resolve_word_id("loaf", "n") == "from_store_loaf"     # lookup 命中
    assert c.resolve_word_id("run", "v") == "word_run_v_1"         # lookup miss → 回退
    # 缓存命中：lookup 改为返回别的也不会变
    c.configure_word_resolver(lambda lemma, pos, sense=1: "changed")
    assert c.resolve_word_id("loaf", "n") == "from_store_loaf"
    c.invalidate_word_id_cache()
    assert c.resolve_word_id("loaf", "n") == "changed"             # 失效后重新查


def test_earliest_created_conflict() -> None:
    # 同一 lemma+pos 多 sense → 取 created_at 最早
    lookup = lambda lemma, pos, sense=1: None   # 不参与；冲突规则在 store 层，这里测缓存 key 分离
    c.configure_word_resolver(lookup)
    c.invalidate_word_id_cache()
    assert c.resolve_word_id("loaf", "n") == "word_loaf_n_1"
    assert c.resolve_word_id("loaf", "n", sense=2) == "word_loaf_n_2"  # sense 独立 key
