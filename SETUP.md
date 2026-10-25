# Run your own copy of Memlo

This guide is for rebuilding the bot, not for joining the hosted Memlo.
To use the hosted bot, use the invite in [README.md](README.md).

The complete deadline workflow needs **no AI API key**: add assignments with a
form, browse them, reschedule or cancel them, and receive automatic reminders.
AI-based message reading, file reading, answers, quizzes and grading are optional.

## 1. Get the project

Install Python **3.11 or newer** and Git, then run:

```sh
git clone https://github.com/defiantlydefying/memlo.git
cd memlo
python3 setup_memlo.py
```

On Windows, use `py -3 setup_memlo.py` instead of `python3 setup_memlo.py`.
You can also download and unzip the repository, open a terminal in that folder,
and run the setup script there.

The script creates `.venv`, installs the dependencies, and copies `.env.example`
to `.env`. It **never overwrites an existing `.env`**. Direct dependencies are
pinned to the versions used for the bot’s current offline tests.

## 2. Create your Discord application

1. Open the [Discord Developer Portal](https://discord.com/developers/applications)
   and create an application. Give it a name, such as “Memlo Test”.
2. On its **Bot** page, obtain/reset its bot token. Put that token in your local
   `.env` as `DISCORD_TOKEN=...`. Do not paste it into chat or commit it to Git.
3. Under installation/OAuth2 settings, create a **server / Guild Install** link
   using the `bot` and `applications.commands` scopes. This must be an invite for
   **your new application**, not the hosted Memlo invite from the README.
4. Select **View Channels**, **Send Messages**, **Read Message History**, and
   **Embed Links**. The combined permission integer is `84992`. Do not grant
   Administrator or Manage Server to the bot. If you use threads, also allow
   **Send Messages in Threads** in the relevant channels.
5. Open your generated invite link and install the bot in a **fresh, private test
   server** where you have Manage Server permission.

The deadline-only mode does not request Message Content Intent. Leave that
privileged intent off unless you enable the optional AI features below.
See Discord’s [OAuth2 scopes](https://docs.discord.com/developers/topics/oauth2)
and [permission reference](https://docs.discord.com/developers/topics/permissions).

## 3. Configure and start it

Edit `.env` in your editor. For a no-AI copy:

```dotenv
DISCORD_TOKEN=your-own-bot-token
ANTHROPIC_API_KEY=
CLASS_TIMEZONE=America/Chicago
```

Choose an IANA timezone appropriate for your class. `/setup` stores a separate
timezone for each Discord server. The `.env` format is literal `KEY=value` with
optional quotes; quote values containing spaces or `#`. No shell commands or
variable substitutions are evaluated. Existing exported environment variables
take precedence over `.env`; clear an old exported value if it overrides your edits.

Run from the project folder:

```sh
.venv/bin/python bot.py --check
.venv/bin/python bot.py
```

On Windows:

```powershell
.venv\Scripts\python.exe bot.py --check
.venv\Scripts\python.exe bot.py
```

`--check` reports whether a token is set and whether AI mode is enabled; it does
not print secrets, connect to Discord, or create a database. It does **not** verify
that your token is valid or that Discord permissions are correct.

Leave the running terminal open. Memlo is online only while the process is running.
It creates `memlo.db` on first start and preserves it between restarts. `.env`,
the database, logs and `.venv` are ignored by Git. Don’t publish database backups.

## 4. Try the entire no-AI workflow

In your private test server:

1. Create a normal text channel, such as `#memlo-test`.
2. As a server admin, run `/setup`, choose that channel, and enter your timezone.
   The bot tracks nothing before this step.
3. Run `/add-deadline`. Enter an assignment title, tomorrow’s date in `YYYY-MM-DD`
   form, and optionally an audience. The form saves locally and posts a receipt.
4. Run `/due`. Check the title, due date and source link. Submit the same title,
   date and audience again: it should still appear as one assignment.
5. Use `/reschedule` to select the assignment and change its date. Check `/due`
   again. Only the original contributor or an admin may change the shared deadline.
6. Run `/cancel-deadline`, select it, and confirm. It should disappear from `/due`.
7. Add another test deadline. Run `/delete-my-data`, confirm, and check that your
   contribution is removed. Never test deletion against someone’s real class data.

Reminders run at **6 AM and 3 PM in the configured server timezone**, for deadlines
due within two days. Newly added or rescheduled work has a one-hour quiet period,
so an immediate reminder is not expected. Cancelled assignments and untracked
channels do not receive reminders. Reminder scheduling is also covered by offline
tests with a controlled clock; these tests do not post to Discord.

## 5. Optional AI features

Add your own `ANTHROPIC_API_KEY` to `.env`, enable **Message Content Intent** on
your application's Bot page, and restart Memlo. Both settings are needed for the
full passive-reading experience. Model calls can incur charges on your account.

You can then share supported study files or public study links, ask questions with
`@Memlo`, and use `/quiz`. `DETECTOR_MODEL` and `ANSWER_MODEL` can override the
model IDs if needed for your Anthropic account. Keep the key out of screenshots,
demo recordings, Git commits and support messages.

## Tests and troubleshooting

```sh
.venv/bin/python -m unittest discover -v
```

The offline suite uses a dummy AI key, temporary databases, and mocked network
calls. `test_routing.py` is a separate opt-in live AI test; discovery does not run it.

- **No slash commands:** check that the bot is online, installed to the server
  with `applications.commands`, and that command synchronization succeeded.
- **Commands say “untracked”:** run `/setup` for the channel first.
- **Forms save but there’s no public receipt:** check Send Messages and Embed Links
  in the selected channel. Use `/due` to see whether the deadline was saved.
- **Disallowed intent error:** without AI, clear the AI key and restart. With AI,
  enable Message Content Intent for your own app before restarting.
- **Reminders seem missing:** check `/setup-status` for the timezone, the one-hour
  quiet period, the due date, and whether that deadline was already reminded about.
- **Keeping it online:** the bot process must stay running for people to try it.
  You can host it on a Linux server such as Hack Club Nest. Install the same
  requirements in a virtual environment, keep `.env` private, and use a service
  manager such as systemd for automatic startup and crash recovery. Run only one
  bot instance against a SQLite database, keep backups off the host, and leave
  the health endpoint bound to loopback rather than exposing it publicly.

For help, contact Emma on Discord at `@kidwithakrow._.`. Never send tokens or keys.
