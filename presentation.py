"""Memlo's Discord presentation and local command interpretation.

These helpers have no database or model dependency, so the everyday interface is
predictable even when the AI service is unavailable.
"""

import re
from datetime import date

import discord


INK_BLUE = 0x5276C7
DEADLINE_AMBER = 0xD5A441
STUDY_TEAL = 0x409B95
ERROR_ROSE = 0xC66B79
MENTIONS_OFF = discord.AllowedMentions.none()


def clip(value, limit):
    text = str(value or "")
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def plain(value, limit=200):
    """User filenames and labels should read as text, not Discord markup."""
    return clip(discord.utils.escape_markdown(
        discord.utils.escape_mentions(clip(value, limit))), limit)


def card(title, description="", *, color=INK_BLUE, footer=None):
    embed = discord.Embed(title=clip(title, 256),
                          description=clip(description, 4096), color=color)
    embed.set_footer(text=clip(footer or "Memlo · Class memory, on your terms", 2048))
    return embed


def command_text(text, bot_name="Memlo"):
    """Remove only a leading bot address; don't replace names inside a question."""
    text = re.sub(r"^\s*<@!?\d+>\s*", "", text or "")
    return re.sub(rf"^\s*@{re.escape(bot_name)}(?:\b|(?=\s))[:,]?\s*", "",
                  text, count=1, flags=re.IGNORECASE).strip()


def is_acknowledgment(text):
    words = re.findall(r"[\w']+", text.casefold())
    return not words or " ".join(words) in {
        "ok", "okay", "kk", "k", "thanks", "thank you", "ty", "tysm",
        "thx", "lol", "lmao", "nice", "cool", "got it", "alright", "bet",
        "thank you so much", "ok thanks", "okay thanks", "sounds good",
    }


def is_quiz_request(text):
    """'Quiz me' is an action; 'when is the quiz?' is a memory question."""
    text = text.strip().casefold()
    if re.search(r"\b(?:don[’']?t|do\s+not|stop|no)\s+(?:(?:make|create|generate|a)\s+)*quiz", text):
        return False
    if re.search(r"\b(?:when|what\s+(?:day|date|time)|is\s+(?:the|our))\b", text):
        return False
    return bool(re.search(
        r"\bquiz\s+(?:me|us|everyone|the\s+class)\b|"
        r"\b(?:make|create|generate|start|give|do|take)\b.{0,70}\bquiz\b|"
        r"\b(?:use|turn)\b.{0,100}\b(?:into|for|make)\s+(?:a\s+)?quiz\b|"
        r"^(?:please\s+)?quiz(?:\s+(?:on|about|from)\b.*)?[.!?]*$", text))


def split_message(text, limit=1900):
    """Split at paragraphs/lines where possible; never exceed Discord's 2000 cap."""
    remaining = str(text or "").strip()
    chunks = []
    while len(remaining) > limit:
        boundary = remaining.rfind("\n", 0, limit + 1)
        if boundary < limit // 2:
            boundary = remaining.rfind(" ", 0, limit + 1)
        if boundary < limit // 2:
            boundary = limit
        chunks.append(remaining[:boundary])
        remaining = remaining[boundary:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def due_label(iso_date, today):
    try:
        due = date.fromisoformat(iso_date)
    except (ValueError, TypeError):
        return "Date needs checking"
    days = (due - today).days
    if days == 0:
        return "Today"
    if days == 1:
        return "Tomorrow"
    return due.strftime("%a, %b ") + str(due.day)


def source_link(url):
    """Only wrap normal HTTPS/HTTP sources; reject markdown-breaking characters."""
    if (isinstance(url, str) and len(url) <= 600
            and url.startswith(("https://", "http://"))
            and not re.search(r"[\s<>()[\]\\]", url)):
        return f"[View source](<{url}>)"
    return ""


def deadline_cards(rows, today, timezone_name):
    if not rows:
        return [card("Nothing due in memory", "Share a deadline in this channel and "
                     "I’ll keep it here. This means I haven’t saved one—not that "
                     "your teacher hasn’t set any.", color=DEADLINE_AMBER)]
    pages = []
    for offset in range(0, len(rows), 5):
        embed = card("Coming up", color=DEADLINE_AMBER,
                     footer=f"Memlo · {timezone_name} · Saved deadlines, not a complete syllabus")
        for row in rows[offset:offset + 5]:
            details = []
            if row["audience"]:
                details.append("For " + plain(row["audience"]))
            if source_link(row["link"]):
                details.append(source_link(row["link"]))
            if "id" in row.keys():
                details.append(f"#{row['id']} · `/reschedule` or `/cancel-deadline`")
            if "revision" in row.keys() and row["revision"]:
                details.append("Updated in Memlo")
            embed.add_field(name=f"{due_label(row['due_date'], today)} · {plain(row['summary'], 170)}",
                            value=clip(" · ".join(details) or "Shared in this channel", 900), inline=False)
        pages.append(embed)
    return pages


def material_cards(groups):
    if not groups:
        return [card("Your study shelf is empty", "Share screenshots, a PDF, a text "
                     "file, or a study link in this channel. Files sent together "
                     "become one study set.", color=STUDY_TEAL)]
    pages = []
    for offset in range(0, len(groups), 5):
        embed = card("Study shelf", "Ask `@Memlo quiz me on <topic>`, or reply to "
                     "an upload with `@Memlo quiz me` to use that exact set.", color=STUDY_TEAL)
        for group in groups[offset:offset + 5]:
            count = group["file_count"]
            embed.add_field(
                name=plain(group["topics"] or "Untitled study set", 200),
                value=clip(f"{count} {'file' if count == 1 else 'files'} · "
                           f"{plain(group['filenames'], 200)}\n{source_link(group['link'])}", 900),
                inline=False)
        pages.append(embed)
    return pages


def help_card():
    embed = card("A little less scrolling. A little more studying.",
                 "I remember what’s shared in your selected class channels. "
                 "Here’s how to use that memory.")
    for name, value in (
        ("Find what matters", "`/add-deadline` — add an assignment with a form, no AI needed\n"
         "`/due` — saved upcoming deadlines, with sources\n"
         "`/materials` — browse the study sets in this channel\n"
         "`@Memlo what’s due tomorrow?` — ask a specific question"),
        ("Practice from your pages", "Share files or a study link, then ask "
         "`@Memlo quiz me on <topic>`, or use `/quiz` to choose a saved set "
         "and difficulty. Slash-command quizzes are private by default. "
         "Reply to an upload to choose that set. "
         "Use **Answer privately** for feedback only you can see."),
        ("You’re in control", "`/setup-status` — see where I’m listening\n"
         "`/reschedule` — correct a saved due date\n"
         "`/cancel-deadline` — stop reminders for a cancelled assignment\n"
         "`/delete-my-data` — remove your saved contributions\n"
         "Admins: `/setup` chooses channels and timezone"),
    ):
        embed.add_field(name=name, value=value, inline=False)
    return embed
