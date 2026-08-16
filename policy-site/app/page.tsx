import Link from "next/link";
import { DISCORD_INSTALL_URL, SiteShell } from "./components";
import { InteractiveHero, Reveal } from "./interactive";

export default function Home() {
  return (
    <SiteShell>
      <main className="landing" id="main-content">
        <InteractiveHero installUrl={DISCORD_INSTALL_URL} />

        <Reveal><section className="outcomes" id="how-it-works" aria-labelledby="outcomes-title">
          <div className="section-intro">
            <p className="eyebrow">One message in</p>
            <h2 id="outcomes-title">Three useful things out.</h2>
          </div>
          <div className="outcome-list">
            <article>
              <p className="outcome-verb">Remember</p>
              <h3>Deadlines stop getting buried.</h3>
              <p>Memlo catches dates and announcements in approved channels and brings them back when they matter.</p>
            </article>
            <article>
              <p className="outcome-verb">Answer</p>
              <h3>“What did I miss?” gets a real answer.</h3>
              <p>Shared notes, slides, links, and images become context the whole class can ask about.</p>
            </article>
            <article>
              <p className="outcome-verb">Practice</p>
              <h3>The chat becomes the study session.</h3>
              <p>Ask for a quiz, answer together or privately, and get feedback without opening another app.</p>
            </article>
          </div>
        </section></Reveal>

        <Reveal><section className="trust-banner" aria-labelledby="trust-title">
          <div className="trust-rule" aria-hidden="true"><span /></div>
          <p className="eyebrow">Trust is a product feature</p>
          <h2 id="trust-title">Memlo sees the class channels an admin chooses. Nothing else.</h2>
          <div className="trust-banner-footer">
            <p>Content-free analytics. Student deletion controls. Server-wide deletion. Clear public policies.</p>
            <Link className="button-link light" href="/trust">Visit the Trust Center <span aria-hidden="true">→</span></Link>
          </div>
        </section></Reveal>
      </main>
    </SiteShell>
  );
}
