import type { SessionSummary } from "../lib/api";

export type SessionPreview = {
  title?: string;
  preview?: string;
};

type SessionRailProps = {
  sessions: SessionSummary[];
  selectedSessionId: string | null;
  previews: Record<string, SessionPreview>;
  isLoading: boolean;
  onCreateSession: () => void;
  onSelectSession: (sessionId: string) => void;
};

function formatSessionLabel(session: SessionSummary) {
  return `${session.session_id.slice(0, 8)} · ${session.account_id}`;
}

export function SessionRail({
  sessions,
  selectedSessionId,
  previews,
  isLoading,
  onCreateSession,
  onSelectSession,
}: SessionRailProps) {
  return (
    <aside className="chat-rail panel-surface">
      <div className="chat-rail__top">
        <div>
          <span className="eyebrow">Sessions</span>
          <h2>Conversation rail</h2>
        </div>
        <button className="primary-button rail-create" type="button" onClick={onCreateSession}>
          New Session
        </button>
      </div>
      <div className="chat-rail__hint">
        Sessions stay sticky to a single backend account. Starting fresh creates a clean routed
        conversation without reloading the full rail.
      </div>
      <div className="session-rail-list">
        {isLoading ? <div className="rail-empty">Loading sessions…</div> : null}
        {!isLoading && !sessions.length ? <div className="rail-empty">No sessions yet.</div> : null}
        {!isLoading
          ? sessions.map((session) => {
              const preview = previews[session.session_id];
              const isSelected = session.session_id === selectedSessionId;
              return (
                <button
                  key={session.session_id}
                  className={`session-rail-item${isSelected ? " active" : ""}`}
                  type="button"
                  onClick={() => onSelectSession(session.session_id)}
                >
                  <div className="session-rail-item__row">
                    <strong>{preview?.title || formatSessionLabel(session)}</strong>
                    <span className={`status-pill compact session-state-pill state-${session.status}`}>
                      {session.status}
                    </span>
                  </div>
                  <div className="session-rail-item__meta">
                    <span>{session.account_id}</span>
                    <span>{session.updated_at || session.created_at || "now"}</span>
                  </div>
                  <p>{preview?.preview || "Conversation context is ready for the next turn."}</p>
                </button>
              );
            })
          : null}
      </div>
    </aside>
  );
}
