import pytest

from app.llm.proposals import ProposalError, validate_speech, validate_tutor


def test_speech_ok() -> None:
    assert validate_speech("The loaf is three dollars.", 200) == "The loaf is three dollars."


def test_speech_empty_rejected() -> None:
    with pytest.raises(ProposalError):
        validate_speech("", 200)


def test_speech_over_long_rejected_not_truncated() -> None:
    with pytest.raises(ProposalError):
        validate_speech("word " * 60, 200)


def test_speech_newline_rejected() -> None:
    with pytest.raises(ProposalError):
        validate_speech("line one\nline two", 200)


def test_speech_url_rejected() -> None:
    with pytest.raises(ProposalError):
        validate_speech("visit https://example.com now", 200)


def test_speech_code_block_rejected() -> None:
    with pytest.raises(ProposalError):
        validate_speech("```python\nx=1\n```", 200)


def test_speech_non_ascii_rejected() -> None:
    with pytest.raises(ProposalError):
        validate_speech("un café", 200)


def test_tutor_ok() -> None:
    validate_tutor("loaf", "A loaf is a big piece of bread. Say it: loaf.",
                   expected_word="loaf", max_scaffold_chars=120)


def test_tutor_wrong_word_rejected() -> None:
    with pytest.raises(ProposalError):
        validate_tutor("apple", "A loaf is bread.", expected_word="loaf", max_scaffold_chars=120)


def test_tutor_scaffold_missing_target_rejected() -> None:
    with pytest.raises(ProposalError):
        validate_tutor("loaf", "A big piece of bread you can buy.",
                       expected_word="loaf", max_scaffold_chars=120)


def test_tutor_scaffold_over_long_rejected() -> None:
    with pytest.raises(ProposalError):
        validate_tutor("loaf", "word " * 30, expected_word="loaf", max_scaffold_chars=120)


def test_tutor_case_insensitive_word() -> None:
    validate_tutor("Loaf", "A loaf is bread.", expected_word="loaf", max_scaffold_chars=120)
