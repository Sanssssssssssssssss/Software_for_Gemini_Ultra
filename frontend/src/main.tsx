import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./app/App";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/state-overrides.css";
import "./styles/layout-overrides.css";
import "./styles/admin-chat-overrides.css";
import "./styles/opencode-refresh.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
