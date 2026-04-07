import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { AdminPage } from "../pages/AdminPage";
import { ChatPage } from "../pages/ChatPage";
import { LoginPage } from "../pages/LoginPage";
import { SetupPage } from "../pages/SetupPage";

export function App() {
  const location = useLocation();

  return (
    <Routes>
      <Route path="/" element={<Navigate to="/ui/login" replace />} />
      <Route path="/ui/login" element={<LoginPage />} />
      <Route path="/setup" element={<SetupPage />} />
      <Route path="/ui/chat" element={<ChatPage key={location.pathname} />} />
      <Route path="/admin" element={<AdminPage key={location.pathname} />} />
      <Route path="*" element={<Navigate to="/ui/login" replace />} />
    </Routes>
  );
}
