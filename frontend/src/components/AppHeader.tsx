import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import type { UiMe } from "../lib/api";

type AppHeaderProps = {
  me: UiMe;
  title: string;
  subtitle: string;
  badge: string;
  actions?: ReactNode;
};

export function AppHeader({ me, title, subtitle, badge, actions }: AppHeaderProps) {
  return (
    <header className="app-header">
      <div className="app-header__intro">
        <span className="eyebrow">{badge}</span>
        <h1>{title}</h1>
        <p>{subtitle}</p>
      </div>
      <div className="app-header__actions">
        <span className="status-pill ok compact">
          {me.subject} · {me.role}
        </span>
        <Link className="secondary-link compact-link" to="/setup">
          Setup
        </Link>
        <Link className="secondary-link compact-link" to="/ui/chat">
          Chat
        </Link>
        {me.is_admin ? (
          <Link className="secondary-link compact-link" to="/admin">
            Admin
          </Link>
        ) : null}
        {actions}
      </div>
    </header>
  );
}
