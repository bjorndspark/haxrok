import { Route, Routes } from "react-router-dom"

import { DebugPage } from "@/features/debug"

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<DebugPage />} />
    </Routes>
  )
}
