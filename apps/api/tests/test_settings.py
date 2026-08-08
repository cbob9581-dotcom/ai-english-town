from app.settings import Settings


def test_phase5_settings_defaults():
    s = Settings()
    assert s.memory_policy_version == "v1"
    assert s.pronunciation_policy_version == "v1"
    assert s.enable_word_timestamps is False
    assert s.word_timestamp_min_model == "whisper-large-v3"
    assert s.pronunciation_audio_consent is False
