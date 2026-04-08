import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { AppHeader } from "../components/AppHeader";
import {
  type AccountSummary,
  type AdminOverview,
  ApiError,
  getAdminOverview,
  getMe,
  logout,
  runAdminAction,
  type SessionSummary,
  type UiMe,
} from "../lib/api";

function formatTimestamp(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const ADMIN_ACTIONS = [
  { key: "refresh", label: "Refresh" },
  { key: "clear-cooldown", label: "Clear Cooldown" },
  { key: "mark-reauth-required", label: "Mark Reauth" },
  { key: "disable-runtime", label: "Disable" },
  { key: "enable-runtime", label: "Enable" },
] as const;

export function AdminPage() {
  const navigate = useNavigate();
  const [me, setMe] = useState<UiMe | null>(null);
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pendingActions, setPendingActions] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const currentMe = await getMe();
        if (!currentMe.authenticated) {
          navigate("/ui/login", { replace: true });
          return;
        }
        if (!currentMe.is_admin) {
          navigate("/ui/chat", { replace: true });
          return;
        }
        const nextOverview = await getAdminOverview();
        if (!cancelled) {
          setMe(currentMe);
          setOverview(nextOverview);
        }
      } catch (caught) {
        if (!cancelled) {
          if (caught instanceof ApiError && caught.status === 401) {
            navigate("/ui/login", { replace: true });
            return;
          }
          setError(caught instanceof Error ? caught.message : "Failed to load admin overview.");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void load();
    const interval = window.setInterval(() => {
      void getAdminOverview()
        .then((nextOverview) => {
          if (!cancelled) {
            setOverview(nextOverview);
          }
        })
        .catch(() => undefined);
    }, 15000);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [navigate]);

  async function handleLogout() {
    await logout();
    navigate("/ui/login", { replace: true });
  }

  async function refreshOverview() {
    setError("");
    try {
      const nextOverview = await getAdminOverview();
      setOverview(nextOverview);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to refresh admin data.");
    }
  }

  async function handleAction(account: AccountSummary, action: string) {
    const key = `${account.account_id}:${action}`;
    setPendingActions((current) => ({ ...current, [key]: action }));
    setNotice("");
    setError("");
    try {
      const result = await runAdminAction(account.account_id, action);
      setOverview((current) => {
        if (!current) {
          return current;
        }
        return {
          ...current,
          accounts: current.accounts.map((item) =>
            item.account_id === account.account_id
              ? {
                  ...item,
                  state: result.state,
                  state_reason: result.detail,
                }
              : item,
          ),
        };
      });
      setNotice(`${account.account_id}: ${result.detail}`);
      await refreshOverview();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Admin action failed.");
    } finally {
      setPendingActions((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
    }
  }

  if (!me && loading) {
    return (
      <main className="page-shell">
        <section className="hero-card">
          <div className="hero-copy">
            <span className="eyebrow">Operations Bootstrap</span>
            <h1>Loading admin console...</h1>
            <p>Account health, queue pressure, and recovery controls are being hydrated.</p>
          </div>
        </section>
      </main>
    );
  }

  if (!me || !overview) {
    return null;
  }

  return (
    <main className="page-shell admin-shell">
      <AppHeader
        me={me}
        badge="Operations Console"
        title="Account pool admin"
        subtitle="A focused operating surface for queue pressure, runtime state, and account recovery. Actions are async and patch local state instead of reloading the page."
        actions={
          <button className="secondary-link compact-link button-reset" type="button" onClick={handleLogout}>
            Logout
          </button>
        }
      />

      <section className="admin-grid">
        <div className="admin-main">
          <section className="admin-summary-grid">
            <article className="metric-card">
              <span>Ready Accounts</span>
              <strong>{overview.telemetry.account_ready}</strong>
              <p>{overview.telemetry.account_total} configured in total.</p>
            </article>
            <article className="metric-card">
              <span>HTTP Load</span>
              <strong>{overview.telemetry.active_requests}</strong>
              <p>{overview.telemetry.total_requests} total requests observed.</p>
            </article>
            <article className="metric-card">
              <span>Persisted Sessions</span>
              <strong>{overview.telemetry.chat_sessions}</strong>
              <p>{overview.telemetry.chat_messages} messages stored.</p>
            </article>
            <article className="metric-card">
              <span>Stored Assets</span>
              <strong>{overview.telemetry.chat_assets}</strong>
              <p>{overview.telemetry.asset_expired_total} expired, {overview.telemetry.asset_deleted_total} orphan deletions.</p>
            </article>
            <article className="metric-card danger">
              <span>Failures</span>
              <strong>{overview.telemetry.total_errors}</strong>
              <p>{overview.telemetry.session_failovers_total} failovers persisted.</p>
            </article>
          </section>

          <section className="panel-surface admin-panel">
            <div className="section-head">
              <div>
                <span className="eyebrow">Runtime Accounts</span>
                <h2>Account health and controls</h2>
              </div>
              <button
                className="secondary-link compact-link button-reset"
                data-testid="admin-refresh-overview"
                type="button"
                onClick={refreshOverview}
              >
                Refresh overview
              </button>
            </div>
            {error ? <div className="inline-error">{error}</div> : null}
            {notice ? <div className="inline-banner tone-success">{notice}</div> : null}
            <div className="admin-account-grid">
              {overview.accounts.map((account) => (
                <article className="account-admin-card" data-testid={`admin-account-${account.account_id}`} key={account.account_id}>
                  <div className="account-admin-card__top">
                    <div>
                      <h3>{account.account_id}</h3>
                      <p>{account.state_reason || account.status_description || "No extra detail reported."}</p>
                    </div>
                    <span className={`status-pill compact state-badge state-${account.state}`}>
                      {account.state}
                    </span>
                  </div>
                  <div className="account-admin-card__stats">
                    <div>
                      <span>Gemini</span>
                      <strong>{account.account_status || "unknown"}</strong>
                    </div>
                    <div>
                      <span>In-flight</span>
                      <strong>
                        {account.active_requests}/{account.configured_max_concurrency}
                      </strong>
                    </div>
                    <div>
                      <span>Queue</span>
                      <strong>{account.queue_depth}</strong>
                    </div>
                    <div>
                      <span>Failures</span>
                      <strong>{account.failure_count}</strong>
                    </div>
                  </div>
                  {account.cooldown_until ? (
                    <div className="body-muted">Cooldown until {formatTimestamp(account.cooldown_until)}</div>
                  ) : null}
                  {account.recent_errors.length ? (
                    <div className="recent-errors">
                      {account.recent_errors.map((item) => (
                        <span key={item}>{item}</span>
                      ))}
                    </div>
                  ) : null}
                  <div className="account-admin-card__actions">
                    {ADMIN_ACTIONS.map((action) => {
                      const key = `${account.account_id}:${action.key}`;
                      const isPending = pendingActions[key] === action.key;
                      return (
                        <button
                          className="secondary-link compact-link button-reset"
                          data-testid={`admin-action-${account.account_id}-${action.key}`}
                          disabled={isPending}
                          key={action.key}
                          type="button"
                          onClick={() => {
                            void handleAction(account, action.key);
                          }}
                        >
                          {isPending ? "Working..." : action.label}
                        </button>
                      );
                    })}
                  </div>
                </article>
              ))}
            </div>
          </section>
        </div>

        <aside className="admin-side">
          <section className="panel-surface admin-panel stack-lg">
            <div className="section-head">
              <div>
                <span className="eyebrow">Sessions</span>
                <h2>Recent routed work</h2>
              </div>
            </div>
            <div className="admin-session-list">
              {overview.sessions.length ? (
                overview.sessions.map((session: SessionSummary) => (
                  <div className="admin-session-row" key={session.session_id}>
                    <div>
                      <strong>{session.session_id.slice(0, 10)}</strong>
                      <p>{session.account_id}</p>
                    </div>
                    <div className="admin-session-row__meta">
                      <span className={`status-pill compact state-badge state-${session.status}`}>
                        {session.status}
                      </span>
                      <small>{formatTimestamp(session.updated_at || session.created_at)}</small>
                    </div>
                  </div>
                ))
              ) : (
                <div className="rail-empty">No recent sessions yet.</div>
              )}
            </div>
          </section>

          <section className="panel-surface admin-panel stack-md">
            <div className="section-head">
              <div>
                <span className="eyebrow">State Mix</span>
                <h2>Routing posture</h2>
              </div>
            </div>
            <div className="state-chip-grid">
              {Object.entries(overview.telemetry.account_state_counts).map(([state, count]) => (
                <div className="state-chip" key={state}>
                  <span>{state}</span>
                  <strong>{count}</strong>
                </div>
              ))}
            </div>
            <p className="body-muted">
              The admin view stays read-mostly. Account actions are explicit and async, while batch
              and deeper maintenance workflows remain in the backend APIs.
            </p>
          </section>

          <section className="panel-surface admin-panel stack-md">
            <div className="section-head">
              <div>
                <span className="eyebrow">Assets</span>
                <h2>Recent file activity</h2>
              </div>
            </div>
            <div className="state-chip-grid">
              {Object.entries(overview.telemetry.asset_status_counts).map(([state, count]) => (
                <div className="state-chip" key={state}>
                  <span>{state}</span>
                  <strong>{count}</strong>
                </div>
              ))}
            </div>
            <div className="admin-session-list">
              {overview.assets.length ? (
                overview.assets.map((asset) => (
                  <div className="admin-session-row" key={asset.asset_id}>
                    <div>
                      <strong>{asset.filename}</strong>
                      <p>{asset.owner_subject} · {asset.mime_type}</p>
                    </div>
                    <div className="admin-session-row__meta">
                      <span className={`status-pill compact state-badge state-${asset.status}`}>{asset.status}</span>
                      <small>{formatTimestamp(asset.expires_at || asset.created_at)}</small>
                    </div>
                  </div>
                ))
              ) : (
                <div className="rail-empty">No recent assets yet.</div>
              )}
            </div>
          </section>
        </aside>
      </section>
    </main>
  );
}
