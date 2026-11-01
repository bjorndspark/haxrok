/**
 * Gapless playback for voice-mode TTS clips arriving over the WebSocket.
 *
 * pcm clips are raw s16le samples streamed into an AudioWorklet as they
 * arrive — playback starts on the first chunk, no decode, no fetch. mp3
 * clips (gateway fallback) are collected per clip, decodeAudioData'd at
 * clip end, then scheduled back-to-back on the AudioContext timeline so
 * consecutive clips have no gap. Both beat an <audio> element per clip,
 * which pays element setup + buffering on every sentence.
 */

// FIFO of Float32 chunks; process() drains at the context rate. "clear"
// empties the queue (barge-in / stop).
const WORKLET_SOURCE = `
class VoicePcmQueue extends AudioWorkletProcessor {
  constructor() {
    super()
    this.chunks = []
    this.offset = 0
    this.port.onmessage = (e) => {
      if (e.data === "clear") {
        this.chunks = []
        this.offset = 0
      } else {
        this.chunks.push(e.data)
      }
    }
  }
  process(_inputs, outputs) {
    const out = outputs[0][0]
    let i = 0
    while (i < out.length && this.chunks.length) {
      const head = this.chunks[0]
      const n = Math.min(out.length - i, head.length - this.offset)
      out.set(head.subarray(this.offset, this.offset + n), i)
      i += n
      this.offset += n
      if (this.offset >= head.length) {
        this.chunks.shift()
        this.offset = 0
      }
    }
    if (i < out.length) out.fill(0, i)
    return true
  }
}
registerProcessor("voice-pcm-queue", VoicePcmQueue)
`

export interface ClipMeta {
  seq: number
  format: "pcm" | "mp3"
  sample_rate?: number
}

export class VoicePlayer {
  private ctx: AudioContext
  private node: AudioWorkletNode
  private gain: GainNode
  private clipFormat: "pcm" | "mp3" = "pcm"
  private pcmRate = 24000
  private mp3Parts: ArrayBuffer[] = []
  private sources = new Set<AudioBufferSourceNode>()
  private nextStart = 0
  private drainTimer: ReturnType<typeof setTimeout> | undefined
  // Set by stop(), cleared by unmute() — the session calls unmute when a
  // NEW turn's transcript arrives (a transcript always precedes that
  // turn's clips). beginClip must never clear this: a cancelled turn's
  // audio_begin stragglers would resurrect playback mid-next-turn.
  private muted = false
  /** AudioContext time at which queued pcm finishes. Playback drains in
   *  realtime whenever the worklet queue is non-empty, so tracking an end
   *  time (not "seconds pushed minus time since last push") stays correct
   *  while chunks stream in during playback. */
  private pcmEndAt = 0

  private constructor(ctx: AudioContext, node: AudioWorkletNode, gain: GainNode) {
    this.ctx = ctx
    this.node = node
    this.gain = gain
  }

  static async create(): Promise<VoicePlayer> {
    const ctx = new AudioContext()
    await ctx.resume().catch(() => {})
    const url = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: "application/javascript" }))
    try {
      await ctx.audioWorklet.addModule(url)
    } finally {
      URL.revokeObjectURL(url)
    }
    const node = new AudioWorkletNode(ctx, "voice-pcm-queue")
    // One gain stage for both paths (worklet + mp3 sources) so talk-over
    // can duck the reply smoothly instead of hard-cutting mid-word.
    const gain = ctx.createGain()
    node.connect(gain).connect(ctx.destination)
    return new VoicePlayer(ctx, node, gain)
  }

  beginClip(meta: ClipMeta): void {
    this.clipFormat = meta.format
    if (meta.format === "pcm" && meta.sample_rate) this.pcmRate = meta.sample_rate
    if (meta.format === "mp3") this.mp3Parts = []
  }

  /** One binary WS frame: pcm samples stream straight to the worklet;
   *  mp3 bytes accumulate until endClip decodes the whole clip. */
  push(chunk: ArrayBuffer): void {
    if (this.muted) return
    if (this.clipFormat === "pcm") {
      const f32 = s16ToF32(chunk, this.pcmRate, this.ctx.sampleRate)
      this.pcmEndAt =
        Math.max(this.ctx.currentTime, this.pcmEndAt) + f32.length / this.ctx.sampleRate
      this.node.port.postMessage(f32, [f32.buffer])
    } else {
      this.mp3Parts.push(chunk)
    }
  }

  async endClip(): Promise<void> {
    if (this.muted || this.clipFormat !== "mp3" || this.mp3Parts.length === 0) return
    const whole = concatBuffers(this.mp3Parts)
    this.mp3Parts = []
    const buf = await this.ctx.decodeAudioData(whole).catch(() => null)
    // Decode takes real time — an interrupt can land mid-await. Re-check
    // or a dead turn's clip schedules over the next reply.
    if (!buf || this.muted) return
    const src = this.ctx.createBufferSource()
    src.buffer = buf
    src.connect(this.gain)
    // Schedule at the previous clip's end — gapless without guessing.
    const t = Math.max(this.ctx.currentTime, this.nextStart)
    src.start(t)
    this.nextStart = t + buf.duration
    this.sources.add(src)
    src.onended = () => this.sources.delete(src)
  }

  /** Seconds of queued audio still to play (rough — drives the UI's
   *  "speaking" state and when the VAD may resume). */
  remainingSec(): number {
    const pcm = Math.max(0, this.pcmEndAt - this.ctx.currentTime)
    const mp3 = Math.max(0, this.nextStart - this.ctx.currentTime)
    return pcm + mp3
  }

  /** Poll until playback drains, then fire cb once. A closed context
   *  never drains — count it as drained rather than polling forever. A
   *  suspended one (talk-over pause) keeps polling: it will be resumed. */
  whenDrained(cb: () => void): void {
    const tick = () => {
      if (this.ctx.state === "closed" || this.remainingSec() <= 0.05) cb()
      else setTimeout(tick, 100)
    }
    tick()
  }

  /** Reopen the audio path for a new turn — called by the session on
   *  `transcript`, the first event of every turn, which always precedes
   *  that turn's clips. */
  unmute(): void {
    this.muted = false
  }

  /** Talk-over: fade the reply down while the user speaks — Alexa-style
   *  ducking rather than a hard cut. `level` is the target gain. */
  duck(level = 0.15): void {
    this.gain.gain.setTargetAtTime(level, this.ctx.currentTime, 0.05)
  }

  /** Back to full volume — a false barge-in, or a reply that ended or
   *  errored while ducked. Ramped so the tail doesn't jump. */
  unduck(): void {
    this.gain.gain.setTargetAtTime(1, this.ctx.currentTime, 0.1)
  }

  /** Barge-in: mute stragglers but let queued audio murmur out at duck
   *  level — Alexa-style drain instead of a hard cut. Bounded because
   *  scheduled mp3 clips can run far ahead of the clock. */
  softStop(drainMs = 1500): void {
    this.muted = true
    this.drainTimer = setTimeout(() => this.stop(), drainMs)
  }

  /** Barge-in / teardown: drop queued pcm and scheduled mp3 clips. */
  stop(): void {
    clearTimeout(this.drainTimer)
    this.muted = true
    // The duck's setTargetAtTime ramp started in the past, so
    // cancelScheduledValues won't touch it — it keeps converging to the
    // duck level and the NEXT reply comes out quiet. cancelAndHoldAtTime
    // kills the in-progress ramp; setValueAtTime pins full volume.
    this.gain.gain.cancelAndHoldAtTime(this.ctx.currentTime)
    this.gain.gain.setValueAtTime(1, this.ctx.currentTime)
    void this.ctx.resume().catch(() => {})
    this.node.port.postMessage("clear")
    for (const s of this.sources) s.stop()
    this.sources.clear()
    this.mp3Parts = []
    this.pcmEndAt = 0
    this.nextStart = 0
  }

  async close(): Promise<void> {
    this.stop()
    this.node.disconnect()
    await this.ctx.close().catch(() => {})
  }
}

function s16ToF32(buf: ArrayBuffer, fromRate: number, toRate: number): Float32Array {
  const i16 = new Int16Array(buf)
  if (fromRate === toRate) {
    const out = new Float32Array(i16.length)
    for (let i = 0; i < i16.length; i++) out[i] = i16[i] / 0x8000
    return out
  }
  // Linear-resample to the context rate (e.g. 24kHz → 48kHz).
  const outLen = Math.floor((i16.length * toRate) / fromRate)
  const out = new Float32Array(outLen)
  const ratio = fromRate / toRate
  for (let i = 0; i < outLen; i++) {
    const src = i * ratio
    const i0 = Math.floor(src)
    const s0 = i16[i0] / 0x8000
    const s1 = i16[Math.min(i0 + 1, i16.length - 1)] / 0x8000
    out[i] = s0 + (s1 - s0) * (src - i0)
  }
  return out
}

function concatBuffers(parts: ArrayBuffer[]): ArrayBuffer {
  const total = parts.reduce((n, p) => n + p.byteLength, 0)
  const out = new Uint8Array(total)
  let off = 0
  for (const p of parts) {
    out.set(new Uint8Array(p), off)
    off += p.byteLength
  }
  return out.buffer
}
