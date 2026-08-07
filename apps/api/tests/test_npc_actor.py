import pytest

from app.llm.mock import MockAdapter
from app.llm.npc_actor import NpcActor
from app.settings import Settings
from tests.fakes import FakeLlmLog

ALLOWED = {"word_loaf_n_1": "loaf", "word_apple_n_1": "apple"}


def fallback(user_text: str) -> str:
    return "Sorry, I didn't catch that. Could you say it again?"


def make_actor(scenario: str = "ok", stream_text_override: str | None = None, **overrides):
    settings = Settings(llm_total_timeout_npc_s=0.2, **overrides)
    log = FakeLlmLog()
    actor = NpcActor(MockAdapter(scenario, stream_text_override=stream_text_override),
                     settings, log, ALLOWED, fallback)
    return actor, log


KW = dict(session_id="s1", generation_id="g1", turn_id="t1", utterance_id="u1",
          user_text="hello", recent_turns=[])


async def test_happy_path_delta_commit_metadata() -> None:
    actor, log = make_actor(stream_text_override="Hello! A loaf is three dollars. Can I help you?")
    msgs = [m async for m in actor.stream_reply(**KW)]
    types = [m["type"] for m in msgs]
    assert types == ["npc.speech.delta", "npc.speech.delta", "npc.speech.delta",
                     "npc.speech.commit", "npc.turn.metadata"]
    deltas = [m["text"] for m in msgs if m["type"] == "npc.speech.delta"]
    assert deltas == ["Hello!", "A loaf is three dollars.", "Can I help you?"]
    commit = [m for m in msgs if m["type"] == "npc.speech.commit"][0]
    assert commit["text"] == "Hello! A loaf is three dollars. Can I help you?"
    meta = [m for m in msgs if m["type"] == "npc.turn.metadata"][0]
    assert meta["candidateWordIds"] == ["word_loaf_n_1"]
    assert meta["generationId"] == "g1" and meta["turnId"] == "t1"
    # happy path 记 ok=1
    assert log.rows[-1]["ok"] is True and log.rows[-1]["fallback_reason"] == "none"


async def test_timeout_degrades() -> None:
    actor, log = make_actor("timeout")
    msgs = [m async for m in actor.stream_reply(**KW)]
    assert [m["type"] for m in msgs] == ["npc.speech.delta", "npc.speech.commit", "npc.turn.metadata"]
    assert msgs[0]["text"] == fallback("hello")
    assert log.rows[-1]["fallback_reason"] == "timeout"
    assert log.rows[-1]["ok"] is False


async def test_connect_error_degrades() -> None:
    actor, log = make_actor("connect_error")
    msgs = [m async for m in actor.stream_reply(**KW)]
    assert msgs[0]["text"] == fallback("hello")
    assert log.rows[-1]["fallback_reason"] == "connect"
    assert log.rows[-1]["attempt"] == 2  # 连接错误内部已重试一次


async def test_api_status_error_degrades() -> None:
    # 4xx openai.APIStatusError（401/403/429/422…）不重试原样上抛 → 按 connect 类降级 scripted
    actor, log = make_actor("status_error")
    msgs = [m async for m in actor.stream_reply(**KW)]
    assert [m["type"] for m in msgs] == ["npc.speech.delta", "npc.speech.commit", "npc.turn.metadata"]
    commit = [m for m in msgs if m["type"] == "npc.speech.commit"][0]
    assert commit["text"] == fallback("hello")
    assert log.rows[-1]["fallback_reason"] == "connect"
    assert log.rows[-1]["ok"] is False


async def test_too_long_degrades_not_truncated() -> None:
    actor, log = make_actor("too_long")
    msgs = [m async for m in actor.stream_reply(**KW)]
    assert msgs[0]["text"] == fallback("hello")
    assert log.rows[-1]["fallback_reason"] == "schema_reject"


async def test_length_truncated_degrades() -> None:
    actor, log = make_actor("truncated")
    msgs = [m async for m in actor.stream_reply(**KW)]
    # 截断发生在流中途：已发出的真实 delta 会先到，最终 commit 覆盖为兜底文本
    commit = [m for m in msgs if m["type"] == "npc.speech.commit"][0]
    assert commit["text"] == fallback("hello")
    assert log.rows[-1]["fallback_reason"] == "length_truncated"


async def test_empty_output_degrades() -> None:
    actor, log = make_actor("empty")
    msgs = [m async for m in actor.stream_reply(**KW)]
    assert msgs[0]["text"] == fallback("hello")
    assert log.rows[-1]["fallback_reason"] == "schema_reject"


async def test_budget_exceeded_degrades() -> None:
    actor, log = make_actor()
    msgs = [m async for m in actor.stream_reply(**KW, budget_exceeded=True)]
    assert msgs[0]["text"] == fallback("hello")
    assert log.rows[-1]["fallback_reason"] == "budget"
    assert log.rows[-1]["ok"] is False


async def test_cumulative_length_degrades() -> None:
    # 累计超 max_speech_chars=200 才在流中途触发 → 需要一段 >200 字符的多句文本
    long_text = "Hello! " + "This is a long sentence with lots of words. " * 6
    actor, log = make_actor(stream_text_override=long_text, llm_max_speech_chars=200)
    msgs = [m async for m in actor.stream_reply(**KW)]
    # 中途超长 → 降级（commit 是兜底文本），不截断继续
    commit = [m for m in msgs if m["type"] == "npc.speech.commit"][0]
    assert commit["text"] == fallback("hello")
    assert log.rows[-1]["fallback_reason"] == "schema_reject"


async def test_remainder_over_cap_degrades() -> None:
    # finalize() 余量：完整句子合计略低于 cap（15 字符→emitted 16），trailing 无终止符片段
    # 单独过 validate_speech（18<30）但累计 34>30 → 整轮降级 schema_reject，不发超限 commit。
    text = "A loaf is here. please help me sir"
    actor, log = make_actor(stream_text_override=text, llm_max_speech_chars=30)
    msgs = [m async for m in actor.stream_reply(**KW)]
    commit = [m for m in msgs if m["type"] == "npc.speech.commit"][0]
    assert commit["text"] == fallback("hello")          # 无超限 commit 文本
    assert log.rows[-1]["fallback_reason"] == "schema_reject"
    assert log.rows[-1]["ok"] is False
    delta_texts = [m["text"] for m in msgs if m["type"] == "npc.speech.delta"]
    assert "please help me sir" not in delta_texts       # 超限片段绝不被发出
    assert delta_texts[-1] == fallback("hello")
