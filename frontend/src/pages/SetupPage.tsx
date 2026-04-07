import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { type SetupStatus, getSetupStatus } from "../lib/api";

const DEFAULT_STEPS = [
  "Run `py scripts/bootstrap_local.py` to generate local config files.",
  "Fill `config/accounts.json` with valid Gemini web cookies.",
  "Run `py scripts/doctor.py` to confirm local readiness.",
  "Start the app with `python scripts/run_local.py --env-file .env`.",
];

export function SetupPage() {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;

    void getSetupStatus()
      .then((payload) => {
        if (!cancelled) {
          setStatus(payload);
        }
      })
      .catch((caught) => {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "Failed to load setup status.");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const nextSteps = useMemo(
    () => (status?.next_steps?.length ? status.next_steps : DEFAULT_STEPS),
    [status],
  );

  return (
    <main className="page-shell">
      <section className="hero-card">
        <div className="hero-copy">
          <span className="eyebrow">Service Bootstrap</span>
          <h1>Setup & readiness</h1>
          <p>
            This page tracks the minimum configuration required before the shared Gemini service can
            be opened to teammates. Fix the failing checks first, then move on to login and live
            validation.
          </p>
        </div>
        <div className="hero-note stack-sm">
          <span className={`status-pill ${status?.setup_complete ? "ok" : "warn"}`}>
            {status?.setup_complete ? "Ready for UI" : "Configuration Pending"}
          </span>
          <Link className="secondary-link" to="/ui/login">
            Go to login
          </Link>
        </div>
      </section>

      <section className="setup-grid">
        <div className="panel-surface stack-lg">
          <div className="section-head">
            <h2>Current checks</h2>
            {loading ? <span className="body-muted">Refreshing…</span> : null}
          </div>
          {error ? <div className="inline-error">{error}</div> : null}
          <div className="stack-md">
            {status?.checks?.map((check) => (
              <article key={check.name} className={`check-card check-${check.status}`}>
                <div className="check-header">
                  <span
                    className={`status-pill compact ${
                      check.status === "pass" ? "ok" : check.status === "warn" ? "warn" : "danger"
                    }`}
                  >
                    {check.status.toUpperCase()}
                  </span>
                  <strong>{check.name}</strong>
                </div>
                <p>{check.detail}</p>
                {check.action ? <div className="body-muted">Action: {check.action}</div> : null}
              </article>
            ))}
          </div>
        </div>

        <aside className="panel-surface stack-lg">
          <div className="section-head">
            <h2>Recommended path</h2>
          </div>
          <div className="stack-md">
            {nextSteps.map((step, index) => (
              <div key={`${index}-${step}`} className="step-card">
                <span className="step-index">{index + 1}</span>
                <p>{step}</p>
              </div>
            ))}
          </div>
          <div className="panel-divider" />
          <div className="stack-sm">
            <h3>Operator note</h3>
            <p className="body-muted">
              This service rides on a non-official Gemini Web wrapper. Even after setup passes,
              runtime availability can still move with cookie expiry, cooldown, and upstream web
              changes.
            </p>
          </div>
        </aside>
      </section>
    </main>
  );
}
