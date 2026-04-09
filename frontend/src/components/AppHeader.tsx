import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import type { UiMe } from "../lib/api";

type AppHeaderProps = {
  actions?: ReactNode;
  badge: string;
  me: UiMe;
  subtitle: string;
  title: string;
};

function formatIdentity(me: UiMe) {
  const roleLabel = me.is_admin ? "管理员" : "普通用户";
  return me.subject ? `${roleLabel} / ${me.subject}` : roleLabel;
}

export function AppHeader({ actions, badge, me, subtitle, title }: AppHeaderProps) {
  return (
    <header className="app-header panel-surface">
      <div className="app-header__intro">
        <div className="app-header__topline">
          <span className="section-kicker">{badge}</span>
          <span className="app-header__identity">{formatIdentity(me)}</span>
        </div>
        <h1>{title}</h1>
        <p>{subtitle}</p>
      </div>

      <div className="app-header__actions">
        <nav className="app-nav" aria-label="工作区导航">
          <Link className="secondary-link compact-link" to="/setup">
            配置
          </Link>
          <Link className="secondary-link compact-link" to="/ui/chat">
            聊天
          </Link>
          {me.is_admin ? (
            <Link className="secondary-link compact-link" to="/admin">
              管理
            </Link>
          ) : null}
        </nav>
        {actions}
      </div>
    </header>
  );
}
