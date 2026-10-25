"""Quick check on the three judgement calls, run against your real material.

  export ANTHROPIC_API_KEY="..."
  python3 test_routing.py

Nothing here writes to the database — it copies it first.
"""
import os
import tempfile

def run_checks(bot, groups):
    print("=== material groups ===")
    for group in groups:
        print(f'  {group["quiz_group"]}: {group["topics"]}')

    print("\n=== acknowledgments — should be False ===")
    for text in ["ok", "thanks!", "lol", "got it", "nice",
                 "what else is due", "when is the lab due"]:
        print(f"  {text!r:22} -> {bot.wants_a_reply(text)}")

    print("\n=== topics must stay separate ===")
    for request in ["quiz", "quiz us on the civil war",
                    "quiz me on the war of 1812", "quiz us on photosynthesis"]:
        selected = bot.pick_material_group(request, groups)
        label = next((g["topics"] for g in groups if g["quiz_group"] == selected),
                     "(nothing on that)")
        print(f"  {request!r:30} -> {label}")

    print("\n=== combine only when requested ===")
    for caption in ["can u use these two textbook pages to make a quiz",
                    "heres more war of 1812 stuff to add", "war of 1812", "random chem notes"]:
        print(f"  {caption!r:52} -> {bot.relate_upload(caption, 'The War of 1812', groups)}")

    print("\n=== grade detection (poster has '8th Grade' role) ===")
    for message in ["7th grade math test friday", "we have a math test friday",
                    "no school monday!", "reminder: essay due monday for AP Bio"]:
        result = bot.analyze_message(message, ["8th Grade", "Member", "he/him"])
        print(f"  {message!r:45} audience={result.get('audience', '')!r:12} "
              f"poster_grade={result.get('poster_grade', '')!r}")

    print("\n=== only matching grades should appear ===")
    today = bot.class_today().isoformat()
    deadlines = [
        dict(summary="Math test", due_date=today, author="Ava", audience="7th Grade"),
        dict(summary="History essay", due_date=today, author="Ben", audience="8th Grade"),
        dict(summary="Picture day form", due_date=today, author="Office", audience=""),
    ]
    print(bot.answer_question("what's due?", deadlines, [], [], asker_roles=["8th Grade"]))


def main():
    # Importing this file during unittest discovery must never call an API.
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY first.")
    import bot

    original_path = bot.DB_PATH
    if not os.path.isfile(original_path):
        raise SystemExit("No local memlo.db found. Run this from the project folder.")
    with tempfile.TemporaryDirectory() as directory:
        try:
            bot.DB_PATH = os.path.join(directory, "test.db")
            # SQLite's backup API also includes committed changes in a WAL file.
            import sqlite3
            source = sqlite3.connect(f"file:{original_path}?mode=ro", uri=True)
            destination = sqlite3.connect(bot.DB_PATH)
            try:
                source.backup(destination)
            finally:
                source.close()
                destination.close()
            bot.init_db()
            conn = bot.db()
            row = conn.execute("SELECT channel_id FROM materials LIMIT 1").fetchone()
            conn.close()
            if row is None:
                raise SystemExit("No study material saved yet. Share a file before running this check.")
            run_checks(bot, bot.get_material_groups(row["channel_id"]))
        finally:
            bot.DB_PATH = original_path


if __name__ == "__main__":
    main()
