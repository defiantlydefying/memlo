# Memlo Privacy Policy

Published at: https://getmemlo.com/privacy

**Last updated: October 25, 2026**

Memlo is a Discord bot that acts as shared memory for class channels. It can
remember deadlines and announcements, answer questions from that memory, remind a
class before work is due, and make quizzes from study material.

Contact: **ehsieh175@gmail.com**

## The short version

- Memlo reads only the class channels or categories a server administrator
  explicitly selects during setup. A new installation reads nothing until setup is
  complete.
- Manual deadlines and reminders work without AI. When AI features are enabled,
  relevant message text and study files are sent to Anthropic's Claude API.
  Class memory is stored in Memlo's SQLite database.
- Product analytics contain event names and operational counts only. Message text,
  filenames, study content, and questions are never sent to analytics.
- Memlo does not sell personal data, show targeted ads, or train its own models on
  class content.
- A user can run `/delete-my-data` or `@Memlo forget me`; a server administrator can run
  `/delete-server-data`.

## What Memlo processes

### Selected Discord channels

With AI enabled, Memlo processes relevant messages, attachments, links, author
information, roles, channel identifiers, and message identifiers in channels selected
by a server administrator. This requires Discord's Message Content intent. Without
AI, slash commands can manage deadlines without reading passive class messages.

### Information stored as class memory

When content is relevant to the product, Memlo may store:

- a short deadline, announcement, or instruction summary;
- the original text behind a saved announcement or note;
- a due date and the time the item was learned;
- the contributor's Discord user ID and current display name;
- Discord server and channel IDs and a link to the source message;
- a digest of a shared image, PDF, text file, Google document, or supported webpage;
- generated quizzes, answer keys, and grading feedback; and
- the class or role an item applies to, when available.

Text uploads are retained as extracted text so quizzes can use the actual source
wording. Images and PDFs are processed into source-focused study text. Saved material
includes a topic, filename, source reference, and contributor information. Memlo does
not keep a separate permanent binary copy of uploaded images or PDFs.

Quizzes are private in Discord by default. Creating or grading one with AI still
sends relevant study content, questions, and submitted answers to Anthropic. Source
checks reduce unsupported questions but do not guarantee accuracy.

### Privacy-preserving product analytics

Memlo records a limited set of events needed to understand whether the product
works: installation, completed setup, first remembered deadline, first answer, first
reminder, first completed quiz, referral-link display, and removal. These events may
include the Discord server ID, timezone, scope type, source, or a numeric count.

Analytics do **not** include message text, attachments, filenames, study digests,
questions, answers, display names, or Discord user IDs.

### Operational information

Memlo records timestamps and content-free error markers such as the failed component
and exception type. Application logs use Discord IDs and internal record IDs where
needed for diagnosis, not message content.

## How the information is used

Information is used only to:

- detect and remember class deadlines, announcements, and instructions;
- answer questions from a selected channel's memory;
- deliver reminders;
- process study material, create quizzes, and grade submitted answers;
- enforce rate limits and prevent abuse;
- keep the service reliable and backed up; and
- measure activation, retention, and reliability using content-free events.

Memlo does not sell or rent data, use it for targeted advertising, build advertising
profiles, or train its own machine-learning models on it.

## Service providers

**Discord** delivers messages and interactions to the bot. Discord's processing is
governed by the [Discord Privacy Policy](https://discord.com/privacy).

**Anthropic** processes relevant message text, study material, quiz questions, and
practice answers when AI features are enabled. Manual deadline commands and
scheduled reminders do not call the AI API. Anthropic's processing is governed by its
[Privacy Policy](https://www.anthropic.com/legal/privacy) and
[Commercial Terms](https://www.anthropic.com/legal/commercial-terms).

**Bot host.** The operator's computer or hosting server holds the SQLite database,
operational logs, and rotating backups. Moving the bot to a hosting provider does
not change the uses described here. The website is hosted separately from the bot.

No other provider receives class content unless this policy is updated first.

## Retention and deletion

Class memory remains until it is deleted, the bot is removed from the server, or the
operator removes it as part of maintenance. The service creates a database backup
every six hours and retains the latest 14 backup snapshots. Deleted records may remain
in those rotating backups until the relevant snapshots expire.

- Run **`/delete-my-data`** to permanently delete records attributed to your Discord
  user ID within that server after confirmation.
- A server administrator can run **`/delete-server-data`** to delete all class memory,
  settings, and server-linked telemetry after confirmation.
- Removing Memlo from a server stops collection and automatically deletes that
  server's stored content and configuration. A content-free aggregate removal event
  may remain.
- You may also contact **ehsieh175@gmail.com** with an access or deletion request.

## Choices and controls

Server administrators choose the channels or categories Memlo can read and can
remove a scope at any time. `/setup-status` shows the configured timezone and tracked
scopes. Removing the bot stops all future processing.

## Students and younger users

Memlo is intended only for people who meet Discord's minimum age requirement,
generally 13 or older and higher where local law requires it. It is not directed to
children under 13. If you believe information from an underage user was processed,
contact **ehsieh175@gmail.com** so it can be deleted.

## Security

Memlo uses explicit channel opt-in, least-privilege Discord permissions, rate limits,
host filtering for fetched links, size limits, automated backups, health monitoring,
and restricted server access. No internet service can guarantee perfect security, so
users should not share highly sensitive personal information through Memlo.

## Changes

Material changes will update the date at the top of this policy and will be announced
through Memlo's support channel or server where practical.
