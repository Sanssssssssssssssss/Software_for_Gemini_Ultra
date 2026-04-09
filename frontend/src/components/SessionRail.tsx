import { memo, useState } from "react";

import type { SessionActivity, SessionPreview } from "../chat/types";
import { getSessionTitle } from "../chat/session-helpers";
import type { SessionSummary } from "../lib/api";

type SessionRailProps = {
  activities: Record<string, SessionActivity>;
  isLoading: boolean;
  onCreateSession: () => void;
  onDeleteSession: (sessionId: string) => Promise<unknown>;
  onRenameSession: (sessionId: string, title: string) => Promise<unknown>;
  onSelectSession: (sessionId: string) => void;
  previews: Record<string, SessionPreview>;
  selectedSessionId: string | null;
  sessions: SessionSummary[];
};

function formatUpdatedAt(value: string | null) {
  if (!value) {
    return "刚刚";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function getSessionStateLabel(session: SessionSummary, activity: SessionActivity | undefined) {
  if (activity?.isStreaming) {
    return "STREAMING";
  }
  if (activity?.isSending) {
    return "SENDING";
  }
  if (activity?.loadingHistory) {
    return "LOADING";
  }
  switch (session.status) {
    case "active":
      return "ACTIVE";
    case "ready":
      return "READY";
    default:
      return session.status.toUpperCase();
  }
}

function SessionRailComponent({
  activities,
  isLoading,
  onCreateSession,
  onDeleteSession,
  onRenameSession,
  onSelectSession,
  previews,
  selectedSessionId,
  sessions,
}: SessionRailProps) {
  const [menuSessionId, setMenuSessionId] = useState<string | null>(null);
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [pendingSessionId, setPendingSessionId] = useState<string | null>(null);

  async function submitRename(sessionId: string) {
    const trimmed = draftTitle.trim();
    if (!trimmed) {
      setEditingSessionId(null);
      setDraftTitle("");
      return;
    }
    setPendingSessionId(sessionId);
    try {
      await onRenameSession(sessionId, trimmed);
      setEditingSessionId(null);
      setMenuSessionId(null);
      setDraftTitle("");
    } finally {
      setPendingSessionId(null);
    }
  }

  async function confirmDelete(sessionId: string) {
    const confirmed = window.confirm("删除后会从当前列表移除这个会话，确定继续吗？");
    if (!confirmed) {
      return;
    }
    setPendingSessionId(sessionId);
    try {
      await onDeleteSession(sessionId);
      setMenuSessionId(null);
      if (editingSessionId === sessionId) {
        setEditingSessionId(null);
        setDraftTitle("");
      }
    } finally {
      setPendingSessionId(null);
    }
  }

  return (
    <aside className="chat-rail panel-surface">
      <div className="chat-rail__top">
        <span className="section-kicker">会话</span>
        <button className="secondary-link compact-link rail-create" type="button" onClick={onCreateSession}>
          <span data-testid="new-session-button">新建</span>
        </button>
      </div>

      <div className="session-rail-list">
        {isLoading ? <div className="rail-empty">正在加载会话...</div> : null}
        {!isLoading && !sessions.length ? <div className="rail-empty">还没有会话，点击上方新建开始。</div> : null}
        {!isLoading
          ? sessions.map((session, index) => {
              const preview = previews[session.session_id];
              const activity = activities[session.session_id];
              const isSelected = session.session_id === selectedSessionId;
              const isEditing = editingSessionId === session.session_id;
              const isPending = pendingSessionId === session.session_id;

              return (
                <div
                  key={session.session_id}
                  className={`session-rail-item${isSelected ? " active" : ""}${menuSessionId === session.session_id ? " menu-open" : ""}`}
                >
                  <div className="session-rail-item__row">
                    {isEditing ? (
                      <input
                        autoFocus
                        className="session-rail-item__title-input"
                        disabled={isPending}
                        value={draftTitle}
                        onBlur={() => void submitRename(session.session_id)}
                        onChange={(event) => setDraftTitle(event.target.value)}
                        onClick={(event) => event.stopPropagation()}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") {
                            event.preventDefault();
                            void submitRename(session.session_id);
                          }
                          if (event.key === "Escape") {
                            setEditingSessionId(null);
                            setDraftTitle("");
                          }
                        }}
                      />
                    ) : (
                      <button
                        className="session-rail-item__main button-reset"
                        type="button"
                        onClick={() => onSelectSession(session.session_id)}
                      >
                        <strong>{getSessionTitle(session, preview, index)}</strong>
                      </button>
                    )}

                    <div className="session-rail-item__actions">
                      <span className={`status-pill compact session-state-pill state-${session.status}`}>
                        {getSessionStateLabel(session, activity)}
                      </span>
                      <button
                        aria-label="会话操作"
                        className="session-rail-item__menu button-reset"
                        type="button"
                        onClick={() =>
                          setMenuSessionId((current) => (current === session.session_id ? null : session.session_id))
                        }
                      >
                        ···
                      </button>
                    </div>
                  </div>

                  <button
                    className="session-rail-item__main button-reset"
                    type="button"
                    onClick={() => onSelectSession(session.session_id)}
                  >
                    <div className="session-rail-item__meta">
                      <span>{session.account_id ? `路由 ${session.account_id}` : "待分配路由"}</span>
                      <span>{formatUpdatedAt(session.updated_at || session.created_at)}</span>
                    </div>
                  </button>

                  {menuSessionId === session.session_id ? (
                    <div className="session-rail-item__menu-panel">
                      <button
                        className="secondary-link compact-link button-reset"
                        disabled={isPending}
                        type="button"
                        onClick={() => {
                          setEditingSessionId(session.session_id);
                          setDraftTitle(getSessionTitle(session, preview, index));
                          setMenuSessionId(session.session_id);
                        }}
                      >
                        重命名
                      </button>
                      <button
                        className="secondary-link compact-link button-reset danger-action"
                        disabled={isPending || activity?.isStreaming}
                        type="button"
                        onClick={() => {
                          void confirmDelete(session.session_id);
                        }}
                      >
                        {isPending ? "处理中..." : "删除"}
                      </button>
                    </div>
                  ) : null}
                </div>
              );
            })
          : null}
      </div>
    </aside>
  );
}

export const SessionRail = memo(SessionRailComponent);
