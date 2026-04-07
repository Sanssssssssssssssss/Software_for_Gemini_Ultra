type LegacyPageProps = {
  legacyHref: string;
};

export function ChatPage({ legacyHref }: LegacyPageProps) {
  return (
    <main className="page-shell">
      <section className="hero-card">
        <div className="hero-copy">
          <span className="eyebrow">Phase 3 Preview</span>
          <h1>Chat workspace is being rebuilt.</h1>
          <p>
            The modern React chat surface lands in the next phase. Until then, the existing
            production chat remains available through the current backend-rendered page.
          </p>
        </div>
      </section>
      <section className="panel-surface stack-md" style={{ marginTop: "24px" }}>
        <h2>Current status</h2>
        <p className="body-muted">
          Login and setup have been migrated first so the UI shell, styling system, and auth flow
          can stabilize before the streaming chat rewrite.
        </p>
        <a className="secondary-link" href={legacyHref}>
          Open current chat page
        </a>
      </section>
    </main>
  );
}
