"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

type ChatMessage = {
  id: number;
  author: "pear" | "eh--" | "BOB" | "Memlo";
  body: string;
  replyTo?: string;
  quiz?: boolean;
  private?: boolean;
};

const startingMessages: ChatMessage[] = [
  {
    id: 1,
    author: "pear",
    body: "science test on October 27 — sharing the thermal energy notes here",
  },
  { id: 2, author: "eh--", body: "WHAT\nREALLY" },
  { id: 3, author: "pear", body: "😭😭\nyes bro\ndid u just find out" },
  { id: 4, author: "eh--", body: "let’s practice from those notes" },
  {
    id: 5,
    author: "Memlo",
    body: "Sample study set · Thermal energy\nThermal energy is the total kinetic energy of particles. Heat is energy transferred between objects; temperature measures average kinetic energy. Heat transfers by conduction (direct contact), convection (fluid movement), and radiation (electromagnetic waves).",
  },
];

const examples = ["/help", "/due", "/quiz", "/add-deadline"];

const quizText = [
  "1. What is thermal energy, and how is it related to the motion of particles in a substance?",
  "2. What's the difference between heat and temperature?",
  "3. Name and briefly describe the three main methods of heat transfer.",
].join("\n");

function replyFor(input: string): Omit<ChatMessage, "id" | "author"> | null {
  const value = input.toLowerCase();

  if (value.startsWith("/help")) {
    return {
      body: "Commands to do things. Mentions to ask questions.\nIn Discord, /help opens buttons for Upcoming deadlines, Add deadline, Study material, Start quiz, and Setup status. Try the sample commands below.",
      replyTo: input,
      private: true,
    };
  }

  if (/@memlo.*quiz|^quiz\s+(?:me|us)/.test(value)) {
    return {
      body: "In Discord, this offers a Start quiz button that opens the same /quiz study-set picker. Choose your material and difficulty; quizzes are private by default.",
      replyTo: input,
    };
  }

  if (/^\/?quiz(?:\s+(?:me|us))?(?:\s+on\b|$)/.test(value)) {
    return {
      body: "Sample quiz after choosing Thermal energy · Mixed and pressing Start quiz in Discord:\n\n" + quizText,
      replyTo: input,
      quiz: true,
      private: true,
    };
  }

  if (value.startsWith("/add-deadline")) {
    return {
      body: "In Discord, /add-deadline opens a form for the assignment, due date, and optional class or audience. No AI is used. Dates and reminders follow the server’s selected timezone.",
      replyTo: input,
      private: true,
    };
  }

  if (value.startsWith("/reschedule") || value.startsWith("/cancel-deadline")) {
    return {
      body: "In Discord, choose a saved deadline to reschedule it or cancel its reminders. Only its original contributor or an admin can make that change. Cancellation asks for confirmation.",
      replyTo: input,
      private: true,
    };
  }

  if (value.startsWith("/materials")) {
    return {
      body: "Study shelf · Sample set\nThermal energy · 1 file\nIn the real bot, /quiz lets you choose a set and a difficulty. Unrelated topics stay separate.",
      replyTo: input,
      private: true,
    };
  }

  if (
    /^\/due\b|what('s| is)? due|whats due|when.*(?:test|quiz)|missed|what did i miss|catch me up/.test(
      value,
    )
  ) {
    return {
      body: "Coming up · Sample deadline\nTue, Oct 27 · Science test\nAmerica/Chicago · Use /reschedule or /cancel-deadline to make a correction in Discord.",
      replyTo: input,
      private: value.startsWith("/due"),
    };
  }

  if (
    /https?:\/\//.test(value) ||
    /study guide|worksheet|pdf|slides/.test(value)
  ) {
    return {
      body: "This preview uses the sample Thermal energy notes above; it does not fetch files or links. In Discord, share study material, check /materials, then choose a saved set with /quiz.",
    };
  }

  if (
    /tomorrow|tmr|monday|tuesday|wednesday|thursday|friday|saturday|sunday|due|deadline|test|exam/.test(
      value,
    ) &&
    !/@memlo/.test(value)
  ) {
    return null;
  }

  return {
    body: "This is an illustrative preview, not a live bot or AI chat. Try /due, /quiz, or /add-deadline, then add Memlo to Discord for the real tools.",
    replyTo: input,
  };
}

function MessageText({ children }: { children: string }) {
  const parts = children.split(/(@Memlo)/gi);
  return (
    <p className="message-body">
      {parts.map((part, index) =>
        /^@memlo$/i.test(part) ? (
          <mark key={index}>{part}</mark>
        ) : (
          <span key={index}>{part}</span>
        ),
      )}
    </p>
  );
}

export function InteractiveHero({
  installUrl,
  supportUrl,
}: {
  installUrl: string;
  supportUrl: string;
}) {
  const reduceMotion = useReducedMotion();
  const [messages, setMessages] = useState<ChatMessage[]>(startingMessages);
  const [draft, setDraft] = useState("");
  const [typing, setTyping] = useState(false);
  const [memoryStatus, setMemoryStatus] = useState(
    "Illustrative preview · no Discord or AI connection",
  );
  const [privateQuiz, setPrivateQuiz] = useState<number | null>(null);
  const [privateDraft, setPrivateDraft] = useState(
    "1. Thermal energy is the kinetic energy of particles.\n\n2. Heat is transferred between objects; temperature measures average kinetic energy.\n\n3. Radiation transfers heat through waves.",
  );
  const nextId = useRef(6);
  const feedRef = useRef<HTMLDivElement>(null);
  const dialogRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (privateQuiz === null) return;
    const previousFocus =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialogRef.current?.querySelector<HTMLTextAreaElement>("textarea")?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPrivateQuiz(null);
      if (event.key !== "Tab") return;
      const controls =
        dialogRef.current?.querySelectorAll<HTMLElement>("button, textarea");
      if (!controls?.length) return;
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("keydown", handleKey);
      document.body.style.overflow = previousOverflow;
      previousFocus?.focus();
    };
  }, [privateQuiz]);

  useEffect(() => {
    const feed = feedRef.current;
    if (feed)
      feed.scrollTo({
        top: feed.scrollHeight,
        behavior: reduceMotion ? "auto" : "smooth",
      });
  }, [messages, typing, reduceMotion]);

  const sendMessage = (message: string) => {
    const clean = message.trim();
    if (!clean || typing) return;

    const userId = nextId.current++;
    setMessages((current) => [
      ...current,
      { id: userId, author: "BOB", body: clean },
    ]);
    setDraft("");

    const response = replyFor(clean);
    if (!response) {
      setMemoryStatus("Preview only · nothing is saved to Discord");
      window.setTimeout(
        () =>
          setMemoryStatus("Illustrative preview · no Discord or AI connection"),
        3200,
      );
      return;
    }

    setTyping(true);
    window.setTimeout(
      () => {
        const botId = nextId.current++;
        setMessages((current) => [
          ...current,
          { id: botId, author: "Memlo", ...response },
        ]);
        setTyping(false);
      },
      reduceMotion ? 40 : 650,
    );
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    sendMessage(draft);
  };

  const submitPrivateAnswer = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!privateDraft.trim()) return;
    setPrivateQuiz(null);
    setTyping(true);
    window.setTimeout(
      () => {
        const botId = nextId.current++;
        setMessages((current) => [
          ...current,
          {
            id: botId,
            author: "Memlo",
            private: true,
            body: [
              "Example feedback for the prefilled sample answers (not live grading):",
              "",
              "✅ 1. Yep! Thermal energy is the total kinetic energy of particles, and more thermal energy means faster particle motion.",
              "✅ 2. Correct — heat transfers between objects; temperature measures the average kinetic energy of particles.",
              "❌ 3. You're missing conduction (direct contact) and convection (fluid currents). Radiation transfers through electromagnetic waves.",
              "",
              "2/3 — solid work, just brush up on the heat transfer methods!",
            ].join("\n"),
          },
        ]);
        setTyping(false);
      },
      reduceMotion ? 40 : 650,
    );
  };

  return (
    <section className="chat-hero" aria-labelledby="product-title">
      <motion.div
        className="chat-hero-copy"
        initial={reduceMotion ? false : { opacity: 0, y: 24 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.65, ease: [0.22, 1, 0.36, 1] }}
      >
        <div>
          <p className="hero-kicker inverse">
            <span className="signal-dot" aria-hidden="true" />
            Memory for class Discords
          </p>
          <h1 id="product-title">
            The message
            <br /> passes.<strong>The memory stays.</strong>
          </h1>
        </div>
        <div className="chat-hero-intro">
          <p>
            The class chat moves fast. Keep the deadlines, find the notes, and
            turn “wait, we have a test?” into a study session.
          </p>
          <div className="hero-actions">
            <motion.a
              className="button-link hero-primary"
              href={installUrl}
              target="_blank"
              rel="noreferrer"
              whileHover={{ y: -3 }}
              whileTap={{ scale: 0.98 }}
            >
              Add Memlo to Discord <span aria-hidden="true">↗</span>
            </motion.a>
            <a className="button-link hero-secondary" href="#demo">
              Play with the demo <span aria-hidden="true">↓</span>
            </a>
            <a
              className="button-link hero-secondary hero-support"
              href={supportUrl}
              target="_blank"
              rel="noreferrer"
            >
              Try Memlo live <span aria-hidden="true">↗</span>
            </a>
          </div>
          <p className="hero-proof inverse-proof">
            Admin-selected channels only · Nothing is read before setup · Live
            test server available
          </p>
        </div>
        <div
          className="study-desk"
          aria-label="Illustration of a deadline card, science notes, and a private quiz. Sample content, not live data."
        >
          <span className="desk-spark spark-one" aria-hidden="true">
            ✳
          </span>
          <span className="desk-spark spark-two" aria-hidden="true">
            ✧
          </span>
          <div className="desk-orbit" aria-hidden="true" />
          <div className="desk-note note-back">
            <span>YOUR STUDY SHELF</span>
            <h2>
              A little less chaos.
              <br />A lot more “got it.”
            </h2>
            <div className="note-lines" />
            <div className="notebook-tabs">
              <i />
              <i />
              <i />
            </div>
          </div>
          <div className="desk-note note-deadline">
            <span className="note-pin" aria-hidden="true" />
            <div className="note-overline">ONE LESS THING TO FORGET</div>
            <div className="deadline-date">
              <b>27</b>
              <span>
                OCT
                <br />
                TUESDAY
              </span>
            </div>
            <h3>Science test</h3>
            <p>Thermal energy · #9th-grade</p>
            <span className="note-chip">↳ /due</span>
          </div>
          <div className="desk-note note-quiz">
            <span className="note-overline">JUST FOR YOU</span>
            <h3>A tiny confidence boost.</h3>
            <p>
              Heat moves from warm to cool.
              <br />
              You knew more than you thought.
            </p>
            <span className="quiz-checks" aria-hidden="true">
              ✓ &nbsp; ✓ &nbsp; ✓
            </span>
            <span className="note-chip">/quiz · private by default</span>
          </div>
          <span className="desk-caption">
            for the things worth remembering ↗
          </span>
        </div>
      </motion.div>

      <div className="demo-intro" id="demo">
        <div>
          <p className="eyebrow">No server required to take a peek</p>
          <h2>Your chat. A little more helpful.</h2>
        </div>
        <p>
          Try a command below. This little sandbox runs in your browser; it
          doesn’t connect to Discord or AI.
        </p>
      </div>
      <motion.div
        className="live-demo-shell"
        initial={reduceMotion ? false : { opacity: 0, y: 36, scale: 0.985 }}
        whileInView={{ opacity: 1, y: 0, scale: 1 }}
        viewport={{ once: true, amount: 0.1 }}
        transition={{ duration: 0.75, ease: [0.22, 1, 0.36, 1] }}
      >
        <div className="live-demo-label">
          <div>
            <span className="live-dot" />
            <strong>Try the Memlo flow</strong>
          </div>
          <p>{memoryStatus}</p>
        </div>

        <div className="prompt-shelf" aria-label="Example messages">
          <span>Try /due, /quiz, or /add-deadline</span>
          {examples.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => sendMessage(example)}
              disabled={typing}
            >
              {example}
            </button>
          ))}
        </div>

        <div
          className="discord-preview live-discord accurate-discord"
          aria-label="Interactive simulation based on the real Memlo Discord demo"
        >
          <aside className="discord-server-rail" aria-label="Server rail">
            <span className="discord-home" aria-hidden="true">
              ⌁
            </span>
            <span className="active-server">WF</span>
            <span>+</span>
            <span>⌕</span>
          </aside>

          <aside
            className="discord-channels"
            aria-label="Class server channels"
          >
            <div className="server-name">
              <strong>west ford hs study chat</strong>
              <span>⌄</span>
            </div>
            <span className="server-tool">▣ &nbsp; Events</span>
            <p>INFORMATION</p>
            <span className="discord-channel">
              <i>#</i> welcome-and-rules
            </span>
            <span className="discord-channel">
              <i>#</i> notes-resources
            </span>
            <p>TEXT CHANNELS</p>
            <span className="discord-channel active">
              <i>#</i> 9th-grade
            </span>
            <span className="discord-channel">
              <i>#</i> 10th-grade
            </span>
            <span className="discord-channel">
              <i>#</i> 11th-grade
            </span>
            <span className="discord-channel">
              <i>#</i> 12th-grade
            </span>
            <span className="discord-channel">
              <i>#</i> general
            </span>
            <span className="discord-channel">
              <i>#</i> homework-help
            </span>
            <div className="discord-userbar">
              <span>B</span>
              <div>
                <strong>BOB</strong>
                <small>Online</small>
              </div>
              <i>⚙</i>
            </div>
          </aside>

          <div className="discord-chat">
            <div className="discord-channel-header">
              <div>
                <span>#</span>
                <strong>9th-grade</strong>
                <i />
                <small>Class chat</small>
              </div>
              <p aria-hidden="true">◧ &nbsp; ♟ &nbsp; Search</p>
            </div>

            <div
              className="live-message-feed real-message-feed"
              aria-live="polite"
              ref={feedRef}
            >
              <p className="discord-day-rule">
                <span>Sample class · October 25, 2026</span>
              </p>
              <AnimatePresence initial={false}>
                {messages.map((message) => {
                  const isBot = message.author === "Memlo";
                  const avatar = isBot
                    ? "M"
                    : message.author === "pear"
                      ? "P"
                      : message.author === "eh--"
                        ? "E"
                        : "B";
                  return (
                    <motion.div
                      className={`sandbox-message ${isBot ? "bot-message real-bot-message" : "person-message"} ${message.private ? "ephemeral-message" : ""}`}
                      key={message.id}
                      initial={reduceMotion ? false : { opacity: 0, y: 12 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.3 }}
                    >
                      <span
                        className={`sandbox-avatar ${isBot ? "bot-avatar" : `person-avatar avatar-${message.author.replace("--", "eh")}`}`}
                      >
                        {avatar}
                      </span>
                      <div>
                        {message.replyTo && (
                          <div className="discord-reply-preview">
                            <span>{isBot ? "BOB" : "Memlo"}</span>
                            {message.replyTo}
                          </div>
                        )}
                        <p className="message-author">
                          <strong>{message.author}</strong>
                          {isBot && <em>APP</em>}
                          <small>{isBot ? "2:31 PM" : "2:30 PM"}</small>
                        </p>
                        <MessageText>{message.body}</MessageText>
                        {message.quiz && (
                          <>
                            <p className="quiz-instruction">
                              <em>
                                In Discord, /quiz is private by default. Use
                                Answer privately for feedback only you can see.
                              </em>
                            </p>
                            <button
                              className="discord-primary-button"
                              type="button"
                              onClick={() => setPrivateQuiz(message.id)}
                            >
                              ✏️ Answer privately
                            </button>
                          </>
                        )}
                        {message.private && (
                          <p className="ephemeral-note">
                            ◉ Only you can see this · Dismiss message
                          </p>
                        )}
                      </div>
                    </motion.div>
                  );
                })}
              </AnimatePresence>
              {typing && (
                <div className="discord-typing">
                  <span>M</span>
                  <p>
                    <strong>Memlo is typing</strong>
                    <i />
                    <i />
                    <i />
                  </p>
                </div>
              )}
            </div>

            <form className="discord-composer live-composer" onSubmit={submit}>
              <span aria-hidden="true">+</span>
              <label className="sr-only" htmlFor="memlo-message">
                Message 9th-grade
              </label>
              <input
                id="memlo-message"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder="Message #9th-grade"
                autoComplete="off"
                disabled={typing}
              />
              <span className="composer-icons" aria-hidden="true">
                GIF &nbsp; ☺
              </span>
              <button
                type="submit"
                disabled={!draft.trim() || typing}
                aria-label="Send message"
              >
                ↑
              </button>
            </form>
          </div>
        </div>

        <AnimatePresence>
          {privateQuiz !== null && (
            <motion.div
              className="discord-modal-layer"
              role="dialog"
              aria-modal="true"
              aria-labelledby="private-answer-title"
              initial={reduceMotion ? false : { opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
            >
              <motion.form
                ref={dialogRef}
                className="discord-modal"
                onSubmit={submitPrivateAnswer}
                initial={
                  reduceMotion ? false : { opacity: 0, y: 18, scale: 0.97 }
                }
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: 10, scale: 0.98 }}
              >
                <button
                  className="modal-close"
                  type="button"
                  onClick={() => setPrivateQuiz(null)}
                  aria-label="Close"
                >
                  ×
                </button>
                <h2 id="private-answer-title">Answer privately</h2>
                <p>
                  Preview only: this form shows example feedback, not live
                  grading. In Discord, AI grading processes answers through
                  Anthropic and replies privately.
                </p>
                <label htmlFor="private-quiz-answer">
                  Type your answer after each question
                </label>
                <textarea
                  id="private-quiz-answer"
                  value={privateDraft}
                  onChange={(event) => setPrivateDraft(event.target.value)}
                  rows={9}
                />
                <div>
                  <button type="button" onClick={() => setPrivateQuiz(null)}>
                    Cancel
                  </button>
                  <button type="submit">Send privately</button>
                </div>
              </motion.form>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
    </section>
  );
}

export function Reveal({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  const reduceMotion = useReducedMotion();
  return (
    <motion.div
      className={className}
      initial={reduceMotion ? false : { opacity: 0, y: 38 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.16 }}
      transition={{ duration: 0.65, ease: [0.22, 1, 0.36, 1] }}
    >
      {children}
    </motion.div>
  );
}
