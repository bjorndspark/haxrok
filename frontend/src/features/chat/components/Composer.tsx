import { AudioLines, Image, Mic, Send, Square } from "lucide-react"
import { useState } from "react"

import { Button } from "@/shared/ui/button"
import { Textarea } from "@/shared/ui/textarea"
import { cn } from "@/shared/lib/utils"

import { useChatUi } from "../store"
import { useChatVoice } from "../useChat"
import { useRecorder } from "../useRecorder"
import type { VoiceError, VoiceStatus } from "../useVoice"

interface Props {
  onSend: (text: string, generateImage: boolean) => void
  disabled: boolean
}

const VOICE_STATUS_TEXT: Partial<Record<VoiceStatus, string>> = {
  connecting: "Starting voice…",
  listening: "Listening…",
  recording: "Recording…",
  thinking: "Thinking…",
  speaking: "Speaking…",
}

const VOICE_ERROR_TEXT: Record<VoiceError, string> = {
  micDenied: "Microphone unavailable — check browser permission",
  vadFailed: "Voice detection failed to start",
  wsFailed: "Voice connection lost",
}

function languageDisplayName(code: string): string {
  if (code === "en") return "English"
  if (code === "ja") return "日本語"
  return code.toUpperCase()
}

export function Composer({ onSend, disabled }: Props) {
  const [text, setText] = useState("")
  const {
    imageMode,
    setImageMode,
    voiceLanguage,
    detectedLanguage,
    setVoiceLanguage,
    talkOver,
    setTalkOver,
  } = useChatUi()
  const voice = useChatVoice()
  const recorder = useRecorder((transcript) => {
    setText((prev) => (prev ? `${prev} ${transcript}` : transcript))
  })

  const submit = () => {
    const value = text.trim()
    if (!value || disabled) return
    setText("")
    onSend(value, imageMode)
    setImageMode(false)
  }

  return (
    <div className="border-t p-4">
      {voice.enabled && (
        <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
          <span
            className={cn(
              "inline-block h-2 w-2 rounded-full",
              voice.status === "recording" ? "bg-red-500 animate-pulse" : "bg-green-500",
            )}
          />
          <span className="truncate">{VOICE_STATUS_TEXT[voice.status] ?? voice.status}</span>
          <label
            className="ml-auto flex shrink-0 cursor-pointer items-center gap-1.5"
            title="Let your speech interrupt the reply"
          >
            <input
              type="checkbox"
              checked={talkOver}
              onChange={(e) => setTalkOver(e.target.checked)}
              className="h-3 w-3 accent-current"
            />
            Talk over
          </label>
          {(voice.status === "thinking" || voice.status === "speaking") && (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 px-2"
              onClick={voice.interrupt}
              title="Stop the reply"
            >
              <Square /> Stop
            </Button>
          )}
        </div>
      )}
      {/* Init failures flip enabled back off, which would hide the row
          above — surface the error on its own line so the button
          doesn't look dead. */}
      {voice.error && (
        <div className="mb-2 text-xs text-destructive">voice: {VOICE_ERROR_TEXT[voice.error]}</div>
      )}
      <div className="flex items-end gap-2">
        <Textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault()
              submit()
            }
          }}
          placeholder={imageMode ? "Describe an image…" : "Type a message…"}
          className="min-h-[44px] flex-1 resize-none"
          rows={2}
        />
        <Button
          variant={imageMode ? "default" : "outline"}
          size="icon"
          onClick={() => setImageMode(!imageMode)}
          title="Image mode: next message generates an image"
        >
          <Image />
        </Button>
        <Button
          variant={voice.enabled ? "default" : "outline"}
          size="icon"
          onClick={voice.toggle}
          title="Hands-free voice mode"
          className={cn(voice.status === "recording" && "animate-pulse")}
        >
          <AudioLines />
        </Button>
        <select
          value={voiceLanguage}
          onChange={(e) => setVoiceLanguage(e.target.value)}
          disabled={recorder.recording || voice.status === "recording"}
          aria-label="Speech language"
          title="Speech language"
          className="h-9 shrink-0 cursor-pointer self-center rounded-md border-0 bg-transparent px-1 text-xs text-muted-foreground hover:bg-accent focus-visible:outline-none"
        >
          <option value="auto">
            {detectedLanguage ? `Auto (${languageDisplayName(detectedLanguage)})` : "Auto"}
          </option>
          <option value="en">English</option>
          <option value="ja">日本語</option>
        </select>
        <Button
          variant={recorder.recording ? "destructive" : "outline"}
          size="icon"
          onClick={() => void recorder.toggle()}
          disabled={voice.enabled}
          title={recorder.recording ? "Stop recording" : "Voice input"}
          className={cn(recorder.recording && "animate-pulse")}
        >
          {recorder.recording ? <Square /> : <Mic />}
        </Button>
        <Button size="icon" onClick={submit} disabled={disabled || !text.trim()}>
          <Send />
        </Button>
      </div>
    </div>
  )
}
