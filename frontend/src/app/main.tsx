import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { BrowserRouter } from "react-router-dom"

import { initRemoteLogging } from "@/shared/lib/logger"

import App from "./App"
import "./index.css"

// Ships console.* + unhandled errors to the backend so logs/frontend/app.log
// sits next to logs/backend/app.log — one place to read both sides in dev.
initRemoteLogging()

const queryClient = new QueryClient()

const root = document.getElementById("root")
if (!root) throw new Error("index.html is missing the #root element")

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
