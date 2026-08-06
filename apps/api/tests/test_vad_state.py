from app.vad import VadStateMachine


def test_speech_start_and_end() -> None:
    vad = VadStateMachine()
    assert vad.consume(start=False, end=False) == "none"
    assert vad.consume(start=True, end=False) == "speech_start"
    assert vad.consume(start=False, end=True) == "speech_end"


def test_silence_padding() -> None:
    vad = VadStateMachine()
    vad.consume(start=True, end=False)
    # 连续无语音 550ms（11 帧×50ms）才判 end
    for _ in range(10):
        assert vad.consume(start=False, end=False) == "none"
    assert vad.consume(start=False, end=False) == "speech_end"
