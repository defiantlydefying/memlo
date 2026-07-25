"""
ClassBrain — the magic loop
A Discord bot that is the memory of your class group chat.

  1. LISTENS to every message and, using Claude Haiku, decides if it's worth
     remembering (a deadline or a teacher announcement). Saves it to a database.
  2. ANSWERS when you @-mention it ("@ClassBrain what's due?") using Claude Sonnet,
     pulling from everything it has remembered.
  3. REMINDS the group on its own when something is due soon — nobody has to ask.
     This is the "it just knew" moment.

------------------------------------------------------------------
SETUP (see README.md for the full walkthrough):
  pip3 install -r requirements.txt
  export DISCORD_TOKEN="..."
  export ANTHROPIC_API_KEY="..."
  export SSL_CERT_FILE=$(python3 -c "import certifi; print(certifi.where())")
  python3 bot.py
------------------------------------------------------------------
"""

import os
import re
import html
import json
import base64
import asyncio
import sqlite3
import ipaddress
from urllib.parse import urlparse
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import discord
import aiohttp
from discord.ext import tasks
import anthropic

# --- Settings you can tweak ----------------------------------------

DETECTOR_MODEL = "claude-haiku-4-5"   # cheap, runs on every message
ANSWER_MODEL = "claude-sonnet-5"      # smarter, only runs when asked or reminding
DB_PATH = "classbrain.db"             # the bot's memory file

# The only image formats Claude can read. An iPhone photo uploaded from a Mac
# arrives as image/heic and has to be converted first.
CLAUDE_IMAGE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")
REMIND_WITHIN_DAYS = 2                # remind about anything due today..+2 days

# --- Reading links people drop -------------------------------------
# A shared link is study material too: a Google Doc, a rubric on the class site, a
# PDF, an educational video. We fetch whatever's linked, let Haiku judge whether it
# was actually worth studying (vs a meme or a game invite), and file the keepers the
# same way an uploaded file is filed. See fetch_link / read_links below.
FETCH_TIMEOUT = 15                    # seconds to wait on a single link
MAX_FETCH_BYTES = 10 * 1024 * 1024    # stop reading a link after 10 MB
MAX_LINKS_PER_MESSAGE = 5             # don't let one message fan out to dozens of fetches
MAX_PAGE_CHARS = 20000               # how much of a web page's text we hand to Claude
# A real browser User-Agent: some sites answer a bare Python client with 403.
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
# URLs inside message text. Stops at whitespace and the brackets Discord/markdown wrap
# links in; trailing sentence punctuation is trimmed off separately in extract_urls.
URL_RE = re.compile(r'https?://[^\s<>()\[\]{}"\'|\\^`]+', re.IGNORECASE)

# Discord never tells a bot what timezone anyone is in, so reminders can't adapt
# per-person — they go out on one clock, set here. Change this to your class's
# timezone (any IANA name: "America/Los_Angeles", "Europe/London", ...).
CLASS_TIMEZONE = ZoneInfo(os.environ.get("CLASS_TIMEZONE", "America/New_York"))
REMIND_AT = [                         # the bot speaks up at these times, daily
    time(hour=6, minute=0, tzinfo=CLASS_TIMEZONE),
    time(hour=15, minute=0, tzinfo=CLASS_TIMEZONE),
]
QUIET_HOURS_AFTER_SAVING = 1          # don't parrot back something just announced


# --- The clock -----------------------------------------------------
# Everything in the bot runs on the class's clock, never the server's. Left as
# date.today(), a bot hosted in UTC would roll over to "tomorrow" at 8pm Eastern —
# so an essay due tonight silently drops out of "what's due" mid-evening, and the
# date handed to Claude for parsing "due Thursday" is off by one.
#
# These return NAIVE datetimes deliberately: timestamps are stored as ISO strings
# and compared as strings in SQL, so adding a "-04:00" suffix to new rows would
# make them sort inconsistently against every row already in the database. The
# wall-clock reading is simply always the class's.

def class_now():
    return datetime.now(CLASS_TIMEZONE).replace(tzinfo=None)


def class_today():
    return class_now().date()


# --- Clients -------------------------------------------------------

claude = anthropic.Anthropic()

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

_view_registered = False   # so a reconnect doesn't re-register the quiz button


# --- The database (the bot's memory) -------------------------------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row   # lets us read columns by name
    return conn


def init_db():
    conn = db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deadlines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            summary TEXT,
            due_date TEXT,          -- "YYYY-MM-DD"
            author TEXT,
            channel_id INTEGER,
            link TEXT,
            reminded INTEGER DEFAULT 0,
            created_at TEXT         -- when we heard about it, ISO 8601
        )
    """)
    # Migration for databases created before created_at / audience existed.
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(deadlines)")]
    if "created_at" not in columns:
        conn.execute("ALTER TABLE deadlines ADD COLUMN created_at TEXT")
    if "audience" not in columns:
        conn.execute("ALTER TABLE deadlines ADD COLUMN audience TEXT")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            summary TEXT,
            original_text TEXT,
            author TEXT,
            channel_id INTEGER,
            link TEXT,
            audience TEXT           -- who it's for ("7th Grade"), or "" = everyone
        )
    """)
    note_cols = [row["name"] for row in conn.execute("PRAGMA table_info(notes)")]
    if "audience" not in note_cols:
        conn.execute("ALTER TABLE notes ADD COLUMN audience TEXT")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS quizzes (
            message_id INTEGER PRIMARY KEY,   -- the bot's own quiz message
            channel_id INTEGER,
            quiz_text TEXT,       -- questions AND answers, so we can grade replies
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS active_categories (
            category_id INTEGER PRIMARY KEY,   -- a Discord category the bot tracks
            guild_id INTEGER,
            name TEXT,             -- category name at activation, for status display
            activated_by TEXT,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            digest TEXT,          -- Claude's study digest of the uploaded file
            author TEXT,
            channel_id INTEGER,
            link TEXT,
            topic TEXT,           -- short label ("War of 1812"), for picking quiz material
            quiz_group TEXT,      -- materials sharing this are quizzed together
            created_at TEXT
        )
    """)
    # Migration for databases created before topic/quiz_group/created_at existed.
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(materials)")]
    for col in ("topic", "quiz_group", "created_at"):
        if col not in columns:
            conn.execute(f"ALTER TABLE materials ADD COLUMN {col} TEXT")
    # Group old material the same way new material is grouped: by the message it was
    # uploaded on. A jump_url ends in the message id, so files posted together —
    # e.g. two pages of the same chapter — land in one group retroactively. Anything
    # without a usable link falls back to a group of its own.
    for row in conn.execute(
            "SELECT id, link FROM materials WHERE quiz_group IS NULL").fetchall():
        message_id = (row["link"] or "").rstrip("/").rsplit("/", 1)[-1]
        group = f"msg-{message_id}" if message_id.isdigit() else f"legacy-{row['id']}"
        conn.execute("UPDATE materials SET quiz_group = ? WHERE id = ?",
                     (group, row["id"]))
    # Backfill topics for material ingested before topics existed. Every digest opens
    # with a markdown heading ("# Civil War Study Digest"), which is the label we
    # would have asked for anyway — without this, old uploads have a blank topic and
    # can never be picked by subject, only by being the most recent thing.
    for row in conn.execute(
            "SELECT id, digest, filename FROM materials WHERE topic IS NULL").fetchall():
        conn.execute("UPDATE materials SET topic = ? WHERE id = ?",
                     (_topic_from_digest(row["digest"], row["filename"]), row["id"]))
    conn.commit()
    conn.close()


def _topic_from_digest(digest, filename):
    """First markdown heading of a digest, tidied into a subject label."""
    for line in (digest or "").splitlines():
        s = line.strip()
        if s.startswith("#"):
            s = s.lstrip("#").strip()
            for noise in ("Study Digest:", "Study Digest", "Quiz — Study Digest",
                          "— Study Digest", "Study Guide:"):
                s = s.replace(noise, "").strip(" —-:")
            if s:
                return s
    return filename


def save_deadline(summary, due_date, author, channel_id, link, audience=""):
    conn = db()
    conn.execute(
        "INSERT INTO deadlines (summary, due_date, author, channel_id, link, "
        "created_at, audience) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (summary, due_date, author, channel_id, link, class_now().isoformat(), audience),
    )
    conn.commit()
    conn.close()


def save_note(summary, original_text, author, channel_id, link, audience=""):
    conn = db()
    conn.execute(
        "INSERT INTO notes (summary, original_text, author, channel_id, link, audience) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (summary, original_text, author, channel_id, link, audience),
    )
    conn.commit()
    conn.close()


def get_upcoming_deadlines(channel_id):
    today = class_today().isoformat()
    conn = db()
    rows = conn.execute(
        "SELECT * FROM deadlines WHERE channel_id = ? AND due_date >= ? "
        "ORDER BY due_date",
        (channel_id, today),
    ).fetchall()
    conn.close()
    return rows


def get_notes(channel_id):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM notes WHERE channel_id = ? ORDER BY id DESC LIMIT 20",
        (channel_id,),
    ).fetchall()
    conn.close()
    return rows


def save_material(filename, digest, author, channel_id, link, topic, quiz_group):
    conn = db()
    cur = conn.execute(
        "INSERT INTO materials (filename, digest, author, channel_id, link, topic, "
        "quiz_group, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (filename, digest, author, channel_id, link, topic, quiz_group,
         class_now().isoformat()),
    )
    conn.commit()
    material_id = cur.lastrowid
    conn.close()
    return material_id


def get_materials(channel_id):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM materials WHERE channel_id = ? ORDER BY id DESC LIMIT 10",
        (channel_id,),
    ).fetchall()
    conn.close()
    return rows


def get_material_groups(channel_id):
    """One row per quiz group in this channel: the group id, the topics it covers,
    the filenames in it, and how recent it is. This is the menu `pick_material_group`
    chooses from — small enough to hand to Haiku without shipping whole digests."""
    conn = db()
    rows = conn.execute(
        "SELECT quiz_group, "
        "       GROUP_CONCAT(DISTINCT topic) AS topics, "
        "       GROUP_CONCAT(filename) AS filenames, "
        "       MAX(id) AS newest "
        "FROM materials WHERE channel_id = ? "
        "GROUP BY quiz_group ORDER BY newest DESC LIMIT 10",
        (channel_id,),
    ).fetchall()
    conn.close()
    return rows


def get_materials_in_group(quiz_group):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM materials WHERE quiz_group = ? ORDER BY id", (quiz_group,)
    ).fetchall()
    conn.close()
    return rows


def merge_quiz_groups(source_group, target_group):
    """Fold one group into another so a quiz drawn from either covers both."""
    conn = db()
    conn.execute("UPDATE materials SET quiz_group = ? WHERE quiz_group = ?",
                 (target_group, source_group))
    conn.commit()
    conn.close()


def save_quiz(message_id, channel_id, quiz_text):
    conn = db()
    conn.execute(
        "INSERT OR REPLACE INTO quizzes (message_id, channel_id, quiz_text, created_at) "
        "VALUES (?, ?, ?, ?)",
        (message_id, channel_id, quiz_text, class_now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_quiz(message_id):
    """The quiz we posted as this message, or None if that isn't one of ours."""
    conn = db()
    row = conn.execute(
        "SELECT quiz_text FROM quizzes WHERE message_id = ?", (message_id,)
    ).fetchone()
    conn.close()
    return row["quiz_text"] if row else None


def get_quiz_questions(message_id):
    """Just the questions for a quiz — NEVER the answer key. The stored blob is
    "QUESTIONS ...\\n\\nANSWER KEY ...", so we cut everything from ANSWER KEY on.
    Used to fill the private answer popup so people can see what they're answering."""
    blob = get_quiz(message_id)
    if not blob:
        return None
    questions = blob.split("ANSWER KEY", 1)[0]
    return questions.replace("QUESTIONS (posted to the chat):", "", 1).strip()


def get_due_soon_unreminded():
    """Deadlines due within the next few days that we haven't reminded about yet.

    Skips anything announced in the last hour — if someone posts a deadline at
    2:55pm, echoing it back at the 3pm reminder reads as the bot not listening.
    The "it just knew" moment only lands once everyone has forgotten.
    """
    today = class_today()
    window = (today + timedelta(days=REMIND_WITHIN_DAYS)).isoformat()
    quiet_until = (class_now() - timedelta(hours=QUIET_HOURS_AFTER_SAVING)).isoformat()
    conn = db()
    rows = conn.execute(
        "SELECT * FROM deadlines WHERE reminded = 0 AND due_date >= ? AND due_date <= ? "
        "AND (created_at IS NULL OR created_at <= ?)",
        (today.isoformat(), window, quiet_until),
    ).fetchall()
    conn.close()
    return rows


def mark_reminded(deadline_id):
    conn = db()
    conn.execute("UPDATE deadlines SET reminded = 1 WHERE id = ?", (deadline_id,))
    conn.commit()
    conn.close()


# Everything a person contributes is attributed by the display name we stored when we
# saw it (there's no user id in the schema). So "forget me" matches on display name in
# the current channel — which is the common case, but its limits are honest ones: it
# won't catch data saved under a since-changed name, and two people sharing a display
# name would match each other. Quizzes aren't attributed to anyone, so nothing there
# to forget. Both functions scope to one channel because that's how all memory is
# scoped — the schema has no server id to widen to.
_USER_TABLES = ("deadlines", "notes", "materials")


def count_user_data(author, channel_id):
    conn = db()
    counts = {t: conn.execute(
        f"SELECT COUNT(*) FROM {t} WHERE author = ? AND channel_id = ?",
        (author, channel_id)).fetchone()[0] for t in _USER_TABLES}
    conn.close()
    return counts


def forget_user(author, channel_id):
    """Delete everything this person contributed to this channel. Returns per-table
    counts of what was actually removed."""
    conn = db()
    counts = {}
    for t in _USER_TABLES:
        cur = conn.execute(
            f"DELETE FROM {t} WHERE author = ? AND channel_id = ?",
            (author, channel_id))
        counts[t] = cur.rowcount
    conn.commit()
    conn.close()
    return counts


# --- Which channels the bot tracks (grade/class scoping) -----------
# At school scale the bot must NOT listen everywhere: running the detector on every
# message in #general/#memes is wasted cost and files chatter as class notes. An admin
# turns on a whole category ("7th Grade") and the bot tracks every channel inside it.
# Everything the bot remembers is already scoped per channel_id, so one channel per
# class keeps 7th-grade science and 8th-grade math completely separate on their own.
#
# Default when a server has activated NOTHING: track every channel — so a small,
# single-class server (like a test server) works out of the box, and gating only
# narrows things once an admin opts specific categories in.

def activate_category(category_id, guild_id, name, activated_by):
    conn = db()
    conn.execute(
        "INSERT OR REPLACE INTO active_categories "
        "(category_id, guild_id, name, activated_by, created_at) VALUES (?, ?, ?, ?, ?)",
        (category_id, guild_id, name, activated_by, class_now().isoformat()))
    conn.commit()
    conn.close()


def deactivate_category(category_id):
    conn = db()
    conn.execute("DELETE FROM active_categories WHERE category_id = ?", (category_id,))
    conn.commit()
    conn.close()


def get_active_categories(guild_id):
    """Set of category ids this server has opted in. Empty set means 'track all'."""
    conn = db()
    rows = conn.execute(
        "SELECT category_id FROM active_categories WHERE guild_id = ?",
        (guild_id,)).fetchall()
    conn.close()
    return {r["category_id"] for r in rows}


def get_active_category_rows(guild_id):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM active_categories WHERE guild_id = ? ORDER BY name",
        (guild_id,)).fetchall()
    conn.close()
    return rows


def is_tracked(guild_id, category_id):
    """Should the bot listen in a channel with this category? See the note above:
    no opted-in categories -> track everything; otherwise only opted-in ones."""
    if guild_id is None:
        return False   # a DM: no class context to scope to
    active = get_active_categories(guild_id)
    if not active:
        return True
    return category_id in active


# --- Claude: detect, answer, remind --------------------------------

def _extract_json(text):
    """Claude sometimes wraps JSON in ```json fences. Grab { ... } safely."""
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    return json.loads(text)


def analyze_message(message_text: str, poster_roles=None) -> dict:
    """Haiku: is this message worth remembering?

    poster_roles are the sender's own roles. In a student server nobody pings a grade
    role or writes "7th grade" every time — they just say "we have a test Friday". So
    when someone describes their OWN class's deadline and one of their roles names a
    grade, we tag it with that grade. General news ("no school Monday") stays untagged.
    """
    today = class_today().isoformat()
    roles = ", ".join(poster_roles) if poster_roles else "(none)"
    prompt = f"""Today's date is {today}.

You are the memory of a school class group chat. Read ONE message and decide
whether it is worth remembering. Worth remembering = a homework/assignment
deadline, a test/quiz date, a teacher announcement, or important instructions
(e.g. "notes are allowed"). Everyday chatter, jokes, and memes are NOT important.

Message: "{message_text}"

WHO IS IT FOR? Two ways to tell:
1. If the message itself names a grade/class/section/group ("7th grade", "Period 3",
   "AP Bio") — put that in "audience". Don't guess; blank if it doesn't say.
2. Otherwise, the sender has these roles: {roles}. If this is a deadline/assignment
   the sender is describing for THEIR OWN class (first-person: "we have", "our", "my",
   "I have a test") AND one of their roles clearly names a grade/class/section, put
   that exact role in "poster_grade". If the message names a DIFFERENT group, is
   general/school-wide news, or you're unsure, leave "poster_grade" blank.
Prefer "audience" when the message states it outright; "poster_grade" is only the
fallback for when it doesn't.

Respond with ONLY a JSON object in exactly this shape:
{{"is_important": true or false,
  "type": "deadline" | "announcement" | "other",
  "summary": "short plain-language summary, or empty string",
  "due_date": "YYYY-MM-DD if there is a clear date, otherwise empty string",
  "audience": "the grade/class/section the message names, or empty string",
  "poster_grade": "the sender's own grade role, only for their own-class deadlines, else empty string"}}"""

    response = claude.messages.create(
        model=DETECTOR_MODEL,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = next(block.text for block in response.content if block.type == "text")
    try:
        return _extract_json(raw)
    except json.JSONDecodeError:
        print(f"  (couldn't parse response: {raw!r})")
        return {"is_important": False}


def ingest_attachment(filename, media_type, data_bytes, is_text) -> dict:
    """Claude reads an uploaded study file (PDF, image, or text) and returns
    {"topic": ..., "digest": ...} — a detailed study digest we can quiz from later,
    plus a short subject label used to decide which material a quiz should draw on.

    The topic comes back as a "TOPIC:" first line rather than as JSON: the digest is
    long, multi-line markdown, and asking for it inside a JSON string is where the
    model most often breaks the quoting.
    """
    instruction = (
        "A student shared this study material with the class. Pull out a detailed, "
        "organized study digest: key concepts, definitions, formulas, dates, and the "
        "topics most likely to be on a test. Be thorough but tidy.\n\n"
        "Start your reply with a single line of the form:\n"
        "TOPIC: <the subject, at most six words, e.g. 'War of 1812' or "
        "'Stoichiometry — limiting reagents'>\n"
        "Then a blank line, then the digest itself. Output nothing else."
    )

    if is_text:
        text = data_bytes.decode("utf-8", errors="ignore")
        content = [{"type": "text", "text": f"{instruction}\n\nMATERIAL:\n{text}"}]
    else:
        b64 = base64.standard_b64encode(data_bytes).decode("utf-8")
        if media_type == "application/pdf":
            block = {"type": "document",
                     "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}
        else:  # an image
            block = {"type": "image",
                     "source": {"type": "base64", "media_type": media_type, "data": b64}}
        content = [block, {"type": "text", "text": instruction}]

    response = claude.messages.create(
        model=ANSWER_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": content}],
    )
    raw = next(block.text for block in response.content if block.type == "text").strip()

    topic = ""
    if raw.upper().startswith("TOPIC:"):
        first, _, rest = raw.partition("\n")
        topic = first.split(":", 1)[1].strip()
        raw = rest.strip()
    return {"topic": topic or filename, "digest": raw}


# --- Reading a link's contents (Google Docs, PDFs, web pages) -------
# The goal is narrow on purpose: not "browse the web", but "someone dropped a study
# material as a link instead of a file". So we fetch the one URL, turn it into the same
# text/PDF/image bytes that ingest_attachment already knows how to read, and reuse that
# whole path. Everything Claude-facing is unchanged; this is just a new way in.

def extract_urls(text):
    """Every http(s) URL in a message, de-duplicated, capped, with trailing sentence
    punctuation trimmed ("...see docs.google.com/x." shouldn't fetch a URL ending in
    a period). Discord's <link> embed-suppression brackets are excluded by URL_RE."""
    urls = []
    for raw in URL_RE.findall(text or ""):
        url = raw.rstrip('.,;:!?)]}\'"')
        if url not in urls:
            urls.append(url)
    return urls[:MAX_LINKS_PER_MESSAGE]


def _is_blocked_host(host):
    """Refuse links that point back at wherever the bot is hosted. People paste public
    URLs, so this only ever fires on abuse — a private/loopback address, or a name that
    obviously isn't a real site. It's a coarse guard: a public hostname that *resolves*
    to a private IP would still get through (we don't resolve DNS here), which is an
    accepted limit for a class bot rather than a hardened fetcher."""
    host = (host or "").lower().strip("[]")   # strip the [] IPv6 literals arrive in
    if not host or host == "localhost" or host.endswith((".local", ".internal")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False   # an ordinary hostname, not a bare IP — allow it
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def _google_export(url):
    """Turn a Google Docs/Slides/Sheets/Drive share link into a direct-download URL,
    since the human-facing /edit page is a JavaScript app with none of the content in
    the HTML. Returns (download_url, kind) where kind is 'text', 'pdf', or 'auto'
    (let the response's content-type decide), or None if it isn't a Google link.

    Requires the doc to be link-shared ('Anyone with the link'); a private doc answers
    the export URL with an HTML sign-in page, which fetch_link detects and reports."""
    m = re.search(r'docs\.google\.com/document/d/([\w-]+)', url)
    if m:
        return f"https://docs.google.com/document/d/{m.group(1)}/export?format=txt", "text"
    m = re.search(r'docs\.google\.com/spreadsheets/d/([\w-]+)', url)
    if m:
        return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv", "text"
    m = re.search(r'docs\.google\.com/presentation/d/([\w-]+)', url)
    if m:
        return f"https://docs.google.com/presentation/d/{m.group(1)}/export/pdf", "pdf"
    m = re.search(r'drive\.google\.com/file/d/([\w-]+)', url)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}", "auto"
    return None


def _meta(html_str, key):
    """A <meta property/name="key"> tag's content, matched without assuming attribute
    order (real pages put content before property as often as after)."""
    for tag in re.findall(r"<meta\b[^>]*>", html_str, re.I):
        if re.search(rf'(?:property|name)\s*=\s*["\']{re.escape(key)}["\']', tag, re.I):
            m = re.search(r'content\s*=\s*["\'](.*?)["\']', tag, re.I | re.S)
            if m:
                return html.unescape(m.group(1)).strip()
    return ""


def _html_to_text(raw_bytes):
    """A web page's readable text. Leads with <title> and the og:/meta description,
    because JavaScript-heavy pages (a YouTube video, say) carry their real subject
    there while the body is mostly script — so a bare tag-strip would miss it. Then the
    body with scripts/styles/tags removed. Returns (title, text)."""
    doc = raw_bytes.decode("utf-8", errors="ignore")
    m = re.search(r"<title[^>]*>(.*?)</title>", doc, re.I | re.S)
    title = html.unescape(re.sub(r"\s+", " ", m.group(1)).strip()) if m else ""
    lead = [t for t in (title, _meta(doc, "og:title"), _meta(doc, "og:description"),
                        _meta(doc, "description")) if t]
    body = re.sub(r"<(script|style|noscript)\b[^>]*>.*?</\1>", " ", doc, flags=re.I | re.S)
    body = html.unescape(re.sub(r"<[^>]+>", " ", body))
    body = re.sub(r"\s+", " ", body).strip()
    # De-dup: the title/description almost always reappear in the body verbatim.
    text = "\n".join(dict.fromkeys(lead + [body]))
    return title, text[:MAX_PAGE_CHARS]


def _label_from_url(url):
    """A short human name for a link when the page has no title: last path segment,
    else the hostname."""
    parsed = urlparse(url)
    tail = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    return (tail or parsed.hostname or url)[:80]


async def _read_capped(resp):
    """Read a response body but stop at MAX_FETCH_BYTES, so a link to a huge file can't
    make the bot download all of it."""
    chunks, total = [], 0
    async for chunk in resp.content.iter_chunked(65536):
        chunks.append(chunk)
        total += len(chunk)
        if total >= MAX_FETCH_BYTES:
            break
    return b"".join(chunks)


async def fetch_link(url):
    """Fetch one URL and normalise it into what ingest_attachment expects. Returns a
    dict {label, media_type, data(bytes), is_text, snippet} on success, or {"error": ...}.
    'snippet' is a short text peek used only to judge whether it's study material.

    Never raises: every failure (blocked host, timeout, 404, a Google doc that isn't
    shared, a file type we can't read) comes back as an "error" the caller decides how
    loud to be about."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or _is_blocked_host(parsed.hostname):
        return {"error": "blocked"}

    fetch_from, forced = url, None
    google = _google_export(url)
    if google:
        fetch_from, forced = google

    try:
        timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT)
        headers = {"User-Agent": BROWSER_UA}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(fetch_from, allow_redirects=True) as resp:
                if resp.status != 200:
                    return {"error": f"http {resp.status}"}
                ctype = (resp.content_type or "").lower()
                data = await _read_capped(resp)
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        return {"error": f"fetch failed: {err}"}

    label = _label_from_url(url)

    # A Google export that comes back as HTML is the "you need access" sign-in page —
    # the doc isn't link-shared. Worth telling the poster about; they can fix it.
    if forced in ("text", "pdf") and ctype.startswith("text/html"):
        return {"error": "private"}

    is_html = ctype.startswith("text/html")
    is_pdf = ctype == "application/pdf" or (forced == "pdf")
    is_image = ctype.startswith("image/")
    is_text = (ctype.startswith("text/") and not is_html) or forced == "text"

    if is_html:
        title, text = _html_to_text(data)
        return {"label": (title or label)[:80], "media_type": None,
                "data": text.encode("utf-8"), "is_text": True, "snippet": text[:1500]}
    if is_pdf:
        return {"label": label, "media_type": "application/pdf",
                "data": data, "is_text": False, "snippet": "(a PDF document)"}
    if is_image:
        if ctype not in CLAUDE_IMAGE_TYPES:
            return {"error": f"image type {ctype}"}
        return {"label": label, "media_type": ctype,
                "data": data, "is_text": False, "snippet": "(an image)"}
    if is_text:
        text = data.decode("utf-8", errors="ignore")
        return {"label": label, "media_type": None,
                "data": data, "is_text": True, "snippet": text[:1500]}
    return {"error": f"unsupported type {ctype or '?'}"}


def looks_like_study_material(caption, url, snippet):
    """The gate that keeps memes and game invites out of memory. A cheap Haiku call:
    given what the poster said and a peek at the linked content, is this worth studying?
    If Haiku is down we keep it (dropping a real study guide is the worse mistake)."""
    result = _haiku_json(
        f"""Someone posted a link in a school class group chat. Decide whether the linked
content is study material worth remembering for the class — notes, a study guide, a
rubric, an assignment, a syllabus, slides, an educational article or video, and so on —
as opposed to a meme, a joke, off-topic chatter, a game invite, or social media.

The link: {url}
What they said with it: "{caption or '(nothing)'}"
A peek at the linked content:
\"\"\"{(snippet or '')[:1500]}\"\"\"

If the caption makes clear it is NOT for studying ("look at this meme", "lol"), say no.
Respond with ONLY: {{"study_material": true or false}}""",
        {"study_material": True},
    )
    return bool(result.get("study_material", True))


def _haiku_json(prompt, fallback):
    """One small Haiku call that must come back as JSON. These are the cheap
    judgement calls (is this a real question? which material do they mean?) where a
    wrong answer should degrade quietly rather than crash the handler."""
    try:
        response = claude.messages.create(
            model=DETECTOR_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = next(block.text for block in response.content if block.type == "text")
        return _extract_json(raw)
    except (json.JSONDecodeError, anthropic.APIError) as err:
        print(f"  (haiku call failed: {err})")
        return fallback


def wants_a_reply(text) -> bool:
    """Is this message actually asking us something?

    Discord counts a reply as a mention, so every "ok" and "thanks" aimed at the bot
    used to trigger a full answer from memory — which is how a one-word acknowledgment
    at 1AM turned into an unprompted recap of every deadline. Anything long, or with a
    question mark, is taken at face value; only short acknowledgments get checked.
    """
    stripped = text.strip()
    if "?" in stripped or len(stripped.split()) > 6:
        return True
    result = _haiku_json(
        f"""A student sent this message to a class study bot: "{stripped}"

Is it a request the bot should answer (a question, or an instruction like "quiz us"),
or is it just an acknowledgment / reaction with nothing to answer — "ok", "thanks",
"lol", "nice", "got it", an emoji?

Respond with ONLY: {{"wants_a_reply": true or false}}""",
        {"wants_a_reply": True},   # if Haiku is down, answering is the safer failure
    )
    return bool(result.get("wants_a_reply", True))


def relate_upload(caption, new_topic, existing):
    """When a new upload lands on a topic we already have material for, decide what
    to do with it. Returns {"group": <quiz_group or None>, "combine": "yes"|"no"|"ask"}.

    Combining is only automatic when the caption says so ("these two pages", "more
    chem notes") — otherwise we surface the overlap and let a human choose, because
    "same topic" and "same quiz" are not the same claim.
    """
    if not existing:
        return {"group": None, "combine": "no"}
    listing = "\n".join(
        f'  - group "{g["quiz_group"]}": {g["topics"]} ({g["filenames"]})'
        for g in existing)
    result = _haiku_json(
        f"""A student uploaded new study material to a class group chat.

New material's topic: "{new_topic}"
Their caption on the upload: "{caption or '(no caption)'}"

Study material the bot already has, by group:
{listing}

Decide two things:
1. Which existing group, if any, covers the SAME subject matter as the new material.
   Same subject means a quiz could sensibly mix them (two pages on the War of 1812,
   two chemistry handouts on the same reaction type). Different units of the same
   class are NOT the same subject.
2. Whether the caption states or clearly implies the new material is meant to go
   together with existing material ("these two pages", "part 2", "more notes on
   this", "add this to the chem stuff"). A caption that just names the subject does
   NOT imply combining.

Respond with ONLY:
{{"group": "the matching group id, or empty string if none",
  "caption_says_combine": true or false}}""",
        {"group": "", "caption_says_combine": False},
    )
    group = (result.get("group") or "").strip()
    if not group or group not in {g["quiz_group"] for g in existing}:
        return {"group": None, "combine": "no"}
    return {"group": group,
            "combine": "yes" if result.get("caption_says_combine") else "ask"}


def pick_material_group(request_text, groups):
    """Which pile of material does this quiz request mean?

    Without this, every quiz was built from the last 10 materials in the channel
    regardless of subject — so a bare "quiz" on a War of 1812 upload could come back
    asking about the Civil War, and two people requesting quizzes on different topics
    at the same time got the same mixed-topic questions. A bare request falls back to
    the newest group, which is what "quiz us on it" almost always means.
    """
    if not groups:
        return None
    listing = "\n".join(
        f'  - group "{g["quiz_group"]}": {g["topics"]} ({g["filenames"]})'
        for g in groups)
    result = _haiku_json(
        f"""A student asked a class study bot for a quiz. Their message:
"{request_text}"

Study material available, newest first:
{listing}

Which group do they mean? If they named a subject, pick the group covering it.
If they were vague ("quiz", "quiz us on it", "quiz me"), pick the newest group.
If they named a subject and NOTHING covers it, return an empty string.

Respond with ONLY: {{"group": "the group id, or empty string"}}""",
        {"group": groups[0]["quiz_group"]},
    )
    group = (result.get("group") or "").strip()
    valid = {g["quiz_group"] for g in groups}
    if group in valid:
        return group
    if not group:
        return None   # they named a subject we have nothing on — say so, don't guess
    return groups[0]["quiz_group"]   # a hallucinated id; fall back to newest


def _audience_tag(row):
    """' [for: 7th Grade]' if the item is targeted, else '' — sqlite3.Row has no
    .get(), and older rows predate the column, so read it defensively."""
    try:
        who = row["audience"]
    except (IndexError, KeyError):
        who = None
    return f' [for: {who}]' if who else ''


def _format_memory(deadlines, notes, materials):
    today = class_today().isoformat()
    lines = [f"Today's date is {today}.", "", "DEADLINES I know about:"]
    if deadlines:
        for d in deadlines:
            lines.append(f'  - {d["summary"]} (due {d["due_date"]}, '
                         f'mentioned by {d["author"]}){_audience_tag(d)}')
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("TEACHER NOTES / ANNOUNCEMENTS I know about:")
    if notes:
        for n in notes:
            lines.append(f'  - {n["summary"]} (from {n["author"]}){_audience_tag(n)}')
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("STUDY MATERIALS people uploaded (digests):")
    if materials:
        for m in materials:
            lines.append(f'  From {m["filename"]}:')
            lines.append(f'  {m["digest"]}')
    else:
        lines.append("  (none)")
    return "\n".join(lines)


def answer_question(question, deadlines, notes, materials, asker_roles=None) -> str:
    """Sonnet: answer a student's question from what we remember, showing them only
    what's actually for them."""
    memory = _format_memory(deadlines, notes, materials)
    roles = ", ".join(asker_roles) if asker_roles else "(no specific roles/grade)"
    prompt = f"""You are ClassBrain, a helpful bot that remembers what happens in
a class group chat. Here is everything you currently remember:

{memory}

The student asking has these roles/groups: {roles}.

Some items are tagged "[for: ...]" meaning they're only for that grade/class/group.
When answering, only include a tagged item if it matches this student (their roles,
or a grade/class they name in their question). Items with NO tag are for everyone —
always fair game. If you genuinely can't tell whether a tagged item applies to them,
include it rather than hide it — missing your own homework is worse than seeing one
extra line. Don't mention this filtering or the roles; just answer.

A student asked: "{question}"

Answer casually and briefly, like a helpful classmate — not a robot. Use the
info above. If you don't have the answer, just say you haven't seen anything
about it yet. Never invent dates or details."""

    response = claude.messages.create(
        model=ANSWER_MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    return next(block.text for block in response.content if block.type == "text")


def compose_reminder(deadline, notes) -> str:
    """Sonnet: write a friendly heads-up that nobody asked for."""
    note_lines = "\n".join(f'  - {n["summary"]}' for n in notes) or "  (none)"
    who = _audience_tag(deadline).replace(" [for: ", "").rstrip("]")
    audience_line = (f'\nThis one is specifically for: {who}. Name them so the wrong '
                     f'grade/class doesn\'t worry — e.g. "Heads up 7th graders:".'
                     if who else "")
    prompt = f"""You are ClassBrain in a class group chat. Write a SHORT, casual,
friendly reminder (one or two sentences) that nobody asked for — a heads-up.

Deadline: {deadline["summary"]} — due {deadline["due_date"]}.{audience_line}
Relevant teacher notes you could mention if useful:
{note_lines}

Start with a relevant emoji. Sound like a helpful friend, not a corporate bot.
Do not add anything except the reminder itself."""

    response = claude.messages.create(
        model=ANSWER_MODEL,
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    return next(block.text for block in response.content if block.type == "text")


def make_quiz(request_text, notes, materials) -> dict:
    """Sonnet: turn the class's context into a quick study quiz.

    Returns {"quiz": ..., "answer_key": ...}. Only "quiz" is ever posted — the
    answer key stays in the database so nobody can read ahead.
    """
    note_lines = "\n".join(f'  - {n["summary"]}' for n in notes) or "  (none yet)"
    material_text = "\n\n".join(
        f'From {m["filename"]}:\n{m["digest"]}' for m in materials
    ) or "  (none uploaded yet)"
    prompt = f"""You are ClassBrain, the study bot for a class group chat. A student
asked you to quiz the group. Their message: "{request_text}"

Class notes:
{note_lines}

Study materials people uploaded (prefer these when relevant):
{material_text}

Write a short, friendly quiz to help them study. Rules:
  - Exactly 3 questions, numbered 1 to 3.
  - The materials above are ONE study set (often several pages of the same thing).
    Draw across all of them rather than leaning on just the first.
  - Casual and encouraging, like a study buddy. Start with one short hype line.
  - The questions must NOT reveal or hint at their own answers. They are posted
    to the chat on their own; the answers are kept private for marking.

Respond with ONLY a JSON object in exactly this shape:
{{"quiz": "the hype line and the 3 numbered questions, written exactly as they should appear in the chat",
  "answer_key": "1. answer 2. answer 3. answer — each with a one-line explanation of why"}}"""

    response = claude.messages.create(
        model=ANSWER_MODEL,
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = next(block.text for block in response.content if block.type == "text")
    try:
        return _extract_json(raw)
    except json.JSONDecodeError:
        print(f"  (couldn't parse quiz response: {raw!r})")
        return {}


def grade_answers(quiz_text, reply_text, student) -> dict:
    """Sonnet: mark someone's reply to a quiz, however they chose to write it."""
    prompt = f"""You are ClassBrain, the study bot for a class group chat. Here is
the quiz you posted, together with the private answer key the students cannot see:

{quiz_text}

{student} replied to it with: "{reply_text}"

FIRST decide whether they are genuinely attempting to answer the quiz. Chatter
("lol", "this is hard", "quiz us again", an unrelated question) is NOT an
attempt. An attempt can look like any of these, and you must accept them all:
  - numbered ("1. photosynthesis 2. mitochondria 3. 1914")
  - complete sentences ("I think the first one is photosynthesis...")
  - a plain comma-separated list ("photosynthesis, mitochondria, 1914")
  - fragments, lowercase, no punctuation, or answers out of order
  - a filled-in worksheet that repeats each question with an "Answer:" line —
    in that case their answer is whatever they typed after each "Answer:", and an
    "Answer:" left blank means they skipped that question

If it IS an attempt, grade it:
  - Match each answer to the question it fits by MEANING, not just position.
    Only fall back to order when the content is ambiguous.
  - Be lenient: typos, misspellings, partial phrasing and synonyms all count as
    correct if the understanding is clearly right. You are helping them study,
    not marking an exam.
  - If they only answered some of the questions, grade those and gently note
    which ones they skipped. Never invent an answer they didn't give.

Write the feedback casually and encouragingly, like a study buddy:
  - One line per question, starting with ✅ or ❌.
  - For anything wrong or skipped, give the right answer plus a one-line why.
  - End with a short score line like "2/3 — nice one!".
  - Do NOT use spoiler tags in your feedback, and do not repeat the quiz.

Respond with ONLY a JSON object in exactly this shape:
{{"is_answer_attempt": true or false,
  "feedback": "your feedback, or empty string if not an attempt"}}"""

    response = claude.messages.create(
        model=ANSWER_MODEL,
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = next(block.text for block in response.content if block.type == "text")
    try:
        return _extract_json(raw)
    except json.JSONDecodeError:
        print(f"  (couldn't parse grading response: {raw!r})")
        return {"is_answer_attempt": False}


# --- The proactive reminder loop (the "it just knew" moment) --------

@tasks.loop(time=REMIND_AT)
async def reminder_loop():
    for d in get_due_soon_unreminded():
        channel = bot.get_channel(d["channel_id"])
        if channel is None:
            continue
        notes = get_notes(d["channel_id"])
        text = await asyncio.to_thread(compose_reminder, d, notes)
        await channel.send(text)
        mark_reminded(d["id"])
        print(f" REMINDED about: {d['summary']} (due {d['due_date']})")


@reminder_loop.before_loop
async def before_reminder_loop():
    await bot.wait_until_ready()   # don't start reminding until we're connected


# --- Answering a quiz privately (button -> popup -> only-you reply) --

def _answer_worksheet(questions_text):
    """Turn the quiz questions into a fill-in template for the popup: each
    numbered question, followed by an "Answer:" line to type on. Returns "" if we
    can't find numbered questions, so the modal falls back to its placeholder."""
    rows = []
    for line in (questions_text or "").splitlines():
        s = line.strip()
        if s and s[0].isdigit():        # a "1. ...", "2) ..." question line
            rows.append(s)
            rows.append("Answer: ")
            rows.append("")             # blank line between questions
    return "\n".join(rows).strip() if rows else ""


class AnswerModal(discord.ui.Modal, title="Your answers"):
    """The popup box someone types their answers into. We pre-fill it with the
    quiz questions (each followed by an "Answer:" line) so they can see what
    they're answering without leaving the popup to peek at the channel."""

    def __init__(self, quiz_message_id, prefill=""):
        super().__init__()
        # Carried from the button press: a modal submission doesn't reliably
        # know which message its button was attached to, so we pass it along.
        self.quiz_message_id = quiz_message_id
        # Built per-quiz, so the field has to be created here rather than as a
        # class attribute — that's the only way to give it a per-quiz default.
        self.answers = discord.ui.TextInput(
            label="Type your answer after each question",
            style=discord.TextStyle.paragraph,
            default=prefill or None,
            placeholder=("1. photosynthesis 2. mitochondria 3. 1914\n"
                         "...or full sentences, or a comma-separated list"),
            required=True,
            max_length=4000,
        )
        self.add_item(self.answers)

    async def on_submit(self, interaction: discord.Interaction):
        # Grading is a Claude call and Discord kills an interaction after 3
        # seconds, so acknowledge first and send the real feedback afterwards.
        await interaction.response.defer(ephemeral=True, thinking=True)
        quiz_text = get_quiz(self.quiz_message_id)
        if not quiz_text:
            await interaction.followup.send(
                "I've lost track of that quiz, sorry — ask me for a fresh one!",
                ephemeral=True)
            return
        result = await asyncio.to_thread(
            grade_answers, quiz_text, self.answers.value,
            interaction.user.display_name)
        await interaction.followup.send(
            result.get("feedback") or "I couldn't read those answers — try again?",
            ephemeral=True)
        print(f" GRADED (privately): {interaction.user.display_name}")


class QuizView(discord.ui.View):
    """The button that rides along with every quiz message."""

    def __init__(self):
        super().__init__(timeout=None)   # persistent: keeps working after a restart

    @discord.ui.button(label="Answer privately", emoji="✏️",
                       style=discord.ButtonStyle.primary,
                       custom_id="classbrain:answer_quiz")
    async def answer(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Look up this quiz's questions (never the answer key) and pre-fill the
        # popup with them. A tiny SQLite read, well within the 3s modal deadline.
        questions = get_quiz_questions(interaction.message.id)
        prefill = _answer_worksheet(questions)
        await interaction.response.send_modal(
            AnswerModal(interaction.message.id, prefill))


# --- "Forget me": a person deletes their own data ------------------

def _forget_summary(counts):
    """'2 deadlines, 1 note, 3 study files' — only the non-zero parts."""
    labels = [("deadlines", "deadline"), ("notes", "note"), ("materials", "study file")]
    parts = [f'{counts[key]} {word}{"" if counts[key] == 1 else "s"}'
             for key, word in labels if counts.get(key)]
    return ", ".join(parts) if parts else "nothing"


class ForgetView(discord.ui.View):
    """Confirm/cancel on a deletion request. Deleting is irreversible, so we count
    first, show what's about to go, and only delete on an explicit confirm — and only
    from the person who asked."""

    def __init__(self, requester_id, author_name, channel_id):
        super().__init__(timeout=120)   # transient; expires if left unanswered
        self.requester_id = requester_id
        self.author_name = author_name
        self.channel_id = channel_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Nobody gets to delete someone else's data by pressing their button.
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "This isn't your request to confirm.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Yes, delete my data", emoji="🧹",
                       style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        counts = await asyncio.to_thread(
            forget_user, self.author_name, self.channel_id)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=f"🧹 Done — I've forgotten {_forget_summary(counts)} you shared "
                    "in this channel. That can't be undone.",
            view=self)
        print(f" FORGOT: {self.author_name} in channel {self.channel_id} -> {counts}")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="No worries — I've kept your data. Nothing was deleted.", view=self)


# Phrases that mean "delete my data". Kept explicit so an offhand "I keep forgetting"
# can't trigger a deletion.
FORGET_PHRASES = ("forget me", "forget my data", "delete my data",
                  "forget everything about me", "erase my data")


# --- Discord event handlers ----------------------------------------

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}. Listening for messages...")
    if not reminder_loop.is_running():
        reminder_loop.start()
    # Re-attach the quiz button handler, so buttons on quizzes posted before the
    # last restart still work. on_ready fires again on every reconnect, hence the flag.
    global _view_registered
    if not _view_registered:
        bot.add_view(QuizView())
        _view_registered = True


class CombineView(discord.ui.View):
    """Asked once, when an upload lands on a subject we already have material for:
    should a quiz draw on both, or keep them apart? Transient — if nobody answers,
    the two piles simply stay separate, which is the safe default."""

    def __init__(self, new_group, existing_group, existing_label):
        super().__init__(timeout=3600)
        self.new_group = new_group
        self.existing_group = existing_group
        self.existing_label = existing_label

    @discord.ui.button(label="Combine them", emoji="🔗",
                       style=discord.ButtonStyle.primary)
    async def combine(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Fold the new upload into the older group, so anyone asking about that
        # subject gets both from here on.
        await asyncio.to_thread(merge_quiz_groups, self.new_group, self.existing_group)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=f"🔗 Got it — I'll quiz from this **and** {self.existing_label} together.",
            view=self)
        print(f" COMBINED material: {self.new_group} -> {self.existing_group}")

    @discord.ui.button(label="Keep separate", emoji="✂️",
                       style=discord.ButtonStyle.secondary)
    async def keep_separate(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="✂️ Keeping them separate — just say which one you want quizzed on.",
            view=self)


async def read_attachments(message: discord.Message):
    """Read every file on a message as study material.

    Files uploaded together in one message share a quiz group, so "can u use these
    two textbook pages to make a quiz" produces one quiz spanning both pages instead
    of two disconnected piles. If the batch lands on a subject we already have
    material for, we either merge it (when the caption says to) or ask.
    """
    # Snapshot the groups that existed BEFORE this upload, so the batch we're about
    # to insert isn't compared against itself.
    prior_groups = await asyncio.to_thread(get_material_groups, message.channel.id)
    quiz_group = f"msg-{message.id}"
    ingested = []

    for att in message.attachments:
        # Discord sometimes appends a charset ("text/plain; charset=utf-8").
        ct = (att.content_type or "").split(";")[0].strip().lower()
        name = att.filename
        lower = name.lower()
        is_pdf = ct == "application/pdf" or lower.endswith(".pdf")
        is_image = ct.startswith("image/")
        is_text = ct.startswith("text/") or lower.endswith((".txt", ".md", ".csv"))
        if not (is_pdf or is_image or is_text):
            await message.channel.send(
                f"(I can read PDFs, images, and text files — I can't open {name} yet.)")
            continue
        if is_image and ct not in CLAUDE_IMAGE_TYPES:
            await message.channel.send(
                f"(I can't open {name} — I can only read JPEG, PNG, GIF, and WebP. "
                "If that's an iPhone photo, screenshot it and send the screenshot.)")
            continue
        data = await att.read()
        media_type = "application/pdf" if is_pdf else ct
        result = await asyncio.to_thread(
            ingest_attachment, name, media_type, data, is_text)
        await asyncio.to_thread(
            save_material, name, result["digest"], message.author.display_name,
            message.channel.id, message.jump_url, result["topic"], quiz_group)
        ingested.append({"filename": name, "topic": result["topic"]})
        print(f" INGESTED FILE: {name} [{result['topic']}]")

    if not ingested:
        return

    # One confirmation for the whole batch — two separate "Got X" lines was the
    # thing that made it look like the two pages had been read in isolation.
    topics = list(dict.fromkeys(item["topic"] for item in ingested))
    files = ", ".join(f"**{item['filename']}**" for item in ingested)
    together = " (I'll treat them as one set)" if len(ingested) > 1 else ""
    await message.channel.send(
        f"📥 Got {files} — {', '.join(topics)}. I've studied {'them' if len(ingested) > 1 else 'it'}"
        f"{together}. Try: `@ClassBrain quiz us on it`")

    relation = await asyncio.to_thread(
        relate_upload, message.clean_content.strip(), topics[0], prior_groups)
    if not relation["group"]:
        return

    match = next(g for g in prior_groups if g["quiz_group"] == relation["group"])
    label = f'the earlier **{match["topics"]}** material'
    if relation["combine"] == "yes":
        await asyncio.to_thread(merge_quiz_groups, quiz_group, relation["group"])
        await message.channel.send(f"🔗 Added to {label} — I'll quiz from both.")
        print(f" COMBINED material (caption): {quiz_group} -> {relation['group']}")
    else:
        await message.channel.send(
            f"I already have {label}. Want quizzes to draw on both, or keep them separate?",
            view=CombineView(quiz_group, relation["group"], label))


async def read_links(message: discord.Message, urls):
    """Fetch every link in a message, keep the ones that are study material, and file
    them like uploaded files. A link and a file dropped in the same message land in the
    same quiz group (both keyed on the message id), so "quiz us on these" spans both.

    Deliberately quiet: a link that turns out to be a meme, a dead page, or a type we
    can't read is dropped without a word — the only failure worth interrupting for is a
    Google doc that just needs its sharing turned on, which the poster can fix.
    """
    quiz_group = f"msg-{message.id}"
    caption = message.clean_content.strip()

    for url in urls:
        fetched = await fetch_link(url)
        if fetched.get("error"):
            if fetched["error"] == "private":
                await message.channel.send(
                    "(I tried to open that Google link but it's not link-shared — set it "
                    "to *Anyone with the link* and repost, or paste the text.)")
            else:
                print(f"  (couldn't read link {url}: {fetched['error']})")
            continue

        if not await asyncio.to_thread(
                looks_like_study_material, caption, url, fetched["snippet"]):
            print(f"(skipped link — not study material) {url}")
            continue

        result = await asyncio.to_thread(
            ingest_attachment, fetched["label"], fetched["media_type"],
            fetched["data"], fetched["is_text"])
        # The link itself is the source we store, not the message's jump_url — so
        # "where's that from?" can point back at the actual doc.
        await asyncio.to_thread(
            save_material, fetched["label"], result["digest"],
            message.author.display_name, message.channel.id, url,
            result["topic"], quiz_group)
        print(f" INGESTED LINK: {url} [{result['topic']}]")
        await message.channel.send(
            f"🔗 Read **{fetched['label']}** — {result['topic']}. I've studied it. "
            "Try: `@ClassBrain quiz us on it`")


def _real_roles(member):
    """A member's meaningful roles, as names — dropping @everyone (which everyone has)
    and bot/integration-managed roles. This is how the bot learns a student's grade
    without any server-specific config: whatever grade/class roles a server happens to
    use, they show up here. An empty list just means the server doesn't use roles that
    way, and scoping quietly falls back to per-channel."""
    if member is None or not hasattr(member, "roles"):
        return []
    return [r.name for r in member.roles if not r.is_default() and not r.managed]


def audience_from_message(message, detected_audience="", poster_grade=""):
    """Who a saved item is for, in order of how sure we can be:
      1. a role actually pinged on the message ("hey @7th Grade, essay Monday")
      2. a grade/section the message names in its text ("7th grade essay Monday")
      3. the sender's own grade, inferred when they describe their own class
         ("we have an essay Monday" from someone with an @8th Grade role)
    Blank means everyone in the channel. (1) and (2) are what the message states;
    (3) is a guess we only make for own-class deadlines, so it's the last resort."""
    pinged = [r.name for r in message.role_mentions
              if not r.is_default() and not r.managed]
    if pinged:
        return ", ".join(pinged)
    if (detected_audience or "").strip():
        return detected_audience.strip()
    return (poster_grade or "").strip()


def _is_for_bot(message: discord.Message) -> bool:
    """Is this message aimed at us? Counts a direct @mention of the bot AND a
    mention of the bot's own auto-created role. Discord makes a managed role with
    the bot's name when it joins, and people pick that role from the @-autocomplete
    just as often as the bot user itself — without this, "@ClassBrain what's due?"
    falls through and gets filed away as chatter."""
    if bot.user in message.mentions:
        return True
    me = message.guild.me if message.guild else None
    if me and set(message.role_mentions) & {r for r in me.roles if r.managed}:
        return True
    return False


def _category_of(channel):
    """The Discord category a channel sits in, or None. Works for threads too —
    a thread inherits its parent channel's category."""
    cat = getattr(channel, "category", None)
    return cat


async def handle_admin_command(message: discord.Message) -> bool:
    """Server admins turn tracking on/off by category. Returns True if the message
    was one of these commands (so on_message stops), False to let it fall through.

    Only someone with Manage Server can run these. A non-admin who happens to type
    'status' just falls through to a normal answer, rather than getting scolded."""
    lower = message.clean_content.lower()
    is_deactivate = "deactivate" in lower or "stop tracking" in lower
    is_activate = (not is_deactivate) and "activate" in lower
    is_status = ("tracking status" in lower or "what are you tracking" in lower
                 or "what channels" in lower)
    if not (is_activate or is_deactivate or is_status):
        return False

    # Guild-only, and only for admins. A non-admin asking falls through to Q&A.
    member = message.author if message.guild else None
    if not member or not member.guild_permissions.manage_guild:
        return False

    if is_status:
        rows = await asyncio.to_thread(get_active_category_rows, message.guild.id)
        if not rows:
            await message.reply(
                "I'm not narrowed to any categories here, so I'm listening in **every** "
                "channel. Run `@ClassBrain activate` inside a class category (like a "
                "grade) to limit me to just your class channels.")
        else:
            names = "\n".join(f"  • {r['name']}" for r in rows)
            await message.reply(
                "📋 I'm only tracking these categories (everything else is ignored):\n"
                f"{names}")
        return True

    category = _category_of(message.channel)
    if category is None:
        await message.reply(
            "This channel isn't inside a category. Put it under one (like a **7th "
            "Grade** category) first — I switch tracking on a whole category at once.")
        return True

    if is_activate:
        await asyncio.to_thread(
            activate_category, category.id, message.guild.id, category.name,
            message.author.display_name)
        channels = ", ".join(f"#{c.name}" for c in category.text_channels) or "(none yet)"
        await message.reply(
            f"✅ Now tracking the **{category.name}** category: {channels}. "
            "Each channel keeps its own separate memory — a deadline in one class "
            "never shows up in another.")
        print(f" ACTIVATED category {category.name} ({category.id})")
    else:  # deactivate
        await asyncio.to_thread(deactivate_category, category.id)
        await message.reply(
            f"🛑 Stopped tracking the **{category.name}** category — I'll ignore its "
            "channels now. (What I already remembered is still there; say "
            "`@ClassBrain forget me` in a channel to clear your own.)")
        print(f" DEACTIVATED category {category.name} ({category.id})")
    return True


@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return

    # Admin config commands run FIRST and everywhere — you have to be able to
    # activate a category from inside a channel the bot isn't tracking yet.
    if _is_for_bot(message) and await handle_admin_command(message):
        return

    # From here on, only act in channels this server has opted in (see is_tracked).
    # Skipping untracked channels here is the whole cost saving at school scale:
    # the detector never runs on #general, and memes never get filed as notes.
    category = _category_of(message.channel)
    tracked = await asyncio.to_thread(
        is_tracked,
        message.guild.id if message.guild else None,
        category.id if category else None)
    if not tracked:
        # If they actually @mentioned us in a non-class channel, don't just vanish —
        # point them at their class channel once. Otherwise stay silent.
        if _is_for_bot(message) and message.clean_content.strip():
            await message.reply(
                "I only keep track of class channels — ask me in your class's "
                "channel and I'll have everything for it. 📚")
        return

    # Files first, always. Someone can drop a study guide while replying to the
    # bot, and Discord counts a reply as a mention — so this can't live inside
    # either branch below or the upload gets swallowed.
    if message.attachments:
        await read_attachments(message)

    # Links are the other way study material arrives. Fetch anything linked and file
    # the keepers — before the branches below, for the same reason files are read
    # first: a doc can be shared in the same breath as a question to the bot.
    urls = extract_urls(message.content)
    if urls:
        await read_links(message, urls)

    text = message.clean_content.strip()

    # Someone replying to a quiz we posted is almost certainly answering it.
    # This has to be checked BEFORE the mention branch below: Discord only counts
    # a reply as a mention while the replier leaves the ping toggle on, and a lot
    # of people turn it off — without this, a pingless answer would fall through
    # and get filed away as a class note.
    if text and message.reference and message.reference.message_id:
        quiz_text = get_quiz(message.reference.message_id)
        if quiz_text:
            result = await asyncio.to_thread(
                grade_answers, quiz_text, text, message.author.display_name)
            # They might be replying to the quiz without answering it ("lol",
            # "quiz us again") — in that case fall through and handle it normally.
            if result.get("is_answer_attempt") and result.get("feedback"):
                await message.reply(result["feedback"])
                print(f" GRADED: {message.author.display_name}: {text}")
                return

    # Mentioning OR replying to the bot means "this is for you" — don't store it.
    if _is_for_bot(message):
        if not text:
            return   # bare reply with just a file — already handled above
        lower = text.lower()
        # "forget me" -> let them delete their own data. Checked before everything
        # else so the phrase can't be swallowed by the quiz or answer branches.
        if any(phrase in lower for phrase in FORGET_PHRASES):
            counts = await asyncio.to_thread(
                count_user_data, message.author.display_name, message.channel.id)
            if sum(counts.values()) == 0:
                await message.reply(
                    "I don't have anything stored from you in this channel — "
                    "nothing to forget. 👍")
                return
            await message.reply(
                f"You've got {_forget_summary(counts)} stored in this channel. "
                "Delete all of it? This can't be undone.",
                view=ForgetView(message.author.id, message.author.display_name,
                                message.channel.id))
            return
        # "quiz" -> teach mode; anything else -> answer from memory.
        if "quiz" in text.lower():
            notes = get_notes(message.channel.id)
            # Quiz only from the material they actually meant. Two people asking
            # about two subjects at once each get their own; the answer key is
            # already stored per quiz message, so the two never cross-contaminate.
            groups = await asyncio.to_thread(get_material_groups, message.channel.id)
            group = await asyncio.to_thread(pick_material_group, text, groups)
            if groups and group is None:
                await message.reply(
                    "I don't have anything uploaded on that yet — send me the pages "
                    "and I'll quiz you on them.")
                return
            materials = await asyncio.to_thread(
                get_materials_in_group, group) if group else []
            quiz = await asyncio.to_thread(make_quiz, text, notes, materials)
            if not quiz.get("quiz"):
                await message.reply("My brain glitched making that one — ask me again?")
                return
            sent = await message.reply(
                f'{quiz["quiz"]}\n\n'
                "*Hit the button for private feedback, or just reply with your answers.*",
                view=QuizView())
            # Only the questions went out. Keep both halves so we can mark
            # replies to this message later.
            save_quiz(sent.id, message.channel.id,
                      f'QUESTIONS (posted to the chat):\n{quiz["quiz"]}\n\n'
                      f'ANSWER KEY (private, students never saw this):\n'
                      f'{quiz.get("answer_key", "")}')
        else:
            # "ok", "thanks", a lone emoji — replying to us isn't the same as asking
            # us something, and Discord makes every reply look like a mention.
            if not await asyncio.to_thread(wants_a_reply, text):
                print(f"(no reply needed) {message.author.display_name}: {text}")
                return
            deadlines = get_upcoming_deadlines(message.channel.id)
            notes = get_notes(message.channel.id)
            materials = get_materials(message.channel.id)
            answer = await asyncio.to_thread(
                answer_question, text, deadlines, notes, materials,
                _real_roles(message.author))
            await message.reply(answer)
        return

    if not text:
        return

    poster_roles = _real_roles(message.author)
    result = await asyncio.to_thread(analyze_message, message.content, poster_roles)

    if not result.get("is_important"):
        print(f"(skipped) {message.author.display_name}: {message.content}")
        return

    author = message.author.display_name
    link = message.jump_url
    # Who it's for: a role pinged on the message wins; then a grade named in the text;
    # then the sender's own grade for their own-class deadlines. Blank = everyone.
    audience = audience_from_message(
        message, result.get("audience", ""), result.get("poster_grade", ""))

    if result.get("type") == "deadline" and result.get("due_date"):
        save_deadline(result["summary"], result["due_date"], author,
                      message.channel.id, link, audience)
        print(f" SAVED DEADLINE: {result['summary']} (due {result['due_date']})"
              f"{f' [for {audience}]' if audience else ''}")
    else:
        save_note(result.get("summary", ""), message.content, author,
                  message.channel.id, link, audience)
        print(f" SAVED NOTE: {result.get('summary', '')}"
              f"{f' [for {audience}]' if audience else ''}")


# --- Go -------------------------------------------------------------

if __name__ == "__main__":
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("Set your DISCORD_TOKEN environment variable first.")
    init_db()
    bot.run(token)
