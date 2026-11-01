import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import ortMjsUrl from "onnxruntime-web/ort-wasm-simd-threaded.mjs?url"
import ortWasmUrl from "onnxruntime-web/ort-wasm-simd-threaded.wasm?url"

import { API_V1_PREFIX, apiUrl } from "@/shared/api/client"

import { useChatUi } from "./store"
import { VoicePlayer, type ClipMeta } from "./voicePlayer"

export type VoiceStatus = "off" | "connecting" | "listening" | "recording" | "thinking" | "speaking"

export type VoiceError = "micDenied" | "vadFailed" | "wsFailed"

export interface UseVoiceOptions {
  /** Resolve (or create) the conversation the voice turn belongs to. */
  ensureConversation: () => Promise<number | null>
  /** Transcript of a confirmed utterance — the server persists it with the
   *  reply (a speculative turn is held until speech_end, so this never
   *  fires for speech that wasn't over). */
  onTranscript: (text: string) => void
  onToken: (token: string) => void
  /** Assistant reply persisted — refresh message/conversation queries. */
  onTurnComplete: () => void
  onError: (message: string | null) => void
}

export interface UseVoiceResult {
  enabled: boolean
  status: VoiceStatus
  error: VoiceError | null
  toggle: () => void
  /** Cancel the in-flight reply — stops TTS playback and generation. */
  interrupt: () => void
}

// 200Hz high-pass strips rumble/hum/thumps that trigger false speech
// detections; VAD and ASR key off harmonics/formants, not the fundamental.
const HIGHPASS_HZ = 200
const VAD_BASE_PATH = "/vad/"
// Frames stream to the server as they arrive, but VAD flags speech-start a
// few hundred ms in — a short pre-roll keeps the utterance's first syllable.
const PREROLL_FRAMES = 16 // 16 × 32ms ≈ 0.5s
// Speculation: a run of low-prob frames mid-utterance probably means the
// speaker is done — the server snapshots the audio and starts the turn
// ~0.7s before VAD redemption confirms it, holding its output until
// speech_end. A sustained voice run after that means a pause, not an
// end — cancel the speculative turn.
const MAYBE_END_PROB = 0.3 // isSpeech below this counts as quiet
const MAYBE_END_FRAMES = 16 // 16 × 32ms ≈ 0.5s of quiet
const RESUME_FRAMES = 4 // ~0.13s of sustained voice undoes a maybe_end
// RMS gate (~-40dBFS): a frame counts as voice only if Silero says speech
// AND it carries real energy — filters high-prob noise (fans, clicks,
// hum) that would otherwise poke holes in quiet runs or fake a resume.
const RMS_MIN = 0.01
// Talk-over: the VAD keeps running while a reply plays, so the reply's
// own echo can fire speech_start. It's only sent upstream after enough
// voiced frames land inside a short window — blips die in the VAD's own
// misfire/end path and slow echo accumulation times out before reaching
// the server.
const BARGE_IN_FRAMES = 4 // voiced frames needed to confirm a barge-in
const BARGE_IN_WINDOW = 12 // ~0.4s after arming to reach them — after
// that it's echo slowly accumulating, not the user; disarm.

export function f32ToS16(frame: Float32Array): ArrayBuffer {
  const buf = new ArrayBuffer(frame.length * 2)
  const view = new DataView(buf)
  for (let i = 0; i < frame.length; i++) {
    const s = Math.max(-1, Math.min(1, frame[i]))
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true)
  }
  return buf
}

export function useVoice(options: UseVoiceOptions): UseVoiceResult {
  const [enabled, setEnabled] = useState(false)
  const [status, setStatus] = useState<VoiceStatus>("off")
  const [error, setError] = useState<VoiceError | null>(null)

  // Only the VAD handle crosses the session boundary (the busy-effect
  // pauses/resumes it). Everything else lives inside the session closure —
  // shared refs let a stale teardown destroy a newer session's mic/ws.
  const vadRef = useRef<{
    start: () => Promise<void>
    pause: () => Promise<void>
    destroy: () => Promise<void>
    listening?: boolean
    errored?: unknown
  } | null>(null)
  const audioCtxRef = useRef<AudioContext | null>(null)
  // The live session's interrupt fn — the effect owns the ws/player, so
  // the exposed interrupt() reaches in through here rather than holding
  // stale session objects itself.
  const interruptRef = useRef<(() => void) | null>(null)
  const vadOpRef = useRef<Promise<void>>(Promise.resolve())
  // Last time the VAD produced a frame — the watchdog uses it to detect a
  // silently dead audio pipeline (worklet crash, ended mic track, …).
  const frameAliveRef = useRef(0)
  const restartsRef = useRef(0)
  // Status mirrored into a ref — the VAD callbacks live in the session
  // closure and would otherwise see the render-time value forever.
  const statusRef = useRef<VoiceStatus>("off")
  const [sessionNonce, setSessionNonce] = useState(0)
  // Conversation the live session's socket is bound to — null until init
  // resolves it. The socket picks a conversation at connect time, so it
  // must follow the viewed one — otherwise turns keep landing on a
  // conversation nobody is looking at (the "messages disappear" bug).
  const boundConvRef = useRef<number | null>(null)

  const optsRef = useRef(options)
  useLayoutEffect(() => {
    optsRef.current = options
    statusRef.current = status
  })

  // Fatal failures set a stable code on voice.error AND raise the
  // message banner — flipping enabled off alone would look like a dead
  // button with no explanation.
  const failVoice = useCallback((code: VoiceError, message: string) => {
    setError(code)
    optsRef.current.onError(message)
    setEnabled(false)
  }, [])

  // The VAD rests while a reply is being generated or spoken — the loop is
  // half-duplex: listen → think → speak → listen. Talk-over keeps it
  // running so the user can barge in mid-reply.
  const talkOver = useChatUi((s) => s.talkOver)
  const busy = !talkOver && (status === "thinking" || status === "speaking")

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    const t0 = performance.now()
    frameAliveRef.current = performance.now() // watchdog grace starts now

    // Session objects are local — assigned to refs only on success, so a
    // dead session can never be torn down twice nor corrupt a newer one.
    let ws: WebSocket | null = null
    let player: VoicePlayer | null = null
    let rawStream: MediaStream | null = null
    let ctx: AudioContext | null = null
    let vad: {
      start: () => Promise<void>
      pause: () => Promise<void>
      destroy: () => Promise<void>
    } | null = null
    let speaking = false
    let quietRun = 0
    let voicedRun = 0
    let maybeSent = false
    // Talk-over: VAD fired speech_start while a reply was in-flight — held
    // until a sustained voiced run confirms it's the user, not the echo.
    let bargeInArmed = false
    let bargeInRun = 0
    let bargeInWindow = 0
    const preroll: ArrayBuffer[] = []

    const cleanup = async () => {
      // Snapshot first, destroy after — and only strip shared refs that
      // still point at THIS session (a newer session may already own them).
      const v = vad
      vad = null
      if (vadRef.current === v) vadRef.current = null
      if (audioCtxRef.current === ctx) audioCtxRef.current = null
      ws?.close()
      void player?.close()
      if (v) await v.destroy().catch(() => {})
      rawStream?.getTracks().forEach((t) => t.stop())
      void ctx?.close().catch(() => {})
      if (interruptRef.current === interruptSession) interruptRef.current = null
      boundConvRef.current = null
    }
    const abort = () => {
      cancelled = true
      void cleanup()
    }

    const send = (obj: unknown) => {
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(typeof obj === "string" ? obj : JSON.stringify(obj))
      }
    }

    // Barge-in on demand: the server cancels the turn and echoes
    // "interrupted", which repeats the local stop — harmless and instant.
    const interruptSession = () => {
      send({ type: "interrupt" })
      player?.stop()
      bargeInArmed = false
    }
    interruptRef.current = interruptSession

    // An utterance's onset: tell the server, flush the pre-roll so the
    // first syllable makes it upstream, and mark frames as speech.
    const commitSpeechStart = () => {
      bargeInArmed = false // committing resolves the arm either way
      speaking = true
      quietRun = voicedRun = 0
      maybeSent = false
      setStatus("recording")
      // Always send the key — null means auto-detect (the server pins
      // what it hears). A bare absence would leave a stale pick.
      const lang = useChatUi.getState().voiceLanguage
      send({ type: "speech_start", language: lang === "auto" ? null : lang })
      for (const f of preroll.splice(0)) ws?.send(f)
    }

    const onWsMessage = (e: MessageEvent) => {
      if (cancelled) return
      const opts = optsRef.current
      if (typeof e.data !== "string") {
        player?.push(e.data as ArrayBuffer)
        return
      }
      const { event, data } = JSON.parse(e.data) as { event: string; data: string }
      if (event !== "token") {
        console.debug("voice evt:", event)
      }
      const d = JSON.parse(data) as Record<string, unknown>
      switch (event) {
        case "transcript": {
          // A transcript heads every new turn, always before its clips —
          // the safe point to reopen audio after an interrupt's mute.
          // stop() first: a barge-in's draining tail must not bleed into
          // the new reply.
          player?.stop()
          player?.unmute()
          // Empty transcript (misfire/silence) → straight back to
          // listening, no "thinking" flicker before turn_end.
          setStatus(d.text ? "thinking" : "listening")
          const ui = useChatUi.getState()
          if (d.language && ui.voiceLanguage === "auto")
            ui.setDetectedLanguage(d.language as string)
          if (d.text) opts.onTranscript(d.text as string)
          break
        }
        case "token":
          opts.onToken(d.token as string)
          break
        case "audio_begin":
          setStatus("speaking")
          player?.beginClip(d as unknown as ClipMeta)
          break
        case "audio_end":
          void player?.endClip()
          break
        case "message":
          opts.onTurnComplete()
          break
        case "interrupted":
          // Mid-utterance barge-in: let the reply's tail murmur out at
          // duck level (Alexa-style) rather than hard-cutting. Otherwise
          // (Stop button, or the user already finished) cut it now.
          if (speaking) player?.softStop()
          else player?.stop()
          bargeInArmed = false
          // The cut-short turn's partial reply is committed before this
          // event — refetch so the chat shows what was actually kept.
          opts.onTurnComplete()
          // A barge-in interrupts while the user is mid-utterance — show
          // recording, not listening.
          setStatus(speaking ? "recording" : "listening")
          break
        case "turn_end":
          // Let queued audio finish before the mic reopens — the mic
          // hearing the tail of the reply would trigger a phantom turn.
          bargeInArmed = false // the reply ended on its own mid-arm
          player?.unduck()
          if (player) player.whenDrained(() => !cancelled && setStatus("listening"))
          else setStatus("listening")
          break
        case "error":
          opts.onError((d.message as string) ?? "voice error")
          // A dead turn can't be barged into — release any held arm.
          bargeInArmed = false
          player?.unduck()
          setStatus("listening")
          break
      }
    }

    const init = async () => {
      try {
        let convId: number | null = null
        try {
          convId = await optsRef.current.ensureConversation()
        } catch {
          convId = null
        }
        if (cancelled) return
        if (convId == null) {
          // Backend/API unreachable — not a VAD problem; say so.
          failVoice("wsFailed", "Couldn't reach the chat backend")
          return
        }
        boundConvRef.current = convId
        // The viewed conversation may have moved during init's awaits —
        // a divergent bound id means the subscribe watcher below missed
        // it (it fires on changes, not on this assignment).
        if (convId !== useChatUi.getState().activeConversationId) {
          setSessionNonce((n) => n + 1)
          return
        }

        const wsUrl = apiUrl(`${API_V1_PREFIX}/chat/conversations/${convId}/voice`).replace(
          /^http/,
          "ws",
        )
        ws = new WebSocket(wsUrl)
        ws.binaryType = "arraybuffer"
        ws.onmessage = onWsMessage
        ws.onclose = () => {
          // Dropped mid-session: tell the user instead of going silently dead.
          if (!cancelled && vadRef.current === vad) {
            failVoice("wsFailed", "Voice connection lost")
          }
        }
        if (cancelled) {
          abort()
          return
        }

        player = await VoicePlayer.create()
        if (cancelled) {
          abort()
          return
        }

        rawStream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        })
        if (cancelled) {
          abort()
          return
        }

        ctx = new AudioContext()
        await ctx.resume().catch(() => {})
        const source = ctx.createMediaStreamSource(rawStream)
        const highpass = ctx.createBiquadFilter()
        highpass.type = "highpass"
        highpass.frequency.value = HIGHPASS_HZ
        const destination = ctx.createMediaStreamDestination()
        source.connect(highpass).connect(destination)
        const filteredStream = destination.stream

        const { MicVAD } = await import("@ricky0123/vad-web")
        if (cancelled) {
          abort()
          return
        }
        vad = await MicVAD.new({
          model: "v5",
          baseAssetPath: VAD_BASE_PATH,
          // 1.2s of silence before the VAD calls an utterance over —
          // longer holds used to cost latency, but speech_maybe_end at
          // ~0.5s quiet already started the turn speculatively, so the
          // extra hold is free forgiveness for mid-sentence pauses.
          redemptionMs: 1200,
          // Defaults (0.3/0.25) fire on modest noise — 0.5/0.35 wants a
          // confident speech call, and 300ms of it rejects clicks/thumps.
          positiveSpeechThreshold: 0.5,
          negativeSpeechThreshold: 0.35,
          minSpeechMs: 300,
          audioContext: ctx,
          getStream: () => Promise.resolve(filteredStream),
          pauseStream: () => Promise.resolve(),
          resumeStream: (s) => Promise.resolve(s),
          startOnLoad: false,
          // ort's glue/wasm must come through Vite's asset pipeline (it
          // loads the glue with a runtime import(), forbidden from public/).
          ortConfig: (ort) => {
            ort.env.wasm.wasmPaths = { mjs: ortMjsUrl, wasm: ortWasmUrl }
          },
          onFrameProcessed: (probs, frame) => {
            if (cancelled) return
            frameAliveRef.current = performance.now()
            let sum = 0
            for (const s of frame) sum += s * s
            const voiced =
              probs.isSpeech >= MAYBE_END_PROB && Math.sqrt(sum / frame.length) >= RMS_MIN
            if (speaking) {
              ws?.send(f32ToS16(frame))
              if (!voiced) {
                voicedRun = 0
                quietRun++
                if (!maybeSent && quietRun >= MAYBE_END_FRAMES) {
                  maybeSent = true
                  send({ type: "speech_maybe_end" })
                }
              } else {
                // Any voiced frame ends a quiet run — flickering probs
                // mid-utterance must never accumulate into a false end.
                quietRun = 0
                // But undoing a sent maybe_end needs sustained voice —
                // one noisy frame shouldn't cancel the speculation.
                voicedRun++
                if (maybeSent && voicedRun >= RESUME_FRAMES) {
                  maybeSent = false
                  send({ type: "speech_continue" })
                }
              }
            } else {
              preroll.push(f32ToS16(frame))
              if (preroll.length > PREROLL_FRAMES) preroll.shift()
              if (bargeInArmed) {
                // Voiced frames accumulate inside a short window — real
                // speech dips between words so a strictly-consecutive run
                // dropped real barge-ins, but an unbounded count lets
                // sustained echo slowly reach the threshold.
                if (voiced) bargeInRun++
                bargeInWindow++
                if (bargeInRun >= BARGE_IN_FRAMES) {
                  bargeInArmed = false
                  commitSpeechStart()
                } else if (bargeInWindow >= BARGE_IN_WINDOW) {
                  // Didn't confirm in time — echo, not the user.
                  bargeInArmed = false
                  player?.unduck()
                }
              }
            }
          },
          onSpeechStart: () => {
            if (cancelled) return
            console.debug("voice evt: speech_start")
            const s = statusRef.current
            if (useChatUi.getState().talkOver && (s === "thinking" || s === "speaking")) {
              // Possible barge-in — or the reply's own echo. Duck the
              // reply immediately (Alexa-style), then hold speech_start
              // until the voiced-run confirm in onFrameProcessed decides.
              bargeInArmed = true
              bargeInRun = 0
              bargeInWindow = 0
              player?.duck()
              return
            }
            commitSpeechStart()
          },
          onVADMisfire: () => {
            console.debug("voice evt: vad_misfire")
            if (bargeInArmed) {
              // Echo blip — nothing was sent upstream, nothing to undo.
              bargeInArmed = false
              player?.unduck()
              return
            }
            speaking = false
            quietRun = voicedRun = 0
            maybeSent = false
            // Too short to be speech: the server drops the buffered audio
            // and any speculation on it. speech_end here would run a turn
            // on the blip (whisper transcribes noise as "Thank you.").
            send({ type: "speech_cancel" })
            if (!cancelled) setStatus("listening")
          },
          onSpeechEnd: () => {
            console.debug("voice evt: speech_end")
            if (bargeInArmed) {
              // Ended before the confirm — echo, not the user.
              bargeInArmed = false
              player?.unduck()
              return
            }
            speaking = false
            quietRun = voicedRun = 0
            maybeSent = false
            send({ type: "speech_end" })
            if (!cancelled) setStatus("thinking")
          },
        })
        if (cancelled) {
          abort()
          return
        }

        vadRef.current = vad
        audioCtxRef.current = ctx
        console.debug("voice: ready in", Math.round(performance.now() - t0), "ms")
        setStatus("listening")
      } catch (e) {
        if (cancelled) return
        const denied =
          e instanceof DOMException && (e.name === "NotAllowedError" || e.name === "NotFoundError")
        console.error("voice: init failed", denied ? "mic denied" : e)
        failVoice(
          denied ? "micDenied" : "vadFailed",
          denied
            ? "Microphone unavailable — check the browser's mic permission"
            : "Voice detection failed to start",
        )
      }
    }
    void init()
    return () => {
      cancelled = true
      void cleanup()
    }
  }, [enabled, sessionNonce, failVoice])

  // Rebind when the viewed conversation diverges from the bound one —
  // switching chats or hitting New chat with voice on restarts the
  // session so ensureConversation attaches it to what's on screen.
  useEffect(() => {
    if (!enabled) return
    return useChatUi.subscribe((s) => {
      const bound = boundConvRef.current
      if (bound != null && bound !== s.activeConversationId) {
        boundConvRef.current = null
        setSessionNonce((n) => n + 1)
      }
    })
  }, [enabled])

  // Deaf-VAD watchdog: while we believe we're listening, frames must arrive
  // (~32ms cadence). Silence means the pipeline died silently — mic track
  // ended, worklet crashed, ctx suspended — not that the user is quiet.
  // Restart the session a couple of times, then surface the failure.
  useEffect(() => {
    if (!enabled) return
    const id = setInterval(() => {
      if (status !== "listening" && status !== "recording") return
      if (performance.now() - frameAliveRef.current < 2500) return
      const vad = vadRef.current
      const ctxState = audioCtxRef.current?.state
      if (restartsRef.current < 2) {
        restartsRef.current += 1
        console.warn(
          "voice: no audio frames — restarting session",
          `(vad.listening=${String(vad?.listening)} vad.errored=${String(vad?.errored)} ctx=${String(ctxState)})`,
        )
        setSessionNonce((n) => n + 1)
      } else {
        console.error(
          "voice: VAD unrecoverable",
          `(vad.listening=${String(vad?.listening)} vad.errored=${String(vad?.errored)} ctx=${String(ctxState)})`,
        )
        failVoice("vadFailed", "Voice detection stopped — toggle voice off and on")
      }
    }, 2000)
    return () => clearInterval(id)
  }, [enabled, status, failVoice])

  // Pause/resume the VAD with the turn state. Transitions are serialized —
  // a start() racing an in-flight pause() can leave the frame processor
  // paused while listening=true, and the VAD goes permanently deaf.
  useEffect(() => {
    const vad = vadRef.current
    if (!vad || status === "off" || status === "connecting") return
    vadOpRef.current = vadOpRef.current.then(async () => {
      try {
        if (busy) {
          await vad.pause()
        } else {
          // Fire-and-forget: resume() can stay pending without user
          // activation, and awaiting it would stall the whole op chain.
          void audioCtxRef.current?.resume().catch(() => {})
          await vad.start()
        }
      } catch (e) {
        console.warn("voice: VAD", busy ? "pause" : "start", "failed", e)
      }
    })
  }, [busy, status])

  const toggle = useCallback(() => {
    setError(null)
    optsRef.current.onError(null) // retrying clears the banner too
    restartsRef.current = 0 // a manual toggle resets the restart budget
    setEnabled((prev) => !prev)
    if (!enabled) setStatus("connecting")
    else setStatus("off")
  }, [enabled])

  const interrupt = useCallback(() => {
    if (!interruptRef.current) return
    interruptRef.current()
    // Optimistic: silence is instant. The server's "interrupted" event
    // confirms and re-sets status (recording if the user kept talking).
    setStatus((s) => (s === "thinking" || s === "speaking" ? "listening" : s))
  }, [])

  return { enabled, status: enabled ? status : "off", error, toggle, interrupt }
}
