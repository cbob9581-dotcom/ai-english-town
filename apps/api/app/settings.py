from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    db_path: Path = Path("english_town.db")
    asset_root: Path = Path(__file__).resolve().parents[3] / "assets"
    asr_ws_url: str = "ws://127.0.0.1:8001/ws/asr"   # 阶段 2 使用
    tts_url: str = "http://127.0.0.1:8002/tts"       # 阶段 2 使用

    # --- 阶段 2：LLM（provider 无关，OpenAI 兼容）---
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""                            # env DEEPSEEK_API_KEY；空 → mock
    llm_model: str = "deepseek-chat"
    llm_connect_timeout_s: float = 1.5
    llm_ttft_timeout_s: float = 2.0
    llm_total_timeout_npc_s: float = 3.0
    llm_total_timeout_tutor_s: float = 6.0
    llm_max_speech_chars: int = 200
    llm_max_scaffold_chars: int = 120
    llm_temperature_npc: float = 0.8
    llm_temperature_tutor: float = 0.3
    llm_max_tokens_npc: int = 320
    llm_max_tokens_tutor: int = 320
    llm_total_timeout_director_s: float = 6.0
    llm_temperature_director: float = 0.2
    llm_max_tokens_director: int = 400
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
