/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Backend origin (e.g. http://localhost:8000) when the API is served elsewhere.
   *  Unset → same-origin: the dev server proxies /api to the backend. */
  readonly VITE_API_URL?: string
}
