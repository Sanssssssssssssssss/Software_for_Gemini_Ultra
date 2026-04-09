import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

import type { UiMe } from "../lib/api";

type WorkspaceTopbarProps = {
  active: "login" | "setup" | "chat" | "admin";
  className?: string;
  me?: UiMe | null;
  onLogout?: (() => void) | null;
  toolbar?: ReactNode;
};

function formatIdentity(me?: UiMe | null) {
  if (!me?.authenticated) {
    return "本地工作区";
  }
  const roleLabel = me.is_admin ? "管理员" : "工作区用户";
  return me.subject ? `${roleLabel} · ${me.subject}` : roleLabel;
}

function getHomeTarget(me?: UiMe | null) {
  return me?.authenticated ? "/ui/chat" : "/ui/login";
}

function navLinkClassName({ isActive }: { isActive: boolean }) {
  return `workspace-topbar__nav-link${isActive ? " is-active" : ""}`;
}

export function WorkspaceTopbar({ active: _active, className, me, onLogout, toolbar }: WorkspaceTopbarProps) {
  const isAuthenticated = Boolean(me?.authenticated);
  const headerClassName = className ? `workspace-topbar panel-surface ${className}` : "workspace-topbar panel-surface";

  return (
    <header className={headerClassName}>
      <div className="workspace-topbar__brand">
        <NavLink className="workspace-topbar__logo" to={getHomeTarget(me)}>
          LAN CHAT
        </NavLink>
        <span className="workspace-topbar__identity">{formatIdentity(me)}</span>
      </div>

      <div className="workspace-topbar__spacer" />

      {toolbar ? <div className="workspace-topbar__toolbar">{toolbar}</div> : null}

      <nav className="workspace-topbar__nav" aria-label="工作区导航">
        {!isAuthenticated ? (
          <>
            <NavLink className={navLinkClassName} to="/ui/login">
              登录
            </NavLink>
            <NavLink className={navLinkClassName} to="/setup">
              配置
            </NavLink>
          </>
        ) : (
          <>
            <NavLink className={navLinkClassName} to="/setup">
              配置
            </NavLink>
            <NavLink className={navLinkClassName} to="/ui/chat">
              聊天
            </NavLink>
            {me?.is_admin ? (
              <NavLink className={navLinkClassName} to="/admin">
                管理
              </NavLink>
            ) : null}
            {onLogout ? (
              <button className="workspace-topbar__nav-link button-reset" type="button" onClick={onLogout}>
                退出
              </button>
            ) : null}
          </>
        )}
      </nav>
    </header>
  );
}
