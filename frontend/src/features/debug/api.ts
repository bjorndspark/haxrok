import { API_V1_PREFIX, apiPost, apiUrl, type ErrorResponse } from "@/shared/api/client"

import type {
  AnalysisEvent,
  AskAnswer,
  AskRequest,
  CheckedAnalysis,
  DesignSummary,
  MeasurementInput,
  Mode,
  ProgressEvent,
  ProjectFile,
  Simulation,
} from "./types"

/** Parse SSE frames (sse-starlette, CRLF-separated) off a fetch body. */
async function* readSse(body: ReadableStream<Uint8Array>): AsyncGenerator<{
  event: string
  data: string
}> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    buffer = buffer.replace(/\r\n/g, "\n")
    const frames = buffer.split("\n\n")
    buffer = frames.pop() ?? ""
    for (const frame of frames) {
      let event = "message"
      let data = ""
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim()
        else if (line.startsWith("data:")) data += line.slice(5).trim()
      }
      // Keep-alive pings are comments (": ping") and carry no data.
      if (data) yield { event, data }
    }
  }
}

/**
 * Upload the project and stream one analysis. Upload and kicad-cli errors
 * come back as the error envelope and are thrown; once the stream opens,
 * failures arrive as an `error` event.
 */
export async function* streamAnalysis(
  files: ProjectFile[],
  symptoms: string,
  measurements: MeasurementInput[],
  mode: Mode,
  signal: AbortSignal,
): AsyncGenerator<AnalysisEvent> {
  const form = new FormData()
  form.append("mode", mode)
  for (const f of files) form.append("files", f.file, f.path)
  form.append("symptoms", symptoms)
  form.append("measurements", JSON.stringify(measurements))

  const res = await fetch(apiUrl(`${API_V1_PREFIX}/debug/analyze`), {
    method: "POST",
    body: form,
    signal,
  })
  if (!res.ok || !res.body) {
    let detail = `HTTP ${res.status}`
    try {
      const body = (await res.json()) as ErrorResponse
      if (body.error.message) detail = body.error.message
    } catch {
      // Not the envelope (proxy error, etc.): keep the status.
    }
    throw new Error(detail)
  }

  for await (const { event, data } of readSse(res.body)) {
    if (event === "design_summary")
      yield { type: "design_summary", summary: JSON.parse(data) as DesignSummary }
    else if (event === "progress")
      yield { type: "progress", progress: JSON.parse(data) as ProgressEvent }
    else if (event === "simulation")
      yield { type: "simulation", simulation: JSON.parse(data) as Simulation }
    else if (event === "result")
      yield { type: "result", result: JSON.parse(data) as CheckedAnalysis }
    else if (event === "error")
      yield { type: "error", message: (JSON.parse(data) as { message: string }).message }
  }
}

/** Ask about one simulation; the conversation so far goes with it. */
export async function askSimulation(req: AskRequest): Promise<string> {
  return (await apiPost<AskAnswer>("/debug/ask", req)).answer
}
