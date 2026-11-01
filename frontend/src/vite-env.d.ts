/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Backend origin (e.g. http://localhost:8000). Unset → same-origin, no remote logging. */
  readonly VITE_API_URL?: string
}
