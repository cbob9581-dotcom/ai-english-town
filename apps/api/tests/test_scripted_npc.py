from app.scripted_npc import reply


def test_greeting() -> None:
    r = reply("hello")
    assert "Welcome" in r["speech"]


def test_loaf_topic() -> None:
    r = reply("I want a loaf")
    assert "loaf" in r["speech"].lower()


def test_price_question() -> None:
    r = reply("how much is it?")
    assert "three" in r["speech"]


def test_fallback() -> None:
    r = reply("asdfghjkl")
    assert "Sorry" in r["speech"]
