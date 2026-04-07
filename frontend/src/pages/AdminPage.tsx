type LegacyPageProps = {
  legacyHref: string;
};

export function AdminPage({ legacyHref }: LegacyPageProps) {
  return (
    <main className="page-shell">
      <section className="hero-card">
        <div className="hero-copy">
          <span className="eyebrow">Phase 4 Preview</span>
          <h1>Admin dashboard migration is queued next.</h1>
          <p>
            Runtime actions and operational visibility still work today on the legacy admin page.
            The asynchronous React admin panel will land after the chat streaming rewrite.
          </p>
        </div>
      </section>
      <section className="panel-surface stack-md" style={{ marginTop: "24px" }}>
        <h2>Current status</h2>
        <p className="body-muted">
          This route remains intentionally conservative in Phase 2 so account recovery flows are not
          destabilized while the frontend shell is being replaced.
        </p>
        <a className="secondary-link" href={legacyHref}>
          Open current admin page
        </a>
      </section>
    </main>
  );
}
