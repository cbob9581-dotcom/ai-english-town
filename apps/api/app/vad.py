"""服务端 VAD 决策。真实 Silero 每帧产出 start/end 标签喂给本状态机。
阶段 1：状态机先独立测试；Silero 模型接入在阶段 2（见计划"偏离说明"）。"""
from __future__ import annotations


class VadStateMachine:
    def __init__(self, min_silence_ms: int = 550, max_speech_ms: int = 20000, frame_ms: int = 50) -> None:
        self.min_silence_ms = min_silence_ms
        self.max_speech_ms = max_speech_ms
        self.frame_ms = frame_ms
        self.in_speech = False
        self.silence_ms = 0
        self.speech_ms = 0

    def consume(self, start: bool, end: bool) -> str:
        if not self.in_speech and start:
            self.in_speech = True
            self.speech_ms = 0
            self.silence_ms = 0
            return "speech_start"
        if self.in_speech:
            self.speech_ms += self.frame_ms
            if end:
                self.in_speech = False
                return "speech_end"
            self.silence_ms += self.frame_ms
            if self.silence_ms >= self.min_silence_ms:
                self.in_speech = False
                return "speech_end"
            if self.speech_ms >= self.max_speech_ms:
                self.in_speech = False
                return "speech_end"
        return "none"
