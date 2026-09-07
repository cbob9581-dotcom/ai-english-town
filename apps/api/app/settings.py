from dataclasses import dataclass
import os
from pathlib import Path

_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def _load_env_file() -> None:
    """极简 .env 加载（无第三方依赖）：逐行 KEY=VALUE；已存在的环境变量优先。
    供 Settings.from_env() 使用，可用 apps/api/.env 固化本地配置（含密钥，不入库，
    见 .gitignore 的 .env 规则）。"""
    try:
        text = _ENV_FILE.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # 已有非空环境变量优先；仅在当前缺失或为空（含空串遮蔽）时用 .env 填充。
        if key and value and not os.environ.get(key):
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    db_path: Path = Path("english_town.db")
    asset_root: Path = Path(__file__).resolve().parents[3] / "assets"
    asr_ws_url: str = "ws://127.0.0.1:8001/ws/asr"   # 阶段 2 使用
    tts_url: str = "http://127.0.0.1:8002/tts"       # 阶段 2 使用

    # --- 阶段 2：LLM（provider 无关，OpenAI 兼容）---
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""                       # env DEEPSEEK_API_KEY；空 → mock
    llm_model: str = "deepseek-chat"
    llm_connect_timeout_s: float = 1.5
    llm_ttft_timeout_s: float = 2.0
    # NPC 回合总预算（墙钟，自 LLM 流开始计）。注意：逐句 TTS 合成发生在 stream_reply
    # 的 yield 之间，TTS 耗时也计入本预算。真实 LLM TTFT ~2s + 多句 TTS 后，3s（phase-2
    # mock 时代的数）必然超时 → 回合在 TTS 中被 asyncio.timeout 静默取消，用户听不到回复。
    # 实测完整回复（~200 字 + 7 段 TTS）约 8s，故默认 15.0 留足余量。
    llm_total_timeout_npc_s: float = 15.0
    llm_total_timeout_tutor_s: float = 6.0
    llm_max_speech_chars: int = 200
    llm_max_scaffold_chars: int = 120
    llm_temperature_npc: float = 0.8
    llm_temperature_tutor: float = 0.3
    llm_max_tokens_npc: int = 320
    llm_max_tokens_tutor: int = 320
    # Director 非流式 complete_json（真实 DeepSeek）：完整场景 JSON 实测 ~3-6s、可能更长，
    # read 超时与 asyncio 预算都得覆盖它（mock 时代 6.0s/400tok 会超时或截断 → 场景降级为骨架）。
    # read_timeout_s 由调用方把本预算传入；fill 在后台跑，骨架立即可见，15s 预算可接受。
    llm_total_timeout_director_s: float = 15.0
    llm_temperature_director: float = 0.2
    llm_max_tokens_director: int = 800
    scene_prefetch_ttl_s: float = 60.0
    scene_prefetch_budget_ratio: float = 0.8
    llm_session_call_cap: int = 200
    llm_concurrency_limit: int = 2
    tutor_cache_dir: Path = Path("data/tutor-audio")

    # --- 阶段 4：学习引擎 ---
    evidence_policy_version: str = "v1"
    fsrs_algorithm_version: str = "fsrs-5"
    score_alpha: float = 0.35
    fsrs_retention: float = 0.9
    fsrs_min_confidence: float = 0.6

    # --- 阶段 5：WorldMemory + 词级对齐评分 ---
    memory_policy_version: str = "v1"
    pronunciation_policy_version: str = "v1"
    enable_word_timestamps: bool = False          # 默认关；开则 asr-worker 输出 words
    word_timestamp_min_model: str = "whisper-large-v3"
    pronunciation_audio_consent: bool = False     # 默认关；开才落盘 WAV

    # --- 阶段 6：GOP 音素级发音评测 ---
    pronunciation_gop_enabled: bool = False          # 默认关
    pronunciation_gop_model: str = "facebook/wav2vec2-lv-60-espeak-cv-ft"
    pronunciation_gop_device: str = "cpu"            # 默认 CPU，规避 ct2/torch CUDA 冲突
    pronunciation_gop_min_word_ms: int = 120         # 词窗最短时长，过短不评
    pronunciation_gop_word_pad_ms: int = 100         # 词窗双侧 pad，补偿 whisper 时间戳误差
    pronunciation_gop_min_conf: float | None = None  # 阈值先 None；kokoro golden 分布量后再定

    _ENV_FIELDS = {
        "llm_base_url": "LLM_BASE_URL",
        "llm_api_key": "DEEPSEEK_API_KEY",
        "llm_model": "LLM_MODEL",
        "llm_connect_timeout_s": "LLM_CONNECT_TIMEOUT_S",
        "llm_ttft_timeout_s": "LLM_TTFT_TIMEOUT_S",
        "llm_total_timeout_npc_s": "LLM_TOTAL_TIMEOUT_NPC_S",
        "llm_total_timeout_tutor_s": "LLM_TOTAL_TIMEOUT_TUTOR_S",
        "llm_max_speech_chars": "LLM_MAX_SPEECH_CHARS",
        "llm_max_scaffold_chars": "LLM_MAX_SCAFFOLD_CHARS",
        "llm_temperature_npc": "LLM_TEMPERATURE_NPC",
        "llm_temperature_tutor": "LLM_TEMPERATURE_TUTOR",
        "llm_max_tokens_npc": "LLM_MAX_TOKENS_NPC",
        "llm_max_tokens_tutor": "LLM_MAX_TOKENS_TUTOR",
        "llm_total_timeout_director_s": "LLM_TOTAL_TIMEOUT_DIRECTOR_S",
        "llm_temperature_director": "LLM_TEMPERATURE_DIRECTOR",
        "llm_max_tokens_director": "LLM_MAX_TOKENS_DIRECTOR",
        "scene_prefetch_ttl_s": "SCENE_PREFETCH_TTL_S",
        "scene_prefetch_budget_ratio": "SCENE_PREFETCH_BUDGET_RATIO",
        "llm_session_call_cap": "LLM_SESSION_CALL_CAP",
        "llm_concurrency_limit": "LLM_CONCURRENCY_LIMIT",
        "tutor_cache_dir": "TUTOR_CACHE_DIR",
        "evidence_policy_version": "EVIDENCE_POLICY_VERSION",
        "fsrs_algorithm_version": "FSRS_ALGORITHM_VERSION",
        "score_alpha": "SCORE_ALPHA",
        "fsrs_retention": "FSRS_RETENTION",
        "fsrs_min_confidence": "FSRS_MIN_CONFIDENCE",
        "memory_policy_version": "MEMORY_POLICY_VERSION",
        "pronunciation_policy_version": "PRONUNCIATION_POLICY_VERSION",
        "enable_word_timestamps": "ENABLE_WORD_TIMESTAMPS",
        "word_timestamp_min_model": "WORD_TIMESTAMP_MIN_MODEL",
        "pronunciation_audio_consent": "PRONUNCIATION_AUDIO_CONSENT",
        "pronunciation_gop_enabled": "PRONUNCIATION_GOP_ENABLED",
        "pronunciation_gop_model": "PRONUNCIATION_GOP_MODEL",
        "pronunciation_gop_device": "PRONUNCIATION_GOP_DEVICE",
        "pronunciation_gop_min_word_ms": "PRONUNCIATION_GOP_MIN_WORD_MS",
        "pronunciation_gop_word_pad_ms": "PRONUNCIATION_GOP_WORD_PAD_MS",
        "pronunciation_gop_min_conf": "PRONUNCIATION_GOP_MIN_CONF",
    }

    @classmethod
    def from_env(cls) -> "Settings":
        _load_env_file()
        kw: dict = {}
        base = cls()
        for field, env_name in cls._ENV_FIELDS.items():
            if env_name in os.environ:
                raw = os.environ[env_name]
                default = getattr(base, field)
                if isinstance(default, bool):
                    kw[field] = raw.lower() == "true"
                elif isinstance(default, int):
                    kw[field] = int(raw)
                elif isinstance(default, float):
                    kw[field] = float(raw)
                elif field == "pronunciation_gop_min_conf":
                    kw[field] = float(raw) if raw not in ("", "none", "null") else None
                else:
                    kw[field] = Path(raw) if field == "tutor_cache_dir" else raw
        return cls(**kw)
