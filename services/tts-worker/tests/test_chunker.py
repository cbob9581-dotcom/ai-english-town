from tts_worker.chunker import chunk_sentences


def test_first_chunk_is_8_to_20_words() -> None:
    text = ("Hello and welcome to our bakery. We have fresh loaves every morning. "
            "Would you like a slice or the whole loaf? Take your time.")
    chunks = chunk_sentences(text)
    assert 8 <= len(chunks[0].split()) <= 20
    assert "".join(chunks).split() == text.split()  # 不丢词


def test_short_text_stays_one_chunk() -> None:
    assert chunk_sentences("Thanks!") == ["Thanks!"]


def test_sentence_boundaries_are_respected() -> None:
    text = "One. Two. Three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen."
    chunks = chunk_sentences(text)
    # 首块吸收后续句子补足字数，剩余按句切分
    assert all(chunk.strip().endswith(".") for chunk in chunks)


def test_empty_and_whitespace_text_returns_empty_list() -> None:
    assert chunk_sentences("") == []
    assert chunk_sentences("   ") == []


def test_multi_chunk_forces_recursive_branch() -> None:
    text = ("Hi. My name is Rosa. I run this bakery. "
            "We bake bread daily. You can ask me anything. Have a nice day.")
    chunks = chunk_sentences(text)
    assert len(chunks) > 1
    # 递归分支仍按句切分，不按词
    assert all(c.strip().endswith(".") for c in chunks)
    # 不丢词
    assert "".join(chunks).split() == text.split()
