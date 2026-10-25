# Memlo

Less scrolling. More studying.

Memlo is a Discord bot that helps your class keep track of what matters. It
organizes deadlines and sends reminders without needing AI. Optional AI tools
remember class announcements, answer questions, and turn study material into quizzes.

## What it does

- **Tracks assignments without AI:** use a form to add deadlines, then browse,
  reschedule or cancel them. Reminders work without a paid API.
- **Remembers the important stuff:** optional AI notices dates and class announcements.
- **Answers from your class’s memory:** ask what’s due or what your teacher said.
- **Helps you practice:** create three-question quizzes from screenshots, PDFs,
  text files, and study links, with an option for private feedback.
- **Keeps study sets organized:** files shared together stay together, and different
  topics can have their own quizzes.
- **Keeps deadlines up to date:** edit the original message, reschedule an
  assignment, or cancel a deadline that no longer applies.

## Add Memlo to Discord

[Add Memlo to your server](https://discord.com/oauth2/authorize?client_id=1527179920386691072&permissions=84992&scope=bot%20applications.commands)

1. Have a server admin open the invite link and add Memlo to your class server.
2. Run `/setup` in Discord to choose a class channel or category and your timezone.
3. Try `/add-deadline` and `/due`, or `/help` for the full guide.

Memlo only listens in the channels an admin selects. It reads nothing until setup
is complete. You don’t need to install Python, provide API keys, or host anything
yourself.

Memlo needs **View Channels**, **Send Messages**, **Read Message History**, and
**Embed Links**—not Administrator. For tracked threads, also allow **Send Messages
in Threads**. The human running `/setup` needs Manage Server permission.

## Try it out

```text
@Memlo what’s due tomorrow?
@Memlo when is our chemistry quiz?
@Memlo quiz me on chemical reactions
```

To practice from a particular upload, reply to that upload with
`@Memlo quiz me`. Use **Answer privately** on the quiz for feedback only you can
see; replying with answers in the channel makes the feedback public.

Or use `/quiz` to choose a saved study set and a difficulty: **Warm-up**, **Mixed**,
or **Challenge**. These quizzes are private by default; turn off the private option
to share one with the channel.

| Command | What it does |
| --- | --- |
| `/help` | Shows examples and a quick guide |
| `/add-deadline` | Opens an assignment form—no AI required |
| `/due` | Shows saved upcoming deadlines and their sources |
| `/materials` | Shows the study sets saved in this channel |
| `/quiz` | Creates a quiz from a study set you choose |
| `/reschedule` | Corrects a saved due date |
| `/cancel-deadline` | Cancels an assignment’s deadline and reminders |
| `/setup-status` | Shows which channels Memlo is listening in |
| `/delete-my-data` | Deletes your saved contributions |
| `/privacy`, `/support`, `/invite` | Shows policy, contact, or installation links |
| `/setup` | Admin: chooses the first class channel/category and timezone |
| `/track-channel`, `/track-category` | Admin: adds channels or categories |
| `/untrack-channel`, `/untrack-category` | Admin: stops listening in those scopes |
| `/delete-server-data` | Admin: confirms deletion of all Memlo data in this server |

Rescheduling and cancellation require the original contributor or a server admin.
Memlo can miss things, and AI answers can be wrong—check the original source or
your teacher when something matters.

## Run your own copy

See [SETUP.md](SETUP.md) for the complete rebuild instructions and a fresh-server
walkthrough. The quick start is `python3 setup_memlo.py`, add your own Discord bot
token to `.env`, then run `.venv/bin/python bot.py` from the project folder.
An AI key is optional; the entire deadline workflow works without one.

## Privacy

You control your saved contributions with `/delete-my-data` or
`@Memlo forget me`. See the [privacy policy](PRIVACY.md) and
[terms of service](TERMS.md) for details.

## Contact

Made by **Emma Hsieh**.

Discord: `@kidwithakrow._.`

Questions, bugs, or ideas? [Reach me through the Memlo Discord](https://discord.gg/7UT2aPf5Us),
or use `/support` in Discord for contact options.
