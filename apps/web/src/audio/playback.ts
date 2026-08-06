/** 阶段 1 播放端：把 AudioQueue 里攒下的整段 WAV（16kHz mono PCM16，RIFF 头）解码并播放。
 * 真实出声依赖 Web Audio；jsdom 无 AudioContext 时静默 no-op（真实播放由 E2E 覆盖）。 */
export interface PlaybackHandle {
  stop(): void;
}

export interface WavPlayer {
  play(buffer: ArrayBuffer): PlaybackHandle | null;
}

export function createWavPlayer(): WavPlayer {
  let ctx: AudioContext | null = null;
  let active: PlaybackHandle | null = null;

  const ensureContext = (): AudioContext | null => {
    if (typeof AudioContext === 'undefined') return null;
    if (!ctx) {
      try {
        ctx = new AudioContext();
        void ctx.resume().catch(() => {});
      } catch {
        return null;
      }
    }
    return ctx;
  };

  return {
    play(buffer) {
      if (!buffer || buffer.byteLength === 0) return null; // TTS 可能返回空，跳过不播
      const ac = ensureContext();
      if (!ac) return null;
      active?.stop(); // 打断上一段在播音频

      let cancelled = false;
      let source: AudioBufferSourceNode | null = null;

      const handle: PlaybackHandle = {
        stop() {
          cancelled = true;
          source?.stop();
          if (active === handle) active = null;
        },
      };
      active = handle;

      ac.decodeAudioData(buffer)
        .then((audioBuffer) => {
          if (cancelled) return;
          source = ac.createBufferSource();
          source.buffer = audioBuffer;
          source.connect(ac.destination);
          source.onended = () => {
            if (active === handle) active = null;
          };
          source.start();
        })
        .catch(() => {
          /* 解码失败：静默忽略，保持 idle */
        });

      return handle;
    },
  };
}
