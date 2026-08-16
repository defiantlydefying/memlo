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
  { id: 1, author: "pear", body: "ugh\nscience test tmr" },
  { id: 2, author: "eh--", body: "WHAT\nREALLY" },
  { id: 3, author: "pear", body: "😭😭\nyes bro\ndid u just find out" },
  { id: 4, author: "eh--", body: "omfg 💔\nim soooo cooked" },
];

const examples = [
  "what's due this week?",
  "quiz me",
  "science test tmr",
];

const quizText = [
  "Let's heat things up with a thermal energy quiz! 🔥",
  "",
  "1. What is thermal energy, and how is it related to the motion of particles in a substance?",
  "2. What's the difference between heat and temperature?",
  "3. Name and briefly describe the three main methods of heat transfer.",
].join("\n");

function replyFor(input: string): Omit<ChatMessage, "id" | "author"> | null {
  const value = input.toLowerCase();

  if (/quiz/.test(value)) {
    return { body: quizText, replyTo: input, quiz: true };
  }

  if (/what('s| is)? due|whats due|missed|what did i miss|catch me up/.test(value)) {
    return {
      body: "Science test tomorrow (7/28)! Also pear mentioned a study guide/worksheet link was coming, so keep an eye out for that if you haven't seen it yet.",
      replyTo: input,
    };
  }

  if (/https?:\/\//.test(value) || /study guide|worksheet|pdf|slides/.test(value)) {
    return {
      body: "🔗 Read study guide — Thermal energy. I've studied it. Try: @Memlo quiz us on it",
    };
  }

  if (/tomorrow|tmr|monday|tuesday|wednesday|thursday|friday|saturday|sunday|due|deadline|test|exam/.test(value) && !/@memlo/.test(value)) {
    return null;
  }

  return {
    body: "I haven't seen anything about that yet. Share the class notes or files here and I can remember them for everyone.",
    replyTo: input,
  };
}

function MessageText({ children }: { children: string }) {
  const parts = children.split(/(@Memlo)/gi);
  return (
    <p className="message-body">
      {parts.map((part, index) => /^@memlo$/i.test(part)
        ? <mark key={index}>{part}</mark>
        : <span key={index}>{part}</span>)}
    </p>
  );
}

export function InteractiveHero({ installUrl }: { installUrl: string }) {
  const reduceMotion = useReducedMotion();
  const [messages, setMessages] = useState<ChatMessage[]>(startingMessages);
  const [draft, setDraft] = useState("");
  const [typing, setTyping] = useState(false);
  const [memoryStatus, setMemoryStatus] = useState("Listening in approved channels");
  const [privateQuiz, setPrivateQuiz] = useState<number | null>(null);
  const [privateDraft, setPrivateDraft] = useState(
    "1. Thermal energy is the kinetic energy of particles.\n\n2. Heat is transferred between objects; temperature measures average kinetic energy.\n\n3. Radiation transfers heat through waves.",
  );
  const nextId = useRef(5);
  const feedRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const feed = feedRef.current;
    if (feed) feed.scrollTo({ top: feed.scrollHeight, behavior: reduceMotion ? "auto" : "smooth" });
  }, [messages, typing, reduceMotion]);

  const sendMessage = (message: string) => {
    const clean = message.trim();
    if (!clean || typing) return;

    const userId = nextId.current++;
    setMessages((current) => [...current, { id: userId, author: "BOB", body: clean }]);
    setDraft("");

    const response = replyFor(clean);
    if (!response) {
      setMemoryStatus("Remembered quietly · Science test tomorrow");
      window.setTimeout(() => setMemoryStatus("Listening in approved channels"), 3200);
      return;
    }

    setTyping(true);
    window.setTimeout(() => {
      const botId = nextId.current++;
      setMessages((current) => [...current, { id: botId, author: "Memlo", ...response }]);
      setTyping(false);
    }, reduceMotion ? 40 : 650);
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
    window.setTimeout(() => {
      const botId = nextId.current++;
      setMessages((current) => [...current, {
        id: botId,
        author: "Memlo",
        private: true,
        body: [
          "✅ 1. Yep! Thermal energy is the total kinetic energy of particles, and more thermal energy means faster particle motion.",
          "✅ 2. Correct — heat transfers between objects; temperature measures the average kinetic energy of particles.",
          "❌ 3. You're missing conduction (direct contact) and convection (fluid currents). Radiation transfers through electromagnetic waves.",
          "",
          "2/3 — solid work, just brush up on the heat transfer methods!",
        ].join("\n"),
      }]);
      setTyping(false);
    }, reduceMotion ? 40 : 650);
  };

  return (
    <section className="chat-hero" id="demo" aria-labelledby="product-title">
      <motion.div className="chat-hero-copy" initial={reduceMotion ? false : { opacity: 0, y: 24 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: .65, ease: [0.22, 1, 0.36, 1] }}>
        <div>
          <p className="hero-kicker inverse"><span className="signal-dot" aria-hidden="true" />Memory for class Discords</p>
          <h1 id="product-title">The message passes.<strong>The memory stays.</strong></h1>
        </div>
        <div className="chat-hero-intro">
          <p>Memlo quietly remembers what matters, reads the study material your class shares, and answers when someone needs help.</p>
          <div className="hero-actions">
            <motion.a className="button-link hero-primary" href={installUrl} target="_blank" rel="noreferrer" whileHover={{ y: -3 }} whileTap={{ scale: .98 }}>Add Memlo to Discord <span aria-hidden="true">↗</span></motion.a>
            <a className="button-link hero-secondary" href="#how-it-works">How it works <span aria-hidden="true">↓</span></a>
          </div>
          <p className="hero-proof inverse-proof">Admin-selected channels only · Nothing is read before setup</p>
        </div>
      </motion.div>

      <motion.div className="live-demo-shell" initial={reduceMotion ? false : { opacity: 0, y: 36, scale: .985 }} animate={{ opacity: 1, y: 0, scale: 1 }} transition={{ duration: .75, delay: .12, ease: [0.22, 1, 0.36, 1] }}>
        <div className="live-demo-label">
          <div><span className="live-dot" /><strong>Try the real Memlo flow</strong></div>
          <p>{memoryStatus}</p>
        </div>

        <div className="prompt-shelf" aria-label="Example messages">
          <span>Try: “what’s due this week?” or “quiz me”</span>
          {examples.map((example) => <button key={example} type="button" onClick={() => sendMessage(example)} disabled={typing}>{example}</button>)}
        </div>

        <div className="discord-preview live-discord accurate-discord" aria-label="Interactive simulation based on the real Memlo Discord demo">
          <aside className="discord-server-rail" aria-label="Server rail">
            <span className="discord-home" aria-hidden="true">⌁</span>
            <span className="active-server">WF</span>
            <span>+</span>
            <span>⌕</span>
          </aside>

          <aside className="discord-channels" aria-label="Class server channels">
            <div className="server-name"><strong>west ford hs study chat</strong><span>⌄</span></div>
            <span className="server-tool">▣ &nbsp; Events</span>
            <p>INFORMATION</p>
            <span className="discord-channel"><i>#</i> welcome-and-rules</span>
            <span className="discord-channel"><i>#</i> notes-resources</span>
            <p>TEXT CHANNELS</p>
            <span className="discord-channel active"><i>#</i> 9th-grade</span>
            <span className="discord-channel"><i>#</i> 10th-grade</span>
            <span className="discord-channel"><i>#</i> 11th-grade</span>
            <span className="discord-channel"><i>#</i> 12th-grade</span>
            <span className="discord-channel"><i>#</i> general</span>
            <span className="discord-channel"><i>#</i> homework-help</span>
            <div className="discord-userbar"><span>B</span><div><strong>BOB</strong><small>Online</small></div><i>⚙</i></div>
          </aside>

          <div className="discord-chat">
            <div className="discord-channel-header">
              <div><span>#</span><strong>9th-grade</strong><i /><small>Class chat</small></div>
              <p aria-hidden="true">◧ &nbsp; ♟ &nbsp; Search</p>
            </div>

            <div className="live-message-feed real-message-feed" aria-live="polite" ref={feedRef}>
              <p className="discord-day-rule"><span>July 27, 2026</span></p>
              <AnimatePresence initial={false}>
                {messages.map((message) => {
                  const isBot = message.author === "Memlo";
                  const avatar = isBot ? "M" : message.author === "pear" ? "P" : message.author === "eh--" ? "E" : "B";
                  return (
                    <motion.div className={`sandbox-message ${isBot ? "bot-message real-bot-message" : "person-message"} ${message.private ? "ephemeral-message" : ""}`} key={message.id} initial={reduceMotion ? false : { opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: .3 }}>
                      <span className={`sandbox-avatar ${isBot ? "bot-avatar" : `person-avatar avatar-${message.author.replace("--", "eh")}`}`}>{avatar}</span>
                      <div>
                        {message.replyTo && <div className="discord-reply-preview"><span>{isBot ? "BOB" : "Memlo"}</span>{message.replyTo}</div>}
                        <p className="message-author"><strong>{message.author}</strong>{isBot && <em>APP</em>}<small>{isBot ? "2:31 PM" : "2:30 PM"}</small></p>
                        <MessageText>{message.body}</MessageText>
                        {message.quiz && (
                          <>
                            <p className="quiz-instruction"><em>Hit the button for private feedback, or just reply with your answers.</em></p>
                            <button className="discord-primary-button" type="button" onClick={() => setPrivateQuiz(message.id)}>✏️ Answer privately</button>
                          </>
                        )}
                        {message.private && <p className="ephemeral-note">◉ Only you can see this · Dismiss message</p>}
                      </div>
                    </motion.div>
                  );
                })}
              </AnimatePresence>
              {typing && <div className="discord-typing"><span>M</span><p><strong>Memlo is typing</strong><i /><i /><i /></p></div>}
            </div>

            <form className="discord-composer live-composer" onSubmit={submit}>
              <span aria-hidden="true">+</span>
              <label className="sr-only" htmlFor="memlo-message">Message 9th-grade</label>
              <input id="memlo-message" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Message #9th-grade" autoComplete="off" disabled={typing} />
              <span className="composer-icons" aria-hidden="true">GIF &nbsp; ☺</span>
              <button type="submit" disabled={!draft.trim() || typing} aria-label="Send message">↑</button>
            </form>
          </div>
        </div>

        <AnimatePresence>
          {privateQuiz !== null && (
            <motion.div className="discord-modal-layer" role="dialog" aria-modal="true" aria-labelledby="private-answer-title" initial={reduceMotion ? false : { opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
              <motion.form className="discord-modal" onSubmit={submitPrivateAnswer} initial={reduceMotion ? false : { opacity: 0, y: 18, scale: .97 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 10, scale: .98 }}>
                <button className="modal-close" type="button" onClick={() => setPrivateQuiz(null)} aria-label="Close">×</button>
                <h2 id="private-answer-title">Answer privately</h2>
                <p>Only you and Memlo will see your answers.</p>
                <label htmlFor="private-quiz-answer">Type your answer after each question</label>
                <textarea id="private-quiz-answer" value={privateDraft} onChange={(event) => setPrivateDraft(event.target.value)} rows={9} />
                <div><button type="button" onClick={() => setPrivateQuiz(null)}>Cancel</button><button type="submit">Send privately</button></div>
              </motion.form>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
    </section>
  );
}

export function Reveal({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  const reduceMotion = useReducedMotion();
  return (
    <motion.div className={className} initial={reduceMotion ? false : { opacity: 0, y: 38 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, amount: .16 }} transition={{ duration: .65, ease: [0.22, 1, 0.36, 1] }}>
      {children}
    </motion.div>
  );
}
