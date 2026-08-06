export function encodePcm16(samples: Float32Array): ArrayBuffer {
  const buf = new ArrayBuffer(samples.length * 2);
  const view = new DataView(buf);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buf;
}

export function createAudioFrameChunks(samples: Float32Array, frameSamples = 320): Float32Array[] {
  const chunks: Float32Array[] = [];
  for (let i = 0; i < samples.length; i += frameSamples) {
    chunks.push(samples.subarray(i, Math.min(i + frameSamples, samples.length)));
  }
  return chunks;
}
