import { useCallback, useEffect, useLayoutEffect, useRef } from "react"

import { errorMessage } from "@/shared/lib/utils"

import { transcribeAudio } from "./api"
import { useChatUi } from "./store"

export interface UseRecorderResult {
  recording: boolean
  /** Start recording, or stop and transcribe. */
  toggle: () => Promise<void>
}

/** Push-to-talk: record with MediaRecorder, transcribe on stop, hand the
 *  text to ``onTranscript``. Mic and transcription failures land in the
 *  error banner instead of vanishing. */
export function useRecorder(onTranscript: (text: string) => void): UseRecorderResult {
  const recording = useChatUi((s) => s.recording)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const onTranscriptRef = useRef(onTranscript)
  useLayoutEffect(() => {
    onTranscriptRef.current = onTranscript
  })

  // Unmounting mid-recording must release the mic, not leave it live.
  useEffect(() => {
    const ref = recorderRef
    return () => {
      const recorder = ref.current
      if (recorder?.state === "recording") {
        recorder.onstop = null
        recorder.stop()
        recorder.stream.getTracks().forEach((t) => {
          t.stop()
        })
        useChatUi.getState().setRecording(false)
      }
    }
  }, [])

  const toggle = useCallback(async () => {
    const ui = useChatUi.getState()
    if (ui.recording) {
      recorderRef.current?.stop()
      return
    }
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      ui.setError("Microphone unavailable — check the browser's mic permission")
      return
    }
    const recorder = new MediaRecorder(stream)
    const chunks: Blob[] = []
    recorder.ondataavailable = (e) => chunks.push(e.data)
    recorder.onstop = () => {
      stream.getTracks().forEach((t) => {
        t.stop()
      })
      const { voiceLanguage, detectedLanguage, setRecording, setError } = useChatUi.getState()
      setRecording(false)
      const lang = voiceLanguage === "auto" ? detectedLanguage : voiceLanguage
      transcribeAudio(new Blob(chunks, { type: recorder.mimeType }), lang ?? undefined)
        .then((text) => {
          onTranscriptRef.current(text)
        })
        .catch((e: unknown) => {
          setError(errorMessage(e, "transcription failed"))
        })
    }
    recorder.start()
    recorderRef.current = recorder
    ui.setRecording(true)
  }, [])

  return { recording, toggle }
}
