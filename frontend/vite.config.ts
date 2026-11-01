/// <reference types="vitest/config" />
import path from "path"
import react from "@vitejs/plugin-react"
import { defineConfig, loadEnv } from "vite"

export default defineConfig(({ mode }) => {
  // VITE_* vars live in the repo-root .env (scripts/setup/env.sh writes them
  // there next to the backend's), so load it for host-side `pnpm dev`; the
  // dev container gets the same vars from compose's env_file/environment.
  const envDir = path.resolve(__dirname, "..")
  // PROJECT_NAME is the display-name knob; VITE_APP_NAME may override it for
  // the browser tab. Empty prefix loads every var — this stays config-local,
  // nothing extra is exposed to the client (import.meta.env keeps the VITE_
  // filter).
  const env = loadEnv(mode, envDir, "")
  const appName = env.VITE_APP_NAME || env.PROJECT_NAME || "App"

  return {
    envDir,
    plugins: [
      react(),
      // index.html's <title> carries %VITE_APP_NAME%; vite only swaps
      // placeholders for vars that exist, so do it here with the fallbacks.
      {
        name: "app-title",
        transformIndexHtml: (html) => html.replaceAll("%VITE_APP_NAME%", appName),
      },
    ],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    server: {
      host: true,
      port: 5173,
      hmr: { host: "localhost" },
      // Host-side edits on the bind mount don't reliably emit inotify
      // events into the container — poll so hot updates never go stale.
      watch: { usePolling: true, interval: 300 },
    },
    test: {
      globals: true,
      environment: "jsdom",
      setupFiles: "./src/test-setup.ts",
    },
  }
})
