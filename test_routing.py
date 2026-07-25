"""Quick check on the three judgement calls, run against your real material.

  export ANTHROPIC_API_KEY="..."
  python3 test_routing.py

Nothing here writes to the database — it copies it first.
"""
import os
import shutil
import tempfile

import bot

if not os.environ.get("ANTHROPIC_API_KEY"):
    raise SystemExit("Set ANTHROPIC_API_KEY first.")

# Work on a throwaway copy so a test run can't touch the bot's real memory.
tmp = os.path.join(tempfile.mkdtemp(), "test.db")
shutil.copy("classbrain.db", tmp)
bot.DB_PATH = tmp
bot.init_db()

conn = bot.db()
channel_id = conn.execute("SELECT DISTINCT channel_id FROM materials").fetchone()[0]
conn.close()
groups = bot.get_material_groups(channel_id)

print("=== material groups ===")
for g in groups:
    print(f'  {g["quiz_group"]}: {g["topics"]}')

print("\n=== wants_a_reply — should be False for acknowledgments ===")
for text in ["ok", "thanks!", "lol", "got it", "nice",
             "what else is due", "when is the lab due"]:
    print(f"  {text!r:22} -> {bot.wants_a_reply(text)}")

print("\n=== pick_material_group — two topics must not cross ===")
for request in ["quiz", "quiz us on the civil war",
                "quiz me on the war of 1812", "quiz us on photosynthesis"]:
    group = bot.pick_material_group(request, groups)
    label = next((g["topics"] for g in groups if g["quiz_group"] == group),
                 "(nothing on that)")
    print(f"  {request!r:30} -> {label}")

print("\n=== relate_upload — 'yes' only when the caption says so ===")
for caption in ["can u use these two textbook pages to make a quiz",
                "heres more war of 1812 stuff to add",
                "war of 1812",
                "random chem notes"]:
    print(f"  {caption!r:52} -> {bot.relate_upload(caption, 'The War of 1812', groups)}")

print("\n=== analyze_message — grade detection (poster has '8th Grade' role) ===")
POSTER_ROLES = ["8th Grade", "Member", "he/him"]
for msg in ["7th grade math test friday",         # explicit text grade -> audience
            "we have a math test friday",         # own class -> poster_grade 8th
            "no school monday!",                   # general -> both blank
            "reminder: essay due monday for AP Bio"]:  # named class -> audience
    r = bot.analyze_message(msg, POSTER_ROLES)
    print(f"  {msg!r:45} audience={r.get('audience','')!r:12} "
          f"poster_grade={r.get('poster_grade','')!r}")

print("\n=== answer_question — an 8th grader shouldn't see 7th-grade-only items ===")
class _Row(dict):   # stand-in for a sqlite3.Row (supports row['key'])
    pass
deadlines = [
    _Row(summary="Math test", due_date="2026-09-10", author="Ava", audience="7th Grade"),
    _Row(summary="History essay", due_date="2026-09-11", author="Ben", audience="8th Grade"),
    _Row(summary="Picture day form", due_date="2026-09-12", author="Office", audience=""),
]
answer = bot.answer_question("what's due?", deadlines, [], [], asker_roles=["8th Grade"])
print("  (asked by an 8th grader — should show History essay + Picture day, NOT Math test)")
print("  " + answer.replace("\n", "\n  "))
