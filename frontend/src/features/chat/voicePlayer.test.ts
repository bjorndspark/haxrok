// @vitest-environment node — VoicePlayer is exercised against a fake
// AudioContext/AudioWorkletNode; the real DSP is the browser's.
import { beforeEach, describe, expect, it, vi } from "vitest"

import { f32ToS16 } from "./useVoice"
import { VoicePlayer } from "./voicePlayer"

class FakeBufferSource {
  buffer: { duration: number } | null = null
  startedAt = 0
  stopped = false
  onended: (() => void) | null = null
  connect() {}
  start(t: number) {
    this.startedAt = t
  }
  stop() {
    this.stopped = true
  }
}

class FakeWorkletNode {
  posted: unknown[] = []
  port = { postMessage: (m: unknown) => this.posted.push(m) }
  connect(target: unknown) {
    return target // AudioNode.connect returns the destination for chaining
  }
  disconnect() {}
}

class FakeGainNode {
  ramped: number[] = []
  gain = {
    value: 1,
    setTargetAtTime: (v: number) => this.ramped.push(v),
    setValueAtTime: (v: number) => {
      this.gain.value = v
    },
    cancelAndHoldAtTime: () => {},
    cancelScheduledValues: () => {},
  }
  connect(target: unknown) {
    return target
  }
  disconnect() {}
}

class FakeAudioContext {
  sampleRate = 48000
  state = "running"
  currentTime = 0
  destination = {}
  audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) }
  sources: FakeBufferSource[] = []
  gains: FakeGainNode[] = []
  decoded: ArrayBuffer[] = []
  createBufferSource() {
    const s = new FakeBufferSource()
    this.sources.push(s)
    return s
  }
  createGain() {
    const g = new FakeGainNode()
    this.gains.push(g)
    return g
  }
  decodeAudioData(buf: ArrayBuffer) {
    this.decoded.push(buf)
    return Promise.resolve({ duration: 0.5 })
  }
  async resume() {}
  async close() {}
}

let ctx: FakeAudioContext

beforeEach(() => {
  ctx = new FakeAudioContext()
  // Constructor mocks: vitest invokes the implementation with `new`, which
  // an arrow function can't take — these must be `function` expressions.
  vi.stubGlobal(
    "AudioContext",
    vi.fn(function () {
      return ctx
    }),
  )
  vi.stubGlobal(
    "AudioWorkletNode",
    vi.fn(function () {
      return new FakeWorkletNode()
    }),
  )
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:fake")
  vi.spyOn(URL, "revokeObjectURL").mockReturnValue(undefined)
})

async function makePlayer() {
  const player = await VoicePlayer.create()
  const node = vi.mocked(AudioWorkletNode).mock.results[0].value as FakeWorkletNode
  return { player, node }
}

function pcm16(samples: number[]): ArrayBuffer {
  const buf = new ArrayBuffer(samples.length * 2)
  const view = new DataView(buf)
  samples.forEach((s, i) => view.setInt16(i * 2, s, true))
  return buf
}

describe("f32ToS16", () => {
  it("encodes little-endian s16 and clamps to [-1, 1]", () => {
    const buf = f32ToS16(new Float32Array([0, 0.5, -0.5, 1.5, -1.5]))
    const view = new DataView(buf)
    expect(view.getInt16(0, true)).toBe(0)
    expect(view.getInt16(2, true)).toBe(Math.trunc(0.5 * 0x7fff))
    expect(view.getInt16(4, true)).toBe(Math.trunc(-0.5 * 0x8000))
    expect(view.getInt16(6, true)).toBe(0x7fff) // clamped high
    expect(view.getInt16(8, true)).toBe(-0x8000) // clamped low
  })
})

describe("VoicePlayer pcm", () => {
  it("streams resampled frames straight to the worklet on push", async () => {
    const { player, node } = await makePlayer()
    player.beginClip({ seq: 0, format: "pcm", sample_rate: 24000 })

    // 240 samples @24kHz = 10ms in, resamples to 480 @48kHz on the port.
    player.push(pcm16(new Array<number>(240).fill(0)))

    expect(node.posted).toHaveLength(1)
    const frame = node.posted[0] as Float32Array
    expect(frame.length).toBe(480)
    expect(player.remainingSec()).toBeGreaterThan(0)
  })

  it("posts no worklet message for mp3 clips until endClip", async () => {
    const { player, node } = await makePlayer()
    player.beginClip({ seq: 0, format: "mp3" })
    player.push(pcm16([1, 2, 3]))
    expect(node.posted).toHaveLength(0)
  })
})

describe("VoicePlayer mp3", () => {
  it("decodes concatenated parts and schedules clips back-to-back", async () => {
    const { player } = await makePlayer()

    const part = new Uint8Array([1, 2, 3]).buffer
    player.beginClip({ seq: 0, format: "mp3" })
    player.push(part)
    player.push(part)
    await player.endClip()

    expect(ctx.decoded).toHaveLength(1)
    expect(ctx.decoded[0].byteLength).toBe(6)
    expect(ctx.sources[0].startedAt).toBe(0)

    // Second clip starts where the first ends (0.5s) — gapless.
    player.beginClip({ seq: 1, format: "mp3" })
    player.push(part)
    await player.endClip()
    expect(ctx.sources[1].startedAt).toBe(0.5)
  })

  it("start time never goes below currentTime", async () => {
    const { player } = await makePlayer()
    player.beginClip({ seq: 0, format: "mp3" })
    player.push(new Uint8Array([1]).buffer)
    await player.endClip()

    // Clock runs past the scheduled end; next clip starts now, not at 0.5.
    ctx.currentTime = 1.0
    player.beginClip({ seq: 1, format: "mp3" })
    player.push(new Uint8Array([1]).buffer)
    await player.endClip()
    expect(ctx.sources[1].startedAt).toBe(1.0)
  })
})

describe("VoicePlayer.stop", () => {
  it("clears the worklet queue and stops scheduled sources", async () => {
    const { player, node } = await makePlayer()
    player.beginClip({ seq: 0, format: "mp3" })
    player.push(new Uint8Array([1]).buffer)
    await player.endClip()

    player.stop()
    expect(node.posted).toContain("clear")
    expect(ctx.sources[0].stopped).toBe(true)
    expect(player.remainingSec()).toBe(0)
  })

  it("drops clip bytes still on the wire until the next clip begins", async () => {
    const { player, node } = await makePlayer()
    player.beginClip({ seq: 0, format: "pcm", sample_rate: 24000 })
    player.push(pcm16(new Array<number>(240).fill(0)))
    player.stop()
    node.posted.length = 0

    // Stragglers from the cancelled clip must not restart playback —
    // including a stale audio_begin, which must NOT un-mute (the old
    // clip's bytes would play over the next reply).
    player.beginClip({ seq: 9, format: "pcm", sample_rate: 24000 })
    player.push(pcm16(new Array<number>(240).fill(0)))
    expect(node.posted).toHaveLength(0)
    expect(player.remainingSec()).toBe(0)

    // The next turn's transcript reopens audio — its clips play normally.
    player.unmute()
    player.beginClip({ seq: 0, format: "pcm", sample_rate: 24000 })
    player.push(pcm16(new Array<number>(240).fill(0)))
    expect(node.posted).toHaveLength(1)
  })

  it("drops a clip whose decode finishes after stop", async () => {
    const { player } = await makePlayer()
    let resolveDecode: (v: { duration: number }) => void = () => {}
    vi.spyOn(ctx, "decodeAudioData").mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveDecode = resolve
        }),
    )
    player.beginClip({ seq: 0, format: "mp3" })
    player.push(new Uint8Array([1]).buffer)
    const pending = player.endClip() // decode starts…
    player.stop() // …interrupt lands mid-decode
    resolveDecode({ duration: 1 })
    await pending

    // The dead turn's clip must not schedule over the next reply.
    expect(ctx.sources).toHaveLength(0)
  })

  it("drops a straggler mp3 clip end after stop", async () => {
    const { player } = await makePlayer()
    player.beginClip({ seq: 0, format: "mp3" })
    player.push(new Uint8Array([1]).buffer)
    player.stop()

    await player.endClip()
    expect(ctx.decoded).toHaveLength(0)
    expect(ctx.sources).toHaveLength(0)
  })
})

describe("VoicePlayer softStop", () => {
  it("keeps the queued tail playing briefly, then stops", async () => {
    const { player, node } = await makePlayer()
    player.beginClip({ seq: 0, format: "pcm", sample_rate: 24000 })
    player.push(pcm16(new Array<number>(240).fill(0)))
    const queued = player.remainingSec()
    expect(queued).toBeGreaterThan(0)

    vi.useFakeTimers()
    try {
      player.softStop(50)
      // Not a hard cut: the worklet queue survives and murmurs out.
      expect(node.posted).not.toContain("clear")
      expect(player.remainingSec()).toBe(queued)

      // Stragglers still drop — a cancelled turn can't extend the drain.
      node.posted.length = 0
      player.push(pcm16(new Array<number>(240).fill(0)))
      expect(node.posted).toHaveLength(0)

      // The drain is bounded — the timer lands a real stop().
      await vi.advanceTimersByTimeAsync(60)
      expect(node.posted).toContain("clear")
      expect(player.remainingSec()).toBe(0)
    } finally {
      vi.useRealTimers()
    }
  })
})

describe("VoicePlayer ducking", () => {
  it("duck lowers the reply; unduck and stop restore full volume", async () => {
    const { player } = await makePlayer()
    const gain = ctx.gains[0]

    player.duck(0.15)
    expect(gain.ramped).toContain(0.15)

    // False barge-in: unduck fades the reply back up.
    player.unduck()
    expect(gain.ramped).toContain(1)

    // Confirmed barge-in → interrupted → stop(): the gain snaps back so
    // the next turn's reply never inherits the duck.
    gain.gain.value = 0.15 // pretend the ramp completed
    player.stop()
    expect(gain.gain.value).toBe(1)
  })
})
