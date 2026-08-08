from app.llm.gesture import derive_gesture, validate_gesture

WORD_TO_ENTITY = {"word_loaf_n_1": "counter.main-1"}


def test_point_derived_when_scene_word_mentioned() -> None:
    g = derive_gesture("This loaf is very fresh!", WORD_TO_ENTITY)
    assert g == {"type": "point", "entityId": "counter.main-1"}


def test_wave_on_greeting() -> None:
    assert derive_gesture("Hello! Welcome!", WORD_TO_ENTITY) == {"type": "wave"}


def test_nod_and_shake() -> None:
    assert derive_gesture("Yes, of course", WORD_TO_ENTITY) == {"type": "nod"}
    assert derive_gesture("Sorry, I cannot do that", WORD_TO_ENTITY) == {"type": "shake"}


def test_no_gesture_for_plain_sentence() -> None:
    assert derive_gesture("The weather is nice today", WORD_TO_ENTITY) is None


def test_validate_rejects_unknown_type() -> None:
    assert validate_gesture({"type": "dance"}, {"counter.main-1"}) is None


def test_validate_rejects_point_to_missing_entity() -> None:
    assert validate_gesture({"type": "point", "entityId": "ghost"}, {"counter.main-1"}) is None


def test_validate_rejects_extra_entity_on_emotion() -> None:
    assert validate_gesture({"type": "wave", "entityId": "counter.main-1"}, {"counter.main-1"}) is None


def test_validate_passes_valid() -> None:
    assert validate_gesture({"type": "point", "entityId": "counter.main-1"}, {"counter.main-1"}) == {"type": "point", "entityId": "counter.main-1"}
    assert validate_gesture({"type": "nod"}, {"counter.main-1"}) == {"type": "nod"}
