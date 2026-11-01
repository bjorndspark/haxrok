import { Route, Routes } from "react-router-dom"

import { ChatPage } from "@/features/chat"

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<ChatPage />} />
    </Routes>
  )
}
