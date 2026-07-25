# ClassBrain — Privacy Policy

**Last updated: July 22, 2026**

ClassBrain ("the bot", "we", "us") is a Discord bot that acts as a shared memory for
a class group chat: it notices deadlines and announcements, answers questions about
them, and makes study quizzes from material people share. This policy explains what
data it handles and why.

> **Plain-language summary:** ClassBrain reads the messages in the channels it's
> added to so it can remember deadlines and answer questions. To do that, message
> text and any study files you upload are sent to Anthropic (the company behind
> Claude) for processing, and a summary is stored in a small database on the
> operator's computer. It does not sell your data, show ads, or share it with anyone
> beyond what's described here. You can ask to have your data deleted at any time.

---

## Who runs this bot

ClassBrain is operated by **Emma Hsieh** ("the operator"). It is a small, personal
project, not a company.

**Contact for any privacy question or data-deletion request:** message the operator
on Discord at **[YOUR DISCORD USERNAME]**, or ask in your class server.
*(You can also add an email here if you prefer.)*

---

## What information ClassBrain handles

ClassBrain only processes activity in the Discord servers and channels it has been
explicitly added to. It does not read direct messages or other servers.

**Message content.** To decide whether a message is worth remembering (a deadline,
an announcement, an instruction like "notes are allowed"), the bot reads the text of
messages posted in channels where it is present. This is done using Discord's
"Message Content" permission.

**Information it stores.** When a message is judged important, or when you interact
with the bot, it saves the following to a local database:

- A short **summary** of the deadline, announcement, or note.
- For saved notes, the **original text** of the message that triggered it.
- The **display name** of the person who posted it.
- The **channel ID** and a **link** back to the original message.
- **Due dates** it detected, and the time it learned about them.
- **Study materials** you upload (images, PDFs, or text files): the bot reads the
  file, writes a study digest, and stores that digest along with the file name.
- **Quizzes** it generates, including the answer keys, and the feedback it gives on
  answers you submit.

**What it does *not* collect.** ClassBrain does not collect your email address, phone
number, payment information, IP address, Discord password, or any messages from
channels it hasn't been added to. It does not track you across servers.

---

## How your information is used

Your information is used **only** to make the bot work:

- Detecting and remembering deadlines, announcements, and instructions.
- Answering questions you ask it (e.g. "what's due this week?").
- Sending unprompted reminders when something is due soon.
- Creating quizzes from shared study material and grading replies.

ClassBrain does **not** use your data for advertising, profiling, or training its own
models, and it is **not sold or rented** to anyone.

---

## Third parties who process your data

Making the bot work involves two services:

**Discord.** The platform the bot runs on. Your use of Discord is governed by
[Discord's Privacy Policy](https://discord.com/privacy).

**Anthropic (Claude).** To understand messages, read uploaded study files, and write
quizzes and answers, ClassBrain sends the relevant **message text and the contents of
files you upload** to Anthropic's Claude API for processing. Anthropic's handling of
that data is governed by their
[Privacy Policy](https://www.anthropic.com/legal/privacy) and
[Commercial Terms](https://www.anthropic.com/legal/commercial-terms); by default,
data sent to the Claude API is not used to train their models. No other third parties
receive your data.

---

## Where your data is stored, and for how long

Stored data lives in a single database file on the **operator's own computer**, not on
a public cloud server.

**Retention.** ClassBrain currently keeps saved deadlines, notes, materials, and
quizzes **until the operator deletes them**. There is no automatic expiry. If you want
something removed, use the contact above and the operator will delete it.

---

## Your choices and rights

- **See what's stored about you:** ask the operator (contact above).
- **Delete your data yourself, any time:** message the bot **`@ClassBrain forget me`**
  in a channel and it will delete everything you've contributed to that channel, after
  a confirmation step. You can also ask the operator to remove specific items or the
  entire database. Removing the bot from a server also stops any further collection
  from it.
- **Stop collection:** an admin can remove ClassBrain from the server at any time, or
  restrict which channels it can see using Discord's channel permissions.

---

## Students and younger users

ClassBrain is built for use in class group chats. Discord's own Terms of Service
require users to be at least **13 years old** (or older where local law sets a higher
age). ClassBrain is intended only for users who meet Discord's minimum age. If you
believe someone under that age has had their information stored by the bot, contact
the operator and it will be deleted. This bot is not directed at children under 13 and
does not knowingly collect their information.

---

## Security

The database is kept on the operator's personal device and is not exposed publicly.
That said, this is a small personal project, not an enterprise service — please don't
share anything through ClassBrain that you would consider highly sensitive.

---

## Changes to this policy

If this policy changes, the "Last updated" date above will change. Significant changes
will be announced in the channels where the bot is active.

---

## Contact

Questions, concerns, or deletion requests: **[YOUR CONTACT EMAIL]**
