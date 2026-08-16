import Link from "next/link";

export const DISCORD_INSTALL_URL =
  "https://discord.com/oauth2/authorize?client_id=1527179920386691072&permissions=117760&scope=bot%20applications.commands";

export function Header() {
  return (
    <header className="topbar">
      <Link className="brand" href="/">
        <span className="brand-mark" aria-hidden="true">M</span><span>Memlo</span>
      </Link>
      <nav className="nav" aria-label="Main navigation">
        <Link href="/#demo">Demo</Link>
        <Link href="/trust">Trust</Link>
        <Link href="/privacy">Privacy</Link>
        <a className="nav-cta" href={DISCORD_INSTALL_URL} target="_blank" rel="noreferrer">Add to Discord</a>
      </nav>
    </header>
  );
}

export function Footer() {
  return (
    <footer className="footer">
      <span>Memlo · Memory for class group chats</span>
      <nav className="footer-links" aria-label="Footer navigation">
        <Link href="/trust">Trust</Link>
        <Link href="/privacy">Privacy</Link>
        <Link href="/terms">Terms</Link>
        <a href="mailto:ehsieh175@gmail.com">Contact</a>
      </nav>
    </footer>
  );
}

export function SiteShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="site-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <Header />
      {children}
      <Footer />
    </div>
  );
}

export function PolicyLayout({
  title,
  eyebrow,
  sections,
}: {
  title: string;
  eyebrow: string;
  sections: Array<{ id: string; label: string; content: React.ReactNode }>;
}) {
  return (
    <SiteShell>
      <main className="policy-main" id="main-content">
        <header className="policy-header">
          <p className="eyebrow">{eyebrow}</p>
          <h1>{title}</h1>
          <p className="updated">Last updated August 11, 2026</p>
        </header>
        <div className="policy-grid">
          <aside className="toc" aria-label="On this page">
            <span>On this page</span>
            {sections.map((section) => (
              <a key={section.id} href={`#${section.id}`}>{section.label}</a>
            ))}
          </aside>
          <article className="policy-content">
            {sections.map((section) => (
              <section key={section.id} id={section.id}>
                <h2>{section.label}</h2>
                {section.content}
              </section>
            ))}
          </article>
        </div>
      </main>
    </SiteShell>
  );
}
