# ClassBrain

An AI bot that lives in your class group chat and becomes its memory —
tracks deadlines, remembers what the teacher said, and reminds the group
before things are due. (YC Fall 2026 application project.)

## Setup (do this once)

1. Install the libraries:
   ```
   pip install -r requirements.txt
   ```

2. Get a Discord bot token:
   - https://discord.com/developers/applications → New Application → Bot → Reset Token
   - Turn ON "MESSAGE CONTENT INTENT" under Privileged Gateway Intents
   - Invite the bot to a test server (OAuth2 → URL Generator → scope: bot →
     permissions: Read Messages, Send Messages)

3. Get an Anthropic API key: https://console.anthropic.com → API Keys

4. Put both keys in your terminal (don't paste them into any file):
   ```
   export DISCORD_TOKEN="your-discord-token"
   export ANTHROPIC_API_KEY="your-anthropic-key"
   ```

5. Run it:
   ```
   python bot.py
   ```

## Where things are

- `bot.py` — the whole bot
- `classbrain.db` — the memory (SQLite, created on first run)
- `requirements.txt` — the libraries to install

## Build order

- [x] Day 1: Bot connects, sees messages
- [x] Day 2: Haiku flags important messages, prints JSON
- [x] Day 3: Save to SQLite, add "@bot what's due?"
- [x] Day 4: Proactive reminders before deadlines
- [x] Bonus: reads uploaded images / PDFs / text files and quizzes from them
- [x] Bonus: reads **links** people drop (Google Docs, PDFs, web pages) as material
- [ ] Day 5: Polish the feel, test with a friend  ← **you are here**

Nothing above Day 5 has been run end-to-end against a real Discord server yet.

## Later ideas (v2 — do NOT build yet)

- **Class intel / vibes.** Beyond deadlines and announcements, the bot notices
  softer signals ("Ms. Smith grades essays hard," "everyone lock in") and
  remembers them. It stays quiet in the chat and only surfaces this when asked
  ("what should I know about Ms. Smith?"). A plain deadline-tracker can't do
  this — it's the compounding-context moat made real. Keep the MVP tight first.

- **Ingest more than chat text.** The bot's memory shouldn't stop at what people
  type. It should also pull from:
  - **Uploaded files** — images/photos of the board, study guides, PDFs, slides
    (Claude reads images directly, so a photo of a syllabus becomes deadlines).
    ✅ built.
  - **Links dropped in chat** — Google Docs/Slides/Sheets, class websites, PDFs.
    ✅ built. Any link posted is fetched, and Haiku judges whether it's actually
    study material before saving — so a meme or a game invite is dropped, an
    educational page or rubric is kept. A link and a file in the same message are
    quizzed together. See `fetch_link` / `read_links` in `bot.py`.
  - **The open internet** — look things up when the chat doesn't have the answer.
    Still v2 — this is real browsing, a bigger thing than reading a shared link.

  This is what turns it from a deadline tracker into the class's actual brain:
  ask "what's on the test?" and it answers from the study guide someone posted
  three weeks ago.

  Known limits of link-reading as built:
  - **Google Docs must be link-shared** ("Anyone with the link"). A private doc
    hits a sign-in wall; the bot notices and asks the poster to fix the sharing.
  - **Videos** (YouTube etc.) — the bot reads the title and description, *not* the
    transcript, so "quiz us on that video" leans on the blurb, not the content.
    Real transcripts need a separate API (a later job).
  - **Quizlet blocks scrapers and Blooket has no public API**, so those links
    mostly won't open — they just get skipped quietly.
  - Fetching needs `SSL_CERT_FILE` set (it's in the setup steps above); without it
    every https fetch fails certificate verification on a Mac.

## Deadline

YC Fall 2026 — apply by **July 27, 2026, 8pm PT**.
