def resolve_word_id(lemma: str, pos: str, *, sense: int = 1) -> str:
    """conceptId → wordId 的服务端 resolve 接缝。
    阶段 4 接 learning_items（命中则用其 id），当前确定性派生。"""
    return f"word_{lemma}_{pos}_{sense}"
