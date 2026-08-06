from app.llm.chunker import SentenceChunker


def test_emits_complete_sentences() -> None:
    c = SentenceChunker()
    assert c.feed("Hello! ") == ["Hello!"]
    assert c.feed("Welcome to the bakery. Can I help you?") == ["Welcome to the bakery.", "Can I help you?"]


def test_fragment_waits_for_punctuation() -> None:
    c = SentenceChunker()
    assert c.feed("The loaf is ") == []
    assert c.feed("three dollars.") == ["The loaf is three dollars."]


def test_short_sentences_emit_immediately() -> None:
    c = SentenceChunker()
    assert c.feed("Hi!") == ["Hi!"]


def test_force_split_at_twenty_words() -> None:
    c = SentenceChunker()
    words = [f"w{i}" for i in range(25)]
    out = c.feed(" ".join(words) + " ")
    assert len(out) == 1
    assert out[0].split() == words[:20]
    assert c.finalize() == " ".join(words[20:])


def test_finalize_returns_remainder() -> None:
    c = SentenceChunker()
    assert c.feed("no punctuation yet") == []
    assert c.finalize() == "no punctuation yet"


def test_decimal_dot_is_not_boundary() -> None:
    c = SentenceChunker()
    assert c.feed("The price is 3.5 dollars.") == ["The price is 3.5 dollars."]


def test_no_double_space_drift() -> None:
    c = SentenceChunker()
    assert c.feed("Hi there. ") == ["Hi there."]
    assert c.finalize() == ""
