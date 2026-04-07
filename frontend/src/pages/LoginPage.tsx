import { FormEvent, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { getMe, getSetupStatus, login } from "../lib/api";

type FormState = {
  username: string;
  password: string;
};

export function LoginPage() {
  const navigate = useNavigate();
  const [form, setForm] = useState<FormState>({ username: "", password: "" });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [setupReady, setSetupReady] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;

    void Promise.all([getMe(), getSetupStatus()])
      .then(([me, setup]) => {
        if (cancelled) {
          return;
        }
        setSetupReady(setup.setup_complete);
        if (me.authenticated) {
          navigate("/ui/chat", { replace: true });
        }
      })
      .catch(() => {
        if (!cancelled) {
          setSetupReady(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [navigate]);

  const helperText = useMemo(() => {
    if (setupReady === null) {
      return "Checking service readiness…";
    }
    if (setupReady) {
      return "Internal UI access is ready. Sign in with your LAN UI credentials.";
    }
    return "Setup checks still have failures. Review setup before handing this UI to users.";
  }, [setupReady]);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      await login(form.username, form.password);
      navigate("/ui/chat", { replace: true });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Login failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="page-shell">
      <section className="hero-card">
        <div className="hero-copy">
          <span className="eyebrow">Internal Shared Access</span>
          <h1>Gemini Internal Service</h1>
          <p>
            A shared Gemini workspace for the LAN: centralized account pool, durable sessions, and
            operator-visible health, without asking each teammate to manage browser cookies locally.
          </p>
        </div>
        <div className="hero-note">
          <span className={`status-pill ${setupReady ? "ok" : "warn"}`}>
            {setupReady ? "Service Ready" : "Needs Setup Review"}
          </span>
        </div>
      </section>

      <section className="auth-layout">
        <div className="auth-panel panel-surface">
          <h2>Sign in</h2>
          <p className="body-muted">{helperText}</p>
          <form className="stack-lg" onSubmit={onSubmit}>
            <label className="field">
              <span>Username</span>
              <input
                autoComplete="username"
                value={form.username}
                onChange={(event) => setForm((current) => ({ ...current, username: event.target.value }))}
                placeholder="admin"
                required
              />
            </label>
            <label className="field">
              <span>Password</span>
              <input
                autoComplete="current-password"
                type="password"
                value={form.password}
                onChange={(event) => setForm((current) => ({ ...current, password: event.target.value }))}
                placeholder="UI password"
                required
              />
            </label>
            {error ? <div className="inline-error">{error}</div> : null}
            <button className="primary-button" disabled={loading} type="submit">
              {loading ? "Signing in…" : "Open Workspace"}
            </button>
          </form>
        </div>

        <aside className="info-panel panel-surface">
          <h3>What this login controls</h3>
          <ul className="feature-list">
            <li>UI access is separate from the Gemini web accounts in the backend pool.</li>
            <li>Standard users can open and continue only their own conversations.</li>
            <li>Administrators can inspect account health and run recovery actions.</li>
          </ul>
          <div className="panel-divider" />
          <div className="stack-sm">
            <div className="body-muted">Need to validate the service first?</div>
            <Link className="secondary-link" to="/setup">
              Open setup checks
            </Link>
          </div>
        </aside>
      </section>
    </main>
  );
}
