# Memlo

Memlo is the memory of a class Discord: it notices deadlines and announcements,
answers questions from what the class shared, reminds the group before work is due,
and creates quizzes from images, PDFs, text files, Google documents, and webpages.

This repository is the YC Fall 2026 application project.

## Trust model

- A new Discord installation reads **nothing** until an administrator runs `/setup`.
- The admin selects a class category or individual text channel and a server timezone.
- Memlo ignores all unselected channels.
- Stored contributions include the Discord user ID so `/delete-my-data` is reliable
  even after a display-name change.
- Product analytics contain event names and limited operational properties only—never
  message text, filenames, study material, questions, or answers.

See [PRIVACY.md](PRIVACY.md) and [TERMS.md](TERMS.md).

## Local setup

1. Install Python 3.11 or newer and the dependencies:

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

2. Create a Discord application and bot in the
   [Discord Developer Portal](https://discord.com/developers/applications). Enable
   **Message Content Intent**. Configure a Discord-provided install link with the
   `bot` and `application.commands` scopes and only the permissions Memlo needs:
   View Channels, Send Messages, Read Message History, Embed Links, and Attach Files.

3. Set the required environment variables:

   ```bash
   export DISCORD_TOKEN="..."
   export ANTHROPIC_API_KEY="..."
   ```

4. Optional production settings:

   ```bash
   export CLASS_TIMEZONE="America/New_York" # default for setup suggestions
   export DISCORD_INSTALL_URL="https://discord.com/oauth2/authorize?client_id=..."
   export PRIVACY_URL="https://getmemlo.com/privacy"
   export TERMS_URL="https://getmemlo.com/terms"
   export SUPPORT_SERVER_URL="https://discord.gg/7UT2aPf5Us"
   export BACKUP_DIR="/home/memlo/backups"
   export BACKUP_RETENTION="14"
   export HEALTH_HOST="127.0.0.1"
   export HEALTH_PORT="8080"               # set 0 to disable
   export LOG_PATH="/home/memlo/memlo.log"
   ```

5. Run the worker:

   ```bash
   .venv/bin/python bot.py
   ```

6. In Discord, a server administrator runs `/setup`, chooses exactly one category or
   channel, and enters the server's IANA timezone. Add more scopes with
   `/track-category` or `/track-channel`.

## Commands

| Command | Who | Purpose |
|---|---|---|
| `/setup` | Manage Server | Choose the first class scope and timezone |
| `/track-category` | Manage Server | Add a category |
| `/track-channel` | Manage Server | Add one text channel |
| `/untrack-category` | Manage Server | Remove a category |
| `/untrack-channel` | Manage Server | Remove a channel |
| `/setup-status` | Anyone | Show timezone and tracked scopes privately |
| `/delete-my-data` | Anyone | Delete records attributed to the requester |
| `/delete-server-data` | Manage Server | Delete all data and reset setup |
| `/privacy` | Anyone | Show the privacy summary and policy links |
| `/support` | Anyone | Join support or get the private contact email |
| `/invite` | Anyone | Get the install link and record a referral event |

Existing mention-based questions and quiz requests still work inside selected channels.

## Operations

- `GET http://127.0.0.1:8080/health` returns Discord readiness, database status,
  connected-server count, last reminder tick, last backup, and last content-free error.
- A transactionally consistent SQLite backup is created every six hours. The newest
  14 snapshots are retained by default.
- Application logs rotate at 5 MB with three historical files.
- Expensive operations use per-user, per-server sliding-window rate limits.
- Reminder scheduling is evaluated separately in each configured server timezone.

## Tests

Offline tests require no Discord connection and no real Anthropic key:

```bash
ANTHROPIC_API_KEY=test python3 -m unittest -v test_stage0.py
ANTHROPIC_API_KEY=test python3 test_links.py
python3 -m py_compile bot.py
```

`test_routing.py` exercises model judgment against real stored material and therefore
requires a real Anthropic key and a local `memlo.db`.

## Repository layout

- `bot.py` — Discord worker, storage, slash commands, and operational tasks
- `memlo.db` — local SQLite state, created at runtime and ignored by git
- `backups/` — rotating SQLite snapshots, ignored by git
- `PRIVACY.md` — source privacy policy
- `TERMS.md` — source terms
- `DEPLOY.md` — production VPS and systemd runbook
- `test_stage0.py` — offline launch-readiness tests
- `test_links.py` — offline link helper tests

## Current gate

The next proof point is five real class servers that remain active after three weeks.
Do not optimize for raw installs before retained usage exists.
