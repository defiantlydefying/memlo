import Link from "next/link";
import { SiteShell } from "../components";

export default function TrustCenter() {
  return (
    <SiteShell>
      <main className="trust-main" id="main-content">
        <header className="trust-header">
          <p className="eyebrow">Memlo trust center</p>
          <h1>Trust, by default.</h1>
          <p className="hero-lede">
            Memlo remembers what matters without treating the whole server like
            data to collect. The boundaries are visible, specific, and reversible.
          </p>
        </header>

        <div className="trust-points">
          <article>
            <p className="trust-label">Setup gate</p>
            <h2>Nothing until an admin chooses.</h2>
            <p>A new installation reads nothing until an administrator uses /setup to select a class channel or category and timezone. /setup-status makes those boundaries visible.</p>
          </article>
          <article>
            <p className="trust-label">Analytics boundary</p>
            <h2>Measure the product, not the conversation.</h2>
            <p>Messages, files, questions, answers, and display names never enter product analytics.</p>
          </article>
          <article>
            <p className="trust-label">Deletion controls</p>
            <h2>Leave no class memory behind.</h2>
            <p>Use /delete-my-data or @Memlo forget me to delete your attributed records after confirmation. Administrators can erase the server’s entire Memlo memory.</p>
          </article>
          <article>
            <p className="trust-label">AI is optional</p>
            <h2>Deadlines do not need a model.</h2>
            <p>Manual deadline forms, browsing, rescheduling, cancellation, and reminders work without AI. When AI study features are enabled, relevant content is sent to Anthropic. Private quizzes and grading are visible only to the requester in Discord; private does not mean local-only processing.</p>
          </article>
        </div>

        <div className="trust-links">
          <Link className="button-link primary" href="/privacy">Read the Privacy Policy <span aria-hidden="true">→</span></Link>
          <Link className="button-link quiet" href="/terms">Read the Terms <span aria-hidden="true">→</span></Link>
        </div>
      </main>
    </SiteShell>
  );
}
