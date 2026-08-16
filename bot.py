"""
Memlo — the magic loop
A Discord bot that is the memory of your class group chat.

  1. LISTENS to every message and, using Claude Haiku, decides if it's worth
     remembering (a deadline or a teacher announcement). Saves it to a database.
  2. ANSWERS when you @-mention it ("@Memlo what's due?") using Claude Sonnet,
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
import logging
import sys
import time as time_module
from collections import defaultdict, deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse, unquote
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
import aiohttp
from aiohttp import web
from discord import app_commands
from discord.ext import tasks
import anthropic

# --- Settings you can tweak ----------------------------------------

DETECTOR_MODEL = "claude-haiku-4-5"   # cheap, runs on every message
ANSWER_MODEL = "claude-sonnet-5"      # smarter, only runs when asked or reminding
DB_PATH = "memlo.db"             # the bot's memory file
DEFAULT_TIMEZONE = os.environ.get("CLASS_TIMEZONE", "America/New_York")
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "backups"))
BACKUP_RETENTION = int(os.environ.get("BACKUP_RETENTION", "14"))
HEALTH_HOST = os.environ.get("HEALTH_HOST", "127.0.0.1")
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", "8080"))
LOG_PATH = os.environ.get("LOG_PATH", "memlo.log")

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
CLASS_TIMEZONE = ZoneInfo(DEFAULT_TIMEZONE)  # fallback for legacy rows and tests
REMIND_HOURS = (6, 15)               # evaluated in each server's own timezone
QUIET_HOURS_AFTER_SAVING = 1          # don't parrot back something just announced

PREFILTER_RE = re.compile(
    r"\b(?:due|deadline|test|quiz|exam|homework|hw|assignment|project|essay|lab|"
    r"worksheet|paper|presentation|submit|turn\s+in|study\s+guide|syllabus|"
    r"cancelled|canceled|rescheduled|no\s+school|bring|allowed|open[- ]note|"
    r"notes\s+allowed|teacher\s+said|class\s+schedule|school\s+schedule|reminder)\b",
    re.IGNORECASE,
)
DATE_SIGNAL_RE = re.compile(
    r"\b(?:tomorrow|tmr|tonight|today|monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\b|\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b",
    re.IGNORECASE,
)
RELEVANT_ROLE_RE = re.compile(
    r"\b(?:teacher|instructor|professor|faculty|staff|admin|moderator|tutor|ta)\b",
    re.IGNORECASE,
)

RATE_LIMITS = {
    "answer": (8, 60),
    "quiz": (3, 300),
    "grade": (10, 60),
    "ingest": (5, 600),
}

LOGGER = logging.getLogger("memlo")
_health = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "last_ready": None,
    "last_reminder_tick": None,
    "last_backup": None,
    "last_error": None,
}


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

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _timezone_for_guild(guild_id=None):
    if guild_id is not None:
        try:
            conn = db()
            row = conn.execute(
                "SELECT timezone FROM guild_settings WHERE guild_id = ?", (guild_id,)
            ).fetchone()
            conn.close()
            if row and row["timezone"]:
                return ZoneInfo(row["timezone"])
        except (sqlite3.Error, KeyError, ZoneInfoNotFoundError):
            pass
    return CLASS_TIMEZONE


def class_now(guild_id=None):
    return datetime.now(_timezone_for_guild(guild_id)).replace(tzinfo=None)


def class_today(guild_id=None):
    return class_now(guild_id).date()


# --- Clients -------------------------------------------------------

claude = anthropic.Anthropic()

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

_view_registered = False   # so a reconnect doesn't re-register the quiz button
_commands_synced = False
_health_runner = None
_reminder_slots = {}


class RateLimiter:
    """Small in-memory sliding-window limiter; no message content is retained."""

    def __init__(self):
        self._events = defaultdict(deque)

    def allow(self, action, guild_id, user_id, now=None):
        limit, window = RATE_LIMITS[action]
        now = time_module.monotonic() if now is None else now
        key = (action, guild_id, user_id)
        events = self._events[key]
        while events and events[0] <= now - window:
            events.popleft()
        if len(events) >= limit:
            return False
        events.append(now)
        return True


rate_limiter = RateLimiter()


# --- The database (the bot's memory) -------------------------------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row   # lets us read columns by name
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _add_column(conn, table, column, declaration):
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


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
    for column, declaration in (
        ("created_at", "TEXT"), ("audience", "TEXT"),
        ("guild_id", "INTEGER"), ("author_id", "INTEGER"),
    ):
        _add_column(conn, "deadlines", column, declaration)
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
    for column, declaration in (
        ("audience", "TEXT"), ("guild_id", "INTEGER"),
        ("author_id", "INTEGER"), ("created_at", "TEXT"),
    ):
        _add_column(conn, "notes", column, declaration)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS quizzes (
            message_id INTEGER PRIMARY KEY,   -- the bot's own quiz message
            channel_id INTEGER,
            quiz_text TEXT,       -- questions AND answers, so we can grade replies
            created_at TEXT
        )
    """)
    _add_column(conn, "quizzes", "guild_id", "INTEGER")
    _add_column(conn, "quizzes", "author_id", "INTEGER")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS active_categories (
            category_id INTEGER PRIMARY KEY,   -- a Discord category the bot tracks
            guild_id INTEGER,
            name TEXT,             -- category name at activation, for status display
            activated_by TEXT,
            created_at TEXT
        )
    """)
    _add_column(conn, "active_categories", "activated_by_id", "INTEGER")
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
    for column, declaration in (
        ("topic", "TEXT"), ("quiz_group", "TEXT"), ("created_at", "TEXT"),
        ("guild_id", "INTEGER"), ("author_id", "INTEGER"),
    ):
        _add_column(conn, "materials", column, declaration)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id INTEGER PRIMARY KEY,
            timezone TEXT NOT NULL,
            installer_id INTEGER,
            installed_at TEXT NOT NULL,
            setup_status TEXT NOT NULL DEFAULT 'pending',
            setup_completed_at TEXT,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tracked_channels (
            channel_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            name TEXT,
            activated_by_id INTEGER,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analytics_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_name TEXT NOT NULL,
            guild_id INTEGER,
            properties_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS operational_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            context TEXT NOT NULL,
            exception_type TEXT NOT NULL,
            guild_id INTEGER,
            created_at TEXT NOT NULL
        )
    """)
    _add_column(conn, "operational_errors", "guild_id", "INTEGER")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_deadlines_guild ON deadlines(guild_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(guild_id, author_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_materials_user ON materials(guild_id, author_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_analytics_guild ON analytics_events(guild_id, event_name)")

    # Existing category-scoped installations stay configured after this migration.
    # Brand-new guilds get a pending row through on_guild_join/on_ready and track nothing.
    for row in conn.execute(
            "SELECT DISTINCT guild_id FROM active_categories WHERE guild_id IS NOT NULL"):
        now = utc_now_iso()
        conn.execute(
            "INSERT OR IGNORE INTO guild_settings "
            "(guild_id, timezone, installed_at, setup_status, setup_completed_at, updated_at) "
            "VALUES (?, ?, ?, 'configured', ?, ?)",
            (row["guild_id"], DEFAULT_TIMEZONE, now, now, now),
        )
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


def save_deadline(summary, due_date, author, channel_id, link, audience="",
                  guild_id=None, author_id=None):
    conn = db()
    conn.execute(
        "INSERT INTO deadlines (summary, due_date, author, channel_id, link, "
        "created_at, audience, guild_id, author_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (summary, due_date, author, channel_id, link,
         class_now(guild_id).isoformat(), audience, guild_id, author_id),
    )
    conn.commit()
    conn.close()


def save_note(summary, original_text, author, channel_id, link, audience="",
              guild_id=None, author_id=None):
    conn = db()
    conn.execute(
        "INSERT INTO notes (summary, original_text, author, channel_id, link, audience, "
        "guild_id, author_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (summary, original_text, author, channel_id, link, audience,
         guild_id, author_id, utc_now_iso()),
    )
    conn.commit()
    conn.close()


def get_upcoming_deadlines(channel_id, guild_id=None):
    today = class_today(guild_id).isoformat()
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


def save_material(filename, digest, author, channel_id, link, topic, quiz_group,
                  guild_id=None, author_id=None):
    conn = db()
    cur = conn.execute(
        "INSERT INTO materials (filename, digest, author, channel_id, link, topic, "
        "quiz_group, created_at, guild_id, author_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (filename, digest, author, channel_id, link, topic, quiz_group,
         utc_now_iso(), guild_id, author_id),
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


def save_quiz(message_id, channel_id, quiz_text, guild_id=None, author_id=None):
    conn = db()
    conn.execute(
        "INSERT OR REPLACE INTO quizzes (message_id, channel_id, quiz_text, created_at, "
        "guild_id, author_id) VALUES (?, ?, ?, ?, ?, ?)",
        (message_id, channel_id, quiz_text, utc_now_iso(), guild_id, author_id),
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


def get_due_soon_unreminded(guild_id):
    """Deadlines due within the next few days that we haven't reminded about yet.

    Skips anything announced in the last hour — if someone posts a deadline at
    2:55pm, echoing it back at the 3pm reminder reads as the bot not listening.
    The "it just knew" moment only lands once everyone has forgotten.
    """
    today = class_today(guild_id)
    window = (today + timedelta(days=REMIND_WITHIN_DAYS)).isoformat()
    quiet_until = (class_now(guild_id) - timedelta(
        hours=QUIET_HOURS_AFTER_SAVING)).isoformat()
    conn = db()
    rows = conn.execute(
        "SELECT * FROM deadlines WHERE guild_id = ? AND reminded = 0 "
        "AND due_date >= ? AND due_date <= ? "
        "AND (created_at IS NULL OR created_at <= ?)",
        (guild_id, today.isoformat(), window, quiet_until),
    ).fetchall()
    conn.close()
    return rows


def mark_reminded(deadline_id):
    conn = db()
    conn.execute("UPDATE deadlines SET reminded = 1 WHERE id = ?", (deadline_id,))
    conn.commit()
    conn.close()


_USER_TABLES = ("deadlines", "notes", "materials")


def count_user_data(author, channel_id):
    conn = db()
    counts = {t: conn.execute(
        f"SELECT COUNT(*) FROM {t} WHERE author_id IS NULL AND author = ? AND channel_id = ?",
        (author, channel_id)).fetchone()[0] for t in _USER_TABLES}
    conn.close()
    return counts


def forget_user(author, channel_id):
    """Legacy display-name deletion for records created before Stage 0."""
    conn = db()
    counts = {}
    for t in _USER_TABLES:
        cur = conn.execute(
            f"DELETE FROM {t} WHERE author_id IS NULL AND author = ? AND channel_id = ?",
            (author, channel_id))
        counts[t] = cur.rowcount
    conn.commit()
    conn.close()
    return counts


def count_user_data_by_id(user_id, guild_id):
    conn = db()
    counts = {table: conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE author_id = ? AND guild_id = ?",
        (user_id, guild_id)).fetchone()[0] for table in _USER_TABLES}
    counts["quizzes"] = conn.execute(
        "SELECT COUNT(*) FROM quizzes WHERE author_id = ? AND guild_id = ?",
        (user_id, guild_id)).fetchone()[0]
    conn.close()
    return counts


def forget_user_by_id(user_id, guild_id):
    """Delete everything attributable to a Discord user across one server."""
    conn = db()
    counts = {}
    for table in _USER_TABLES + ("quizzes",):
        cur = conn.execute(
            f"DELETE FROM {table} WHERE author_id = ? AND guild_id = ?",
            (user_id, guild_id))
        counts[table] = cur.rowcount
    conn.commit()
    conn.close()
    return counts


TELEMETRY_EVENTS = {
    "install", "setup_complete", "first_deadline_saved", "first_answer",
    "first_reminder", "first_quiz_completed", "referral_link_shown", "removal",
}
TELEMETRY_PROPERTY_ALLOWLIST = {"scope_type", "timezone", "source", "count"}


def record_event(event_name, guild_id=None, properties=None, once=False):
    """Store product events only; message text and filenames are never accepted."""
    if event_name not in TELEMETRY_EVENTS:
        raise ValueError(f"Unsupported telemetry event: {event_name}")
    clean = {
        key: value for key, value in (properties or {}).items()
        if key in TELEMETRY_PROPERTY_ALLOWLIST
        and isinstance(value, (str, int, float, bool, type(None)))
    }
    conn = db()
    if once and conn.execute(
            "SELECT 1 FROM analytics_events WHERE event_name = ? AND guild_id IS ?",
            (event_name, guild_id)).fetchone():
        conn.close()
        return False
    conn.execute(
        "INSERT INTO analytics_events (event_name, guild_id, properties_json, created_at) "
        "VALUES (?, ?, ?, ?)",
        (event_name, guild_id, json.dumps(clean, sort_keys=True), utc_now_iso()),
    )
    conn.commit()
    conn.close()
    return True


def record_operational_error(context, exc, guild_id=None):
    """Record a content-free error marker for health checks and operations."""
    global _health
    happened = utc_now_iso()
    _health["last_error"] = happened
    try:
        conn = db()
        conn.execute(
            "INSERT INTO operational_errors (context, exception_type, guild_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (context[:100], type(exc).__name__[:100], guild_id, happened),
        )
        conn.commit()
        conn.close()
    except sqlite3.Error:
        LOGGER.exception("Could not record operational error")


# --- Which channels the bot tracks (grade/class scoping) -----------
# At school scale the bot must NOT listen everywhere: running the detector on every
# message in #general/#memes is wasted cost and files chatter as class notes. An admin
# turns on a whole category ("7th Grade") and the bot tracks every channel inside it.
# Everything the bot remembers is already scoped per channel_id, so one channel per
# class keeps 7th-grade science and 8th-grade math completely separate on their own.
#
# A server tracks NOTHING until an admin explicitly completes /setup. This opt-in
# invariant is central to privacy, predictable cost, and Message Content approval.


def ensure_guild_settings(guild_id, installer_id=None):
    now = utc_now_iso()
    conn = db()
    conn.execute(
        "INSERT OR IGNORE INTO guild_settings "
        "(guild_id, timezone, installer_id, installed_at, setup_status, updated_at) "
        "VALUES (?, ?, ?, ?, 'pending', ?)",
        (guild_id, DEFAULT_TIMEZONE, installer_id, now, now),
    )
    if installer_id is not None:
        conn.execute(
            "UPDATE guild_settings SET installer_id = COALESCE(installer_id, ?), "
            "updated_at = ? WHERE guild_id = ?",
            (installer_id, now, guild_id),
        )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)).fetchone()
    conn.close()
    return row


def configure_guild(guild_id, timezone_name, installer_id):
    ZoneInfo(timezone_name)  # validate before writing
    ensure_guild_settings(guild_id, installer_id)
    now = utc_now_iso()
    conn = db()
    conn.execute(
        "UPDATE guild_settings SET timezone = ?, installer_id = COALESCE(installer_id, ?), "
        "setup_status = 'configured', setup_completed_at = COALESCE(setup_completed_at, ?), "
        "updated_at = ? WHERE guild_id = ?",
        (timezone_name, installer_id, now, now, guild_id),
    )
    conn.commit()
    conn.close()


def get_guild_settings(guild_id):
    conn = db()
    row = conn.execute(
        "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)).fetchone()
    conn.close()
    return row

def activate_category(category_id, guild_id, name, activated_by, activated_by_id=None):
    conn = db()
    conn.execute(
        "INSERT OR REPLACE INTO active_categories "
        "(category_id, guild_id, name, activated_by, activated_by_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (category_id, guild_id, name, activated_by, activated_by_id, utc_now_iso()))
    conn.commit()
    conn.close()


def deactivate_category(category_id):
    conn = db()
    conn.execute("DELETE FROM active_categories WHERE category_id = ?", (category_id,))
    conn.commit()
    conn.close()


def activate_channel(channel_id, guild_id, name, activated_by_id):
    conn = db()
    conn.execute(
        "INSERT OR REPLACE INTO tracked_channels "
        "(channel_id, guild_id, name, activated_by_id, created_at) VALUES (?, ?, ?, ?, ?)",
        (channel_id, guild_id, name, activated_by_id, utc_now_iso()),
    )
    conn.commit()
    conn.close()


def deactivate_channel(channel_id):
    conn = db()
    conn.execute("DELETE FROM tracked_channels WHERE channel_id = ?", (channel_id,))
    conn.commit()
    conn.close()


def get_active_categories(guild_id):
    conn = db()
    rows = conn.execute(
        "SELECT category_id FROM active_categories WHERE guild_id = ?",
        (guild_id,)).fetchall()
    conn.close()
    return {r["category_id"] for r in rows}


def get_active_channels(guild_id):
    conn = db()
    rows = conn.execute(
        "SELECT channel_id FROM tracked_channels WHERE guild_id = ?", (guild_id,)
    ).fetchall()
    conn.close()
    return {r["channel_id"] for r in rows}


def get_active_category_rows(guild_id):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM active_categories WHERE guild_id = ? ORDER BY name",
        (guild_id,)).fetchall()
    conn.close()
    return rows


def is_tracked(guild_id, category_id, channel_id=None):
    """True only after setup and only for an explicitly selected scope."""
    if guild_id is None:
        return False   # a DM: no class context to scope to
    settings = get_guild_settings(guild_id)
    if not settings or settings["setup_status"] != "configured":
        return False
    if channel_id is not None and channel_id in get_active_channels(guild_id):
        return True
    return category_id is not None and category_id in get_active_categories(guild_id)


def delete_server_data(guild_id, reinitialize=True):
    """Delete all memory, settings, and telemetry for one Discord server."""
    conn = db()
    counts = {}
    for table in ("deadlines", "notes", "materials", "quizzes", "active_categories",
                  "tracked_channels", "analytics_events", "operational_errors"):
        cur = conn.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
        counts[table] = cur.rowcount
    conn.execute("DELETE FROM guild_settings WHERE guild_id = ?", (guild_id,))
    conn.commit()
    conn.close()
    if reinitialize:
        ensure_guild_settings(guild_id)
    return counts


# --- Operations: logging, backups, and health ----------------------

def setup_logging():
    if LOGGER.handlers:
        return
    LOGGER.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    LOGGER.addHandler(stream)
    if LOG_PATH:
        rotating = RotatingFileHandler(
            LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3)
        rotating.setFormatter(formatter)
        LOGGER.addHandler(rotating)


def backup_database():
    """Take a transactionally consistent SQLite backup and prune old snapshots."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = BACKUP_DIR / f"memlo-{stamp}.db"
    source = db()
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    backups = sorted(BACKUP_DIR.glob("memlo-*.db"), reverse=True)
    for old in backups[max(BACKUP_RETENTION, 1):]:
        old.unlink()
    _health["last_backup"] = utc_now_iso()
    return target


async def health_handler(request):
    database_ok = True
    try:
        conn = db()
        conn.execute("SELECT 1").fetchone()
        conn.close()
    except sqlite3.Error:
        database_ok = False
    ready = bot.is_ready()
    status = "ok" if ready and database_ok else "degraded"
    return web.json_response({
        "status": status,
        "discord_ready": ready,
        "database_ok": database_ok,
        "guild_count": len(bot.guilds),
        **_health,
    }, status=200 if status == "ok" else 503)


async def start_health_server():
    global _health_runner
    if HEALTH_PORT <= 0 or _health_runner is not None:
        return
    app = web.Application()
    app.router.add_get("/health", health_handler)
    _health_runner = web.AppRunner(app)
    await _health_runner.setup()
    site = web.TCPSite(_health_runner, HEALTH_HOST, HEALTH_PORT)
    await site.start()
    LOGGER.info("Health endpoint listening on http://%s:%s/health",
                HEALTH_HOST, HEALTH_PORT)


# --- Claude: detect, answer, remind --------------------------------

def should_analyze_message(message_text, poster_roles=None):
    """Cheap local gate before the per-message classifier call.

    It intentionally errs toward recall when a trusted school role is present, while
    ordinary chatter without a deadline/date/announcement signal never leaves the VPS.
    """
    if PREFILTER_RE.search(message_text or ""):
        return True
    if any(RELEVANT_ROLE_RE.search(role or "") for role in (poster_roles or [])):
        return True
    return bool(DATE_SIGNAL_RE.search(message_text or "") and re.search(
        r"\b(?:teacher|class|school|schedule|reminder|announcement)\b",
        message_text or "", re.IGNORECASE))

def _extract_json(text):
    """Claude sometimes wraps JSON in ```json fences. Grab { ... } safely."""
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    return json.loads(text)


def analyze_message(message_text: str, poster_roles=None, guild_id=None) -> dict:
    """Haiku: is this message worth remembering?

    poster_roles are the sender's own roles. In a student server nobody pings a grade
    role or writes "7th grade" every time — they just say "we have a test Friday". So
    when someone describes their OWN class's deadline and one of their roles names a
    grade, we tag it with that grade. General news ("no school Monday") stays untagged.
    """
    today = class_today(guild_id).isoformat()
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
        LOGGER.warning("Could not parse classifier response")
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


def _sniff_type(data):
    """The real media type of a downloaded body, read from its first bytes.

    Google Drive's download endpoint labels nearly everything application/octet-stream,
    so trusting the Content-Type header alone makes us reject PDFs and photos we can
    actually read. Returns a media type, or None if the bytes aren't anything we handle."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    # No magic number. If it decodes cleanly and reads as printable characters it's a
    # text file (someone's .txt/.md/.csv notes); otherwise it's a binary we can't use.
    head = data[:2048]
    if not head:
        return None
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        return None
    printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in text)
    return "text/plain" if printable / len(text) > 0.9 else None


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
                disposition = resp.headers.get("Content-Disposition", "")
                data = await _read_capped(resp)
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        return {"error": f"fetch failed: {err}"}

    label = _label_from_url(url)

    # Every Drive share link ends in /view, so the URL alone names the material "view".
    # The download response carries the real filename — much better to remember it by.
    name = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disposition)
    if name:
        label = unquote(name.group(1)).strip()[:80] or label

    # A Google export that comes back as HTML is the "you need access" sign-in page —
    # the doc isn't link-shared. Worth telling the poster about; they can fix it.
    # Drive downloads ('auto') count too: a real file arrives as a PDF, an image, or
    # some other binary, never as a web page. HTML there means Google served an
    # interstitial instead of the file, and ingesting that would remember a sign-in
    # page as class notes.
    if forced and ctype.startswith("text/html"):
        return {"error": "private"}

    # A server that won't say what it sent (Drive answers octet-stream for most files)
    # leaves the bytes as the only evidence. Only override a header this uninformative —
    # a server that names a real type is more reliable than a guess.
    if ctype in ("", "application/octet-stream", "binary/octet-stream"):
        ctype = _sniff_type(data) or ctype

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
        LOGGER.warning("Classifier helper failed: %s", type(err).__name__)
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
    prompt = f"""You are Memlo, a helpful bot that remembers what happens in
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
    prompt = f"""You are Memlo in a class group chat. Write a SHORT, casual,
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
    prompt = f"""You are Memlo, the study bot for a class group chat. A student
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
        LOGGER.warning("Could not parse quiz response")
        return {}


def grade_answers(quiz_text, reply_text, student) -> dict:
    """Sonnet: mark someone's reply to a quiz, however they chose to write it."""
    prompt = f"""You are Memlo, the study bot for a class group chat. Here is
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
        LOGGER.warning("Could not parse grading response")
        return {"is_answer_attempt": False}


# --- The proactive reminder loop (the "it just knew" moment) --------

@tasks.loop(minutes=5)
async def reminder_loop():
    _health["last_reminder_tick"] = utc_now_iso()
    for guild in bot.guilds:
        settings = get_guild_settings(guild.id)
        if not settings or settings["setup_status"] != "configured":
            continue
        local_now = class_now(guild.id)
        if local_now.hour not in REMIND_HOURS or local_now.minute >= 15:
            continue
        slot = f"{local_now.date().isoformat()}:{local_now.hour}"
        if _reminder_slots.get(guild.id) == slot:
            continue
        _reminder_slots[guild.id] = slot
        for d in get_due_soon_unreminded(guild.id):
            channel = bot.get_channel(d["channel_id"])
            if channel is None:
                continue
            notes = get_notes(d["channel_id"])
            text = await asyncio.to_thread(compose_reminder, d, notes)
            await channel.send(text)
            mark_reminded(d["id"])
            record_event("first_reminder", guild.id, once=True)
            LOGGER.info("Reminder sent: guild=%s deadline_id=%s", guild.id, d["id"])


@reminder_loop.before_loop
async def before_reminder_loop():
    await bot.wait_until_ready()   # don't start reminding until we're connected


@tasks.loop(hours=6)
async def backup_loop():
    try:
        target = await asyncio.to_thread(backup_database)
        LOGGER.info("Database backup completed: %s", target)
    except Exception as exc:
        LOGGER.exception("Database backup failed")
        record_operational_error("backup_loop", exc)


@backup_loop.before_loop
async def before_backup_loop():
    await bot.wait_until_ready()


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
        guild_id = interaction.guild_id
        if not rate_limiter.allow("grade", guild_id, interaction.user.id):
            await interaction.followup.send(
                "You’re moving faster than I can grade — try again in a minute.",
                ephemeral=True)
            return
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
        if result.get("is_answer_attempt") and result.get("feedback"):
            record_event("first_quiz_completed", guild_id, once=True)
        LOGGER.info("Quiz graded privately: guild=%s user=%s",
                    guild_id, interaction.user.id)


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
    labels = [("deadlines", "deadline"), ("notes", "note"),
              ("materials", "study file"), ("quizzes", "quiz")]
    parts = [f'{counts[key]} {word}{"" if counts[key] == 1 else "s"}'
             for key, word in labels if counts.get(key)]
    return ", ".join(parts) if parts else "nothing"


class ForgetView(discord.ui.View):
    """Confirm/cancel on a deletion request. Deleting is irreversible, so we count
    first, show what's about to go, and only delete on an explicit confirm — and only
    from the person who asked."""

    def __init__(self, requester_id, guild_id, author_name=None, channel_id=None):
        super().__init__(timeout=120)   # transient; expires if left unanswered
        self.requester_id = requester_id
        self.guild_id = guild_id
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
            forget_user_by_id, self.requester_id, self.guild_id)
        # Also remove safe-to-identify legacy rows from this channel. Legacy rows
        # have no Discord user ID; limiting this fallback to author_id IS NULL avoids
        # deleting a current user's data merely because display names match.
        if self.author_name and self.channel_id:
            legacy = await asyncio.to_thread(
                forget_user, self.author_name, self.channel_id)
            for key, value in legacy.items():
                counts[key] = counts.get(key, 0) + value
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=f"🧹 Done — I've deleted {_forget_summary(counts)} linked to you "
                    "in this server. That can't be undone.",
            view=self)
        LOGGER.info("User data deleted: guild=%s user=%s counts=%s",
                    self.guild_id, self.requester_id, counts)

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


# --- Slash-command onboarding and privacy controls -----------------

def _is_admin(interaction):
    member = interaction.user
    return bool(interaction.guild and isinstance(member, discord.Member)
                and member.guild_permissions.manage_guild)


async def _require_admin(interaction):
    if _is_admin(interaction):
        return True
    await interaction.response.send_message(
        "You need **Manage Server** permission to do that.", ephemeral=True)
    return False


@tree.command(name="setup", description="Choose a class scope and timezone for Memlo")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(
    timezone_name="IANA timezone, such as America/Chicago or Europe/London",
    category="Track every text channel in this class category",
    channel="Or track one class text channel",
)
async def setup_command(interaction: discord.Interaction,
                        timezone_name: str = DEFAULT_TIMEZONE,
                        category: discord.CategoryChannel = None,
                        channel: discord.TextChannel = None):
    if not await _require_admin(interaction):
        return
    if (category is None) == (channel is None):
        await interaction.response.send_message(
            "Choose exactly one **category** or one **channel**. You can add more "
            "later with `/track-category` or `/track-channel`.", ephemeral=True)
        return
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        await interaction.response.send_message(
            "I don’t recognize that timezone. Use an IANA name like "
            "`America/Chicago`, `America/New_York`, or `Europe/London`.",
            ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    configure_guild(interaction.guild_id, timezone_name, interaction.user.id)
    if category is not None:
        activate_category(category.id, interaction.guild_id, category.name,
                          interaction.user.display_name, interaction.user.id)
        scope = f"the **{category.name}** category"
        scope_type = "category"
    else:
        activate_channel(channel.id, interaction.guild_id, channel.name,
                         interaction.user.id)
        scope = channel.mention
        scope_type = "channel"
    record_event("setup_complete", interaction.guild_id,
                 {"scope_type": scope_type, "timezone": timezone_name}, once=True)
    await interaction.followup.send(
        f"✅ Memlo is ready. I’ll only read {scope}, using `{timezone_name}` "
        "for dates and reminders. Everything else stays ignored.", ephemeral=True)


@tree.command(name="track-category", description="Add a class category to Memlo")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def track_category_command(interaction: discord.Interaction,
                                 category: discord.CategoryChannel):
    if not await _require_admin(interaction):
        return
    settings = get_guild_settings(interaction.guild_id)
    if not settings or settings["setup_status"] != "configured":
        await interaction.response.send_message(
            "Run `/setup` first so I know this server’s timezone.", ephemeral=True)
        return
    activate_category(category.id, interaction.guild_id, category.name,
                      interaction.user.display_name, interaction.user.id)
    await interaction.response.send_message(
        f"✅ Now tracking the **{category.name}** category.", ephemeral=True)


@tree.command(name="track-channel", description="Add one class channel to Memlo")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def track_channel_command(interaction: discord.Interaction,
                                channel: discord.TextChannel):
    if not await _require_admin(interaction):
        return
    settings = get_guild_settings(interaction.guild_id)
    if not settings or settings["setup_status"] != "configured":
        await interaction.response.send_message(
            "Run `/setup` first so I know this server’s timezone.", ephemeral=True)
        return
    activate_channel(channel.id, interaction.guild_id, channel.name,
                     interaction.user.id)
    await interaction.response.send_message(
        f"✅ Now tracking {channel.mention}.", ephemeral=True)


@tree.command(name="untrack-category", description="Stop Memlo reading a category")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def untrack_category_command(interaction: discord.Interaction,
                                   category: discord.CategoryChannel):
    if not await _require_admin(interaction):
        return
    deactivate_category(category.id)
    await interaction.response.send_message(
        f"🛑 Stopped tracking the **{category.name}** category.", ephemeral=True)


@tree.command(name="untrack-channel", description="Stop Memlo reading a channel")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def untrack_channel_command(interaction: discord.Interaction,
                                  channel: discord.TextChannel):
    if not await _require_admin(interaction):
        return
    deactivate_channel(channel.id)
    await interaction.response.send_message(
        f"🛑 Stopped tracking {channel.mention}.", ephemeral=True)


@tree.command(name="setup-status", description="Show Memlo’s configured scopes")
@app_commands.guild_only()
async def setup_status_command(interaction: discord.Interaction):
    settings = get_guild_settings(interaction.guild_id)
    if not settings or settings["setup_status"] != "configured":
        await interaction.response.send_message(
            "Memlo is not set up yet, so I’m reading **nothing**. An admin can "
            "run `/setup` to choose a class channel or category.", ephemeral=True)
        return
    categories = get_active_category_rows(interaction.guild_id)
    conn = db()
    channels = conn.execute(
        "SELECT name FROM tracked_channels WHERE guild_id = ? ORDER BY name",
        (interaction.guild_id,)).fetchall()
    conn.close()
    scopes = [f"category: **{row['name']}**" for row in categories]
    scopes += [f"channel: **#{row['name']}**" for row in channels]
    await interaction.response.send_message(
        f"Timezone: `{settings['timezone']}`\nTracked scopes:\n" +
        ("\n".join(f"• {scope}" for scope in scopes) if scopes else "• none"),
        ephemeral=True)


@tree.command(name="delete-my-data", description="Delete your Memlo data in this server")
@app_commands.guild_only()
async def delete_my_data_command(interaction: discord.Interaction):
    counts = await asyncio.to_thread(
        count_user_data_by_id, interaction.user.id, interaction.guild_id)
    legacy = await asyncio.to_thread(
        count_user_data, interaction.user.display_name, interaction.channel_id)
    for key, value in legacy.items():
        counts[key] = counts.get(key, 0) + value
    if sum(counts.values()) == 0:
        await interaction.response.send_message(
            "I don’t have anything stored that’s linked to you in this server.",
            ephemeral=True)
        return
    await interaction.response.send_message(
        f"I found {_forget_summary(counts)} linked to you. Delete it permanently?",
        view=ForgetView(interaction.user.id, interaction.guild_id,
                        interaction.user.display_name, interaction.channel_id),
        ephemeral=True)


class DeleteServerView(discord.ui.View):
    def __init__(self, requester_id, guild_id):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.guild_id = guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id or not _is_admin(interaction):
            await interaction.response.send_message(
                "Only the admin who requested this deletion can confirm it.",
                ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Delete this server’s Memlo data", emoji="🗑️",
                       style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        counts = await asyncio.to_thread(delete_server_data, self.guild_id)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="🗑️ All Memlo memory and configuration for this server has "
                    "been deleted. I’m now tracking nothing; run `/setup` to start over.",
            view=self)
        LOGGER.warning("Server data deleted: guild=%s counts=%s", self.guild_id, counts)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="Canceled. Nothing was deleted.", view=self)


@tree.command(name="delete-server-data",
              description="Permanently delete all Memlo data for this server")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def delete_server_data_command(interaction: discord.Interaction):
    if not await _require_admin(interaction):
        return
    await interaction.response.send_message(
        "This permanently deletes every saved deadline, note, study digest, quiz, "
        "setting, and telemetry event for this server. Continue?",
        view=DeleteServerView(interaction.user.id, interaction.guild_id),
        ephemeral=True)


@tree.command(name="invite", description="Get the link to add Memlo to another server")
async def invite_command(interaction: discord.Interaction):
    install_url = os.environ.get(
        "DISCORD_INSTALL_URL",
        f"https://discord.com/oauth2/authorize?client_id={bot.user.id}")
    record_event("referral_link_shown", interaction.guild_id,
                 {"source": "invite_command"})
    await interaction.response.send_message(
        f"Add Memlo to another class server: {install_url}", ephemeral=True)


@tree.command(name="privacy", description="See how Memlo handles and deletes data")
async def privacy_command(interaction: discord.Interaction):
    privacy_url = os.environ.get(
        "PRIVACY_URL",
        "https://getmemlo.com/privacy",
    )
    terms_url = os.environ.get(
        "TERMS_URL",
        "https://getmemlo.com/terms",
    )
    links = []
    if privacy_url:
        links.append(f"Privacy: {privacy_url}")
    if terms_url:
        links.append(f"Terms: {terms_url}")
    text = ("Memlo only reads admin-selected class channels. It stores structured "
            "class memory and never sells data or sends message text to analytics. "
            "Use `/delete-my-data` any time.")
    if links:
        text += "\n" + "\n".join(links)
    await interaction.response.send_message(text, ephemeral=True)


@tree.command(name="support", description="Get help or report a privacy issue")
async def support_command(interaction: discord.Interaction):
    support_url = os.environ.get(
        "SUPPORT_SERVER_URL", "https://discord.gg/7UT2aPf5Us"
    )
    if not support_url:
        await interaction.response.send_message(
            "Memlo support is not configured yet. Email ehsieh175@gmail.com for help, "
            "privacy requests, or security reports.",
            ephemeral=True)
        return
    await interaction.response.send_message(
        f"Join the Memlo support server: {support_url}\n"
        "For private data or security requests, email ehsieh175@gmail.com.",
        ephemeral=True)


# --- Discord event handlers ----------------------------------------

@bot.event
async def on_ready():
    global _view_registered, _commands_synced
    _health["last_ready"] = utc_now_iso()
    LOGGER.info("Logged in as %s; connected to %s guilds", bot.user, len(bot.guilds))
    for guild in bot.guilds:
        ensure_guild_settings(guild.id)
        record_event("install", guild.id, {"source": "existing_or_startup"}, once=True)
    if not reminder_loop.is_running():
        reminder_loop.start()
    if not backup_loop.is_running():
        backup_loop.start()
    await start_health_server()
    if not _commands_synced:
        try:
            await tree.sync()
            _commands_synced = True
            LOGGER.info("Slash commands synced")
        except Exception as exc:
            LOGGER.exception("Slash-command sync failed")
            record_operational_error("command_sync", exc)
    # Re-attach the quiz button handler, so buttons on quizzes posted before the
    # last restart still work. on_ready fires again on every reconnect, hence the flag.
    if not _view_registered:
        bot.add_view(QuizView())
        _view_registered = True


@bot.event
async def on_guild_join(guild):
    ensure_guild_settings(guild.id)
    record_event("install", guild.id, {"source": "guild_join"}, once=True)
    LOGGER.info("Installed in guild=%s", guild.id)


@bot.event
async def on_guild_remove(guild):
    counts = await asyncio.to_thread(delete_server_data, guild.id, False)
    record_event("removal", None, {"count": 1})
    LOGGER.info("Removed from guild=%s; data deleted=%s", guild.id, counts)


@bot.event
async def on_error(event_method, *args, **kwargs):
    exc = sys.exc_info()[1] or RuntimeError(f"Unhandled {event_method} error")
    LOGGER.exception("Unhandled Discord event error: %s", event_method)
    record_operational_error(f"discord_event:{event_method}", exc)


@tree.error
async def on_app_command_error(interaction, error):
    guild_id = interaction.guild_id
    LOGGER.error("Slash command failed: command=%s guild=%s type=%s",
                 getattr(interaction.command, "name", "unknown"), guild_id,
                 type(error).__name__)
    record_operational_error(
        f"slash:{getattr(interaction.command, 'name', 'unknown')}", error, guild_id)
    message = "Something went wrong. I logged a content-free error marker so it can be fixed."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


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
        LOGGER.info("Material groups combined")

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
            message.channel.id, message.jump_url, result["topic"], quiz_group,
            message.guild.id, message.author.id)
        ingested.append({"filename": name, "topic": result["topic"]})
        LOGGER.info("File ingested: guild=%s channel=%s",
                    message.guild.id, message.channel.id)

    if not ingested:
        return

    # One confirmation for the whole batch — two separate "Got X" lines was the
    # thing that made it look like the two pages had been read in isolation.
    topics = list(dict.fromkeys(item["topic"] for item in ingested))
    files = ", ".join(f"**{item['filename']}**" for item in ingested)
    together = " (I'll treat them as one set)" if len(ingested) > 1 else ""
    await message.channel.send(
        f"📥 Got {files} — {', '.join(topics)}. I've studied {'them' if len(ingested) > 1 else 'it'}"
        f"{together}. Try: `@Memlo quiz us on it`")

    relation = await asyncio.to_thread(
        relate_upload, message.clean_content.strip(), topics[0], prior_groups)
    if not relation["group"]:
        return

    match = next(g for g in prior_groups if g["quiz_group"] == relation["group"])
    label = f'the earlier **{match["topics"]}** material'
    if relation["combine"] == "yes":
        await asyncio.to_thread(merge_quiz_groups, quiz_group, relation["group"])
        await message.channel.send(f"🔗 Added to {label} — I'll quiz from both.")
        LOGGER.info("Material groups combined from caption: guild=%s", message.guild.id)
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
                LOGGER.warning("Could not read a shared link: error=%s", fetched["error"])
            continue

        if not await asyncio.to_thread(
                looks_like_study_material, caption, url, fetched["snippet"]):
            LOGGER.info("Shared link skipped as non-study material: guild=%s",
                        message.guild.id)
            continue

        result = await asyncio.to_thread(
            ingest_attachment, fetched["label"], fetched["media_type"],
            fetched["data"], fetched["is_text"])
        # The link itself is the source we store, not the message's jump_url — so
        # "where's that from?" can point back at the actual doc.
        await asyncio.to_thread(
            save_material, fetched["label"], result["digest"],
            message.author.display_name, message.channel.id, url,
            result["topic"], quiz_group, message.guild.id, message.author.id)
        LOGGER.info("Link ingested: guild=%s channel=%s",
                    message.guild.id, message.channel.id)
        await message.channel.send(
            f"🔗 Read **{fetched['label']}** — {result['topic']}. I've studied it. "
            "Try: `@Memlo quiz us on it`")


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
    just as often as the bot user itself — without this, "@Memlo what's due?"
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
        settings = await asyncio.to_thread(get_guild_settings, message.guild.id)
        rows = await asyncio.to_thread(get_active_category_rows, message.guild.id)
        if not settings or settings["setup_status"] != "configured":
            await message.reply(
                "I’m not set up here, so I’m reading **nothing**. A server admin can "
                "run `/setup` to choose a class category or channel.")
        else:
            names = "\n".join(f"  • {r['name']}" for r in rows)
            await message.reply(
                "📋 Use `/setup-status` for the complete scope. Tracked categories:\n"
                f"{names or '  • none'}")
        return True

    category = _category_of(message.channel)
    if category is None:
        await message.reply(
            "This channel isn't inside a category. Put it under one (like a **7th "
            "Grade** category) first — I switch tracking on a whole category at once.")
        return True

    if is_activate:
        settings = await asyncio.to_thread(get_guild_settings, message.guild.id)
        if not settings or settings["setup_status"] != "configured":
            await message.reply(
                "Setup now requires an explicit timezone and scope. Please run `/setup`; "
                "until then I’m reading nothing.")
            return True
        await asyncio.to_thread(
            activate_category, category.id, message.guild.id, category.name,
            message.author.display_name, message.author.id)
        channels = ", ".join(f"#{c.name}" for c in category.text_channels) or "(none yet)"
        await message.reply(
            f"✅ Now tracking the **{category.name}** category: {channels}. "
            "Each channel keeps its own separate memory — a deadline in one class "
            "never shows up in another.")
        LOGGER.info("Category activated: guild=%s category=%s",
                    message.guild.id, category.id)
    else:  # deactivate
        await asyncio.to_thread(deactivate_category, category.id)
        await message.reply(
            f"🛑 Stopped tracking the **{category.name}** category — I'll ignore its "
            "channels now. (What I already remembered is still there; say "
            "`@Memlo forget me` in a channel to clear your own.)")
        LOGGER.info("Category deactivated: guild=%s category=%s",
                    message.guild.id, category.id)
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
        category.id if category else None,
        message.channel.id)
    if not tracked:
        # If they actually @mentioned us in a non-class channel, don't just vanish —
        # point them at their class channel once. Otherwise stay silent.
        if _is_for_bot(message) and message.clean_content.strip():
            settings = get_guild_settings(message.guild.id) if message.guild else None
            if not settings or settings["setup_status"] != "configured":
                await message.reply(
                    "I’m not set up yet, so I’m reading nothing. A server admin can "
                    "run `/setup` to choose a class channel. 🔒")
            else:
                await message.reply(
                    "I only keep track of admin-selected class channels — ask me in "
                    "one of those channels. 📚")
        return

    urls = extract_urls(message.content)
    if (message.attachments or urls) and not rate_limiter.allow(
            "ingest", message.guild.id, message.author.id):
        await message.reply(
            "That’s a lot of study material at once — wait a few minutes, then try again.")
        return

    # Files first, always. Someone can drop a study guide while replying to the
    # bot, and Discord counts a reply as a mention — so this can't live inside
    # either branch below or the upload gets swallowed.
    if message.attachments:
        await read_attachments(message)

    # Links are the other way study material arrives. Fetch anything linked and file
    # the keepers — before the branches below, for the same reason files are read
    # first: a doc can be shared in the same breath as a question to the bot.
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
            if not rate_limiter.allow(
                    "grade", message.guild.id, message.author.id):
                await message.reply(
                    "You’re moving faster than I can grade — try again in a minute.")
                return
            result = await asyncio.to_thread(
                grade_answers, quiz_text, text, message.author.display_name)
            # They might be replying to the quiz without answering it ("lol",
            # "quiz us again") — in that case fall through and handle it normally.
            if result.get("is_answer_attempt") and result.get("feedback"):
                await message.reply(result["feedback"])
                record_event("first_quiz_completed", message.guild.id, once=True)
                LOGGER.info("Quiz graded: guild=%s user=%s",
                            message.guild.id, message.author.id)
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
                count_user_data_by_id, message.author.id, message.guild.id)
            legacy = await asyncio.to_thread(
                count_user_data, message.author.display_name, message.channel.id)
            for key, value in legacy.items():
                counts[key] = counts.get(key, 0) + value
            if sum(counts.values()) == 0:
                await message.reply(
                    "I don't have anything stored from you in this server — "
                    "nothing to forget. 👍")
                return
            await message.reply(
                f"You've got {_forget_summary(counts)} stored in this server. "
                "Delete all of it? This can't be undone.",
                view=ForgetView(message.author.id, message.guild.id,
                                message.author.display_name, message.channel.id))
            return
        # "quiz" -> teach mode; anything else -> answer from memory.
        if "quiz" in text.lower():
            if not rate_limiter.allow("quiz", message.guild.id, message.author.id):
                await message.reply(
                    "You’ve made a few quizzes already — give me five minutes, then try again.")
                return
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
                      f'{quiz.get("answer_key", "")}',
                      message.guild.id, message.author.id)
        else:
            # "ok", "thanks", a lone emoji — replying to us isn't the same as asking
            # us something, and Discord makes every reply look like a mention.
            if not await asyncio.to_thread(wants_a_reply, text):
                LOGGER.info("No reply needed: guild=%s user=%s",
                            message.guild.id, message.author.id)
                return
            if not rate_limiter.allow("answer", message.guild.id, message.author.id):
                await message.reply(
                    "I’ve answered a bunch just now — give me a minute, then ask again.")
                return
            deadlines = get_upcoming_deadlines(message.channel.id, message.guild.id)
            notes = get_notes(message.channel.id)
            materials = get_materials(message.channel.id)
            answer = await asyncio.to_thread(
                answer_question, text, deadlines, notes, materials,
                _real_roles(message.author))
            await message.reply(answer)
            record_event("first_answer", message.guild.id, once=True)
        return

    if not text:
        return

    poster_roles = _real_roles(message.author)
    if not should_analyze_message(message.content, poster_roles):
        return
    result = await asyncio.to_thread(
        analyze_message, message.content, poster_roles, message.guild.id)

    if not result.get("is_important"):
        LOGGER.info("Classifier skipped message: guild=%s", message.guild.id)
        return

    author = message.author.display_name
    link = message.jump_url
    # Who it's for: a role pinged on the message wins; then a grade named in the text;
    # then the sender's own grade for their own-class deadlines. Blank = everyone.
    audience = audience_from_message(
        message, result.get("audience", ""), result.get("poster_grade", ""))

    if result.get("type") == "deadline" and result.get("due_date"):
        save_deadline(result["summary"], result["due_date"], author,
                      message.channel.id, link, audience,
                      message.guild.id, message.author.id)
        record_event("first_deadline_saved", message.guild.id, once=True)
        LOGGER.info("Deadline saved: guild=%s channel=%s due=%s",
                    message.guild.id, message.channel.id, result["due_date"])
    else:
        save_note(result.get("summary", ""), message.content, author,
                  message.channel.id, link, audience,
                  message.guild.id, message.author.id)
        LOGGER.info("Note saved: guild=%s channel=%s",
                    message.guild.id, message.channel.id)


# --- Go -------------------------------------------------------------

if __name__ == "__main__":
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("Set your DISCORD_TOKEN environment variable first.")
    setup_logging()
    init_db()
    bot.run(token)
