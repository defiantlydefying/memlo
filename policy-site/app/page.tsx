import Link from "next/link";
import {
  DISCORD_INSTALL_URL,
  DISCORD_SUPPORT_URL,
  SiteShell,
} from "./components";
import { InteractiveHero, Reveal } from "./interactive";

export default function Home() {
  return (
    <SiteShell>
      <main className="landing" id="main-content">
        <InteractiveHero
          installUrl={DISCORD_INSTALL_URL}
          supportUrl={DISCORD_SUPPORT_URL}
        />

        <Reveal>
          <section
            className="outcomes"
            id="how-it-works"
            aria-labelledby="outcomes-title"
          >
            <div className="section-intro">
              <p className="eyebrow">Less scrolling. More knowing.</p>
              <h2 id="outcomes-title">
                A home for the
                <br />
                “important bits.”
              </h2>
              <p>
                Commands to do things. Mentions to ask questions. Start with
                <code> /help</code> for a button menu—no commands to memorize.
              </p>
            </div>
            <div className="outcome-list">
              <article>
                <p className="outcome-verb">
                  <span aria-hidden="true">↳</span> 01 · Remember
                </p>
                <h3>Deadlines stop getting buried.</h3>
                <p>
                  Add an assignment with <code>/add-deadline</code>. Browse it
                  with <code>/due</code>, correct it with{" "}
                  <code>/reschedule</code>, or stop its reminders with{" "}
                  <code>/cancel-deadline</code>. No AI needed.
                </p>
              </article>
              <article>
                <p className="outcome-verb">
                  <span aria-hidden="true">⌕</span> 02 · Find
                </p>
                <h3>“What did I miss?” gets a real answer.</h3>
                <p>
                  Optional AI answers from the notes, slides, links, and images
                  shared in approved channels. Ask <code>@Memlo explain these notes</code>.
                  Check important answers against the original source.
                </p>
              </article>
              <article>
                <p className="outcome-verb">
                  <span aria-hidden="true">✳</span> 03 · Practice
                </p>
                <h3>The chat becomes the study session.</h3>
                <p>
                  Use <code>/quiz</code>, pick a saved study set and difficulty,
                  then press Start quiz. Topics stay separate, quizzes are private by
                  default, and feedback stays in Discord. Mention “quiz me” and
                  Memlo points you to that same flow.
                </p>
              </article>
            </div>
          </section>
        </Reveal>

        <Reveal>
          <section
            className="outcomes setup-section"
            id="get-started"
            aria-labelledby="setup-title"
          >
            <div className="section-intro">
              <p className="eyebrow">Add. Choose. Start.</p>
              <h2 id="setup-title">
                Make yourself
                <br />
                at home.
              </h2>
              <p>
                A class channel, not your whole server. You choose where Memlo
                belongs.
              </p>
            </div>
            <div className="outcome-list">
              <article>
                <p className="outcome-verb">01 · Set up</p>
                <h3>An admin picks the boundaries.</h3>
                <p>
                  Add Memlo, then run <code>/setup</code> with Manage Server
                  permission. Choose a class channel or category and its
                  timezone. Memlo does not request Administrator permission.
                </p>
              </article>
              <article>
                <p className="outcome-verb">02 · Keep track</p>
                <h3>The deadline tools work without AI.</h3>
                <p>
                  Forms, reminders, rescheduling, and cancellation do not need a
                  paid AI key. <code>/setup-status</code> shows the timezone and
                  tracked channels. Only the original contributor or an admin
                  can change a deadline.
                </p>
              </article>
              <article>
                <p className="outcome-verb">03 · Practice</p>
                <h3>Your pages. Your study set.</h3>
                <p>
                  With AI enabled, share screenshots, a PDF, text, or a study
                  link. <code>/materials</code> lists saved sets;{" "}
                  <code>/quiz</code> offers Warm-up, Mixed, and Challenge
                  levels. Source checks reduce unsupported questions, but AI can
                  still be wrong.
                </p>
              </article>
            </div>
          </section>
        </Reveal>

        <Reveal>
          <section className="trust-banner" aria-labelledby="trust-title">
            <div className="trust-rule" aria-hidden="true">
              <span />
            </div>
            <p className="eyebrow">Trust is a product feature</p>
            <h2 id="trust-title">
              A good memory.
              <br />
              <span>Clear boundaries.</span>
            </h2>
            <div className="trust-banner-footer">
              <p>
                Memlo sees the class channels an admin chooses. Nothing else.
                Use <code>/delete-my-data</code> or{" "}
                <code>@Memlo forget me</code> to remove your saved
                contributions. Questions? Reach Emma on Discord:
                @kidwithakrow._.
              </p>
              <Link className="button-link light" href="/trust">
                Visit the Trust Center <span aria-hidden="true">→</span>
              </Link>
            </div>
          </section>
        </Reveal>
      </main>
    </SiteShell>
  );
}
