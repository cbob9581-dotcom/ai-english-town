export class AudioQueue {
  private chunks: { id: string; buffer: ArrayBuffer }[] = [];

  enqueue(id: string, buffer: ArrayBuffer): void { this.chunks.push({ id, buffer }); }

  next(): { id: string; buffer: ArrayBuffer } | null { return this.chunks.shift() ?? null; }

  clear(): number { const n = this.chunks.length; this.chunks = []; return n; }

  size(): number { return this.chunks.length; }
}
