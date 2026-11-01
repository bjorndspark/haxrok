/**
 * Browser → backend log shipping. Browsers can't write files, so console
 * output and unhandled errors are batched and POSTed to
 * /api/v1/logs/frontend, where the backend appends them to
 * logs/frontend/app.log — one place to read both sides during development.
 *
 * initRemoteLogging() runs once from app/main.tsx and only when VITE_API_URL
 * is set. Sending is best-effort: failures are dropped silently so logging
 * can never break the app.
 */

type LogLevel = "DEBUG" | "INFO" | "WARN" | "ERROR"

const REMOTE_FLUSH_MS = 5000
const REMOTE_BATCH_MAX = 200
const REMOTE_PENDING_MAX = 1000
const REMOTE_MESSAGE_MAX = 2000
const SESSION_KEY = "app.log-session-id"

interface RemoteLogEntry {
  ts: string
  level: LogLevel
  source: string
  message: string
}

const pendingRemote: RemoteLogEntry[] = []
let remoteInitialised = false

function safeStringify(value: unknown): string {
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function getSessionId(): string {
  try {
    const existing = sessionStorage.getItem(SESSION_KEY)
    if (existing) return existing
    const id = crypto.randomUUID()
    sessionStorage.setItem(SESSION_KEY, id)
    return id
  } catch {
    return "unknown"
  }
}

function enqueueRemote(level: LogLevel, source: string, args: unknown[]): void {
  const message = args
    .map((a) => (typeof a === "string" ? a : safeStringify(a)))
    .join(" ")
    .slice(0, REMOTE_MESSAGE_MAX)
  pendingRemote.push({ ts: new Date().toISOString(), level, source, message })
  if (pendingRemote.length > REMOTE_PENDING_MAX) pendingRemote.shift()
}

/** POST one batch. `keepalive` lets it outlive the page, so the same path
 *  serves the pagehide flush — sendBeacon can't: Chrome rejects a
 *  cross-origin beacon whose Blob type (application/json) isn't
 *  CORS-safelisted, and the API is cross-origin (VITE_API_URL). */
export function flushRemoteLogs(): void {
  const base = import.meta.env.VITE_API_URL
  if (!base || pendingRemote.length === 0) return
  const entries = pendingRemote.splice(0, REMOTE_BATCH_MAX)
  const body = JSON.stringify({ session_id: getSessionId(), entries })
  const url = `${base}/api/v1/logs/frontend`
  fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
    keepalive: true,
  }).catch(() => {})
}

export function initRemoteLogging(): void {
  if (remoteInitialised) return
  if (!import.meta.env.VITE_API_URL) return
  remoteInitialised = true

  type ConsoleMethod = "debug" | "log" | "info" | "warn" | "error"
  const patch: [ConsoleMethod, LogLevel][] = [
    ["debug", "DEBUG"],
    ["log", "INFO"],
    ["info", "INFO"],
    ["warn", "WARN"],
    ["error", "ERROR"],
  ]
  for (const [method, level] of patch) {
    const original = console[method].bind(console)
    console[method] = (...args: unknown[]) => {
      enqueueRemote(level, "console", args)
      original(...args)
    }
  }

  window.addEventListener("error", (event) => {
    enqueueRemote("ERROR", "window.onerror", [
      `${event.message} at ${event.filename}:${event.lineno}:${event.colno}`,
    ])
  })
  window.addEventListener("unhandledrejection", (event) => {
    enqueueRemote("ERROR", "unhandledrejection", [event.reason])
  })
  setInterval(() => flushRemoteLogs(), REMOTE_FLUSH_MS)
  window.addEventListener("pagehide", () => {
    flushRemoteLogs()
  })
}
