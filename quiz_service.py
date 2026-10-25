"""Shared quiz generation/publication for slash commands and mentions."""

import asyncio
import re
from dataclasses import dataclass

from presentation import STUDY_TEAL, card, clip


MAX_CONTEXT_CHARS = 60000


class QuizError(ValueError):
    """A safe, actionable message that can be shown to a student."""


def valid_quiz(value):
    return (isinstance(value, dict)
            and all(isinstance(value.get(key), str) and value[key].strip()
                    for key in ("quiz", "answer_key"))
            and len(value["quiz"]) <= 3800
            and re.findall(r"^\s*(\d+)[.)]\s+", value["quiz"], re.MULTILINE) == ["1", "2", "3"])


@dataclass(frozen=True)
class PreparedQuiz:
    questions: str
    answer_key: str
    label: str
    sources: tuple


class QuizService:
    def __init__(self, host, concurrency=3):
        self.host = host
        self.slots = asyncio.Semaphore(concurrency)

    async def build(self, request, channel_id, guild_id, group=None, *, allow_notes=True):
        h = self.host
        if not h.ai_available():
            raise QuizError("AI quizzes aren’t enabled on this copy. You can still add, "
                            "browse, and update deadlines without an API key.")
        materials = await asyncio.to_thread(h.get_materials_in_group, group, channel_id) if group else []
        if group and not materials:
            raise QuizError("That study set changed or was deleted. Choose a current set with `/materials` or `/quiz`.")
        notes = await asyncio.to_thread(h.get_notes, channel_id) if not materials and allow_notes else []
        if not materials and not notes:
            raise QuizError("There’s nothing to quiz from in this channel yet. Share study pages or notes first; `/materials` shows what’s saved.")
        if sum(len(row["digest"] or "") for row in materials) + sum(len(row["summary"] or "") for row in notes) > MAX_CONTEXT_CHARS:
            raise QuizError("This study set is too large for one useful quiz. Share a smaller chapter or unit as a separate set.")
        try:
            await asyncio.wait_for(self.slots.acquire(), timeout=2)
        except asyncio.TimeoutError:
            raise QuizError("I’m making a few quizzes right now. Try again in a moment.") from None
        try:
            result = await asyncio.to_thread(h.make_quiz, request, notes, materials)
        except Exception as exc:
            h.record_operational_error("quiz_generation", exc, guild_id)
            raise QuizError("Quiz generation is unavailable right now. Your study set is saved—try again shortly.") from None
        finally:
            self.slots.release()
        if not valid_quiz(result):
            raise QuizError("I couldn’t build a complete three-question quiz and answer key. Your study set is still saved—try again.")
        topics = list(dict.fromkeys(row["topic"] or row["filename"] for row in materials))
        sources = tuple(("materials", row["id"]) for row in materials)
        sources += tuple(("notes", row["id"]) for row in notes)
        return PreparedQuiz(result["quiz"], result["answer_key"],
                            "; ".join(topics) if topics else "Saved class notes", sources)

    def _sources_available(self, prepared, channel_id):
        conn = self.host.db()
        try:
            for table in ("materials", "notes"):
                ids = [source_id for source_table, source_id in prepared.sources if source_table == table]
                if ids:
                    placeholders = ",".join("?" for _ in ids)
                    count = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE channel_id = ? "
                                         f"AND id IN ({placeholders})", (channel_id, *ids)).fetchone()[0]
                    if count != len(ids):
                        return False
            return True
        finally:
            conn.close()

    def _delete_key(self, message_id):
        conn = self.host.db()
        try:
            with conn:
                conn.execute("DELETE FROM quizzes WHERE message_id = ?", (message_id,))
        finally:
            conn.close()

    async def publish(self, prepared, send, channel_id, guild_id, author_id, *, private=False):
        h = self.host
        if not await asyncio.to_thread(self._sources_available, prepared, channel_id):
            raise QuizError("The source material was deleted while I was making this quiz. Nothing was posted; choose another study set.")
        action = "Only you can see this quiz · Answer privately" if private else "Answer privately, or reply with 1. / 2. / 3."
        embed = card("Practice · 3 questions", prepared.questions, color=STUDY_TEAL,
                     footer=f"Memlo · {clip(prepared.label, 180)} · {action}")
        # Enable the button only AFTER the key is committed. A fast click must
        # never reach an ungradeable quiz, and a failed save must leave no quiz.
        sent = await send(embed=embed)
        try:
            await asyncio.to_thread(h.save_quiz, sent.id, channel_id,
                f"QUESTIONS (posted to the chat):\n{prepared.questions}\n\n"
                f"ANSWER KEY (private, students never saw this):\n{prepared.answer_key}", guild_id, author_id)
            # Re-check after posting too: deletion may race the network request.
            if not await asyncio.to_thread(self._sources_available, prepared, channel_id):
                raise QuizError("The source material was deleted while I was posting. Choose another study set.")
            await sent.edit(view=h.QuizView())
        except Exception as exc:
            for cleanup in (lambda: asyncio.to_thread(self._delete_key, sent.id), sent.delete):
                try:
                    await cleanup()
                except Exception as cleanup_error:
                    h.record_operational_error("quiz_publish_cleanup", cleanup_error, guild_id)
            h.record_operational_error("quiz_publish", exc, guild_id)
            if isinstance(exc, QuizError):
                raise
            raise QuizError("I couldn’t finish saving this quiz. If its card is still visible, "
                            "it isn’t ready to answer. Try again shortly.") from None
        return sent
