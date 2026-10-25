"""Discord message adapters for keeping class memory in sync.

The host supplies the existing classifier, scope checks and storage factories.
This module never imports the running Discord client, so edits can be tested with
small stand-ins and no network connection.
"""

import asyncio
from weakref import WeakValueDictionary

import discord

from deadline_store import version_time
from presentation import DEADLINE_AMBER, card, plain


class DeadlineEvents:
    def __init__(self, host):
        self.host = host
        self._locks = WeakValueDictionary()

    def _lock(self, message_id):
        lock = self._locks.get(message_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[message_id] = lock
        return lock

    def _notes(self, message):
        conn = self.host.db()
        try:
            return conn.execute("SELECT * FROM notes WHERE guild_id = ? AND channel_id = ? "
                                "AND link = ?", (message.guild.id, message.channel.id,
                                                 message.jump_url)).fetchall()
        finally:
            conn.close()

    def _delete_notes(self, guild_id, channel_id, link):
        conn = self.host.db()
        try:
            with conn:
                conn.execute("DELETE FROM notes WHERE guild_id = ? AND channel_id = ? "
                             "AND link = ?", (guild_id, channel_id, link))
        finally:
            conn.close()

    def _tracked(self, channel, guild_id):
        category = self.host._category_of(channel)
        return self.host.is_tracked(guild_id, category.id if category else None,
                                    getattr(channel, "parent_id", None) or channel.id)

    async def remember(self, message, *, edited=False):
        async with self._lock(message.id):
            return await self._remember(message, edited=edited)

    async def _remember(self, message, *, edited=False):
        h = self.host
        if not message.guild or message.author.bot or getattr(message, "webhook_id", None):
            return
        if not self._tracked(message.channel, message.guild.id):
            return
        if not h.ai_available():
            return
        store = h.deadline_store()
        old = await asyncio.to_thread(store.source, message.guild.id, message.channel.id, message.id)
        notes = await asyncio.to_thread(self._notes, message)
        stamp = version_time(getattr(message, "edited_at", None) or
                             getattr(message, "created_at", None))
        versions = [old["source_version"]] if old and old["source_version"] else []
        versions += [version_time(row["created_at"]) for row in notes if row["created_at"]]
        if versions and stamp <= max(versions):
            return
        if h._is_for_bot(message):
            if edited:
                await asyncio.to_thread(store.remove_source, message.guild.id,
                                        message.channel.id, message.id, source_version=stamp)
                await asyncio.to_thread(self._delete_notes, message.guild.id,
                                        message.channel.id, message.jump_url)
            return
        roles = h._real_roles(message.author)
        reference = getattr(message, "reference", None)
        target_id = getattr(reference, "message_id", None)
        target = await asyncio.to_thread(store.source, message.guild.id, message.channel.id,
                                         target_id) if target_id else None
        correction_reply = target and h.DATE_SIGNAL_RE.search(message.content)
        if not (edited and (old or notes)) and not correction_reply and not h.should_analyze_message(message.content, roles):
            return
        result = await asyncio.to_thread(h.analyze_message, message.content, roles, message.guild.id)
        if result.get("classification_failed"):
            # A malformed response/outage is not evidence that an assignment vanished.
            return
        is_deadline = (result.get("is_important") is True and result.get("type") == "deadline"
                       and result.get("due_date"))
        if is_deadline:
            change = await asyncio.to_thread(
                store.save, result["summary"], result["due_date"], message.author.display_name,
                message.channel.id, message.jump_url,
                h.audience_from_message(message, result.get("audience", ""), result.get("poster_grade", "")),
                message.guild.id, message.author.id, source_message_id=message.id,
                source_version=stamp, correction=result.get("is_correction") is True,
                target_message_id=getattr(reference, "message_id", None),
                admin=bool(message.author.guild_permissions.manage_guild))
            if change.action in {"needs_choice", "forbidden"}:
                await message.reply(
                    "I haven’t changed a deadline: I need one exact assignment and permission "
                    "to update it. The original contributor or a server admin can use `/reschedule` "
                    "and choose the deadline.", mention_author=False)
                return
            if change.action == "stale":
                return
            await asyncio.to_thread(self._delete_notes, message.guild.id,
                                    message.channel.id, message.jump_url)
            h.record_event("first_deadline_saved", message.guild.id, once=True)
            if change.action in {"updated", "rescheduled"} and change.old_date != change.new_date:
                await message.reply(embed=card("Deadline updated",
                    f"**{plain(result['summary'])}**\n{change.old_date} → **{change.new_date}**\n"
                    "The old date won’t appear in upcoming deadlines.", color=DEADLINE_AMBER),
                    mention_author=False)
            elif change.action == "replaced" and old and old["status"] == "active":
                await message.reply("Your saved copy changed. Other contributors’ sources weren’t "
                                    "rewritten; the original contributor or an admin can use "
                                    "`/reschedule` to correct the shared assignment.", mention_author=False)
            return change

        await asyncio.to_thread(store.remove_source, message.guild.id, message.channel.id,
                                message.id, source_version=stamp)
        await asyncio.to_thread(self._delete_notes, message.guild.id,
                                message.channel.id, message.jump_url)
        if result.get("is_important") is True:
            audience = h.audience_from_message(message, result.get("audience", ""), result.get("poster_grade", ""))
            await asyncio.to_thread(h.save_note, result["summary"], message.content,
                                    message.author.display_name, message.channel.id, message.jump_url,
                                    audience, message.guild.id, message.author.id, source_version=stamp)

    async def edited(self, payload):
        # Discord also emits edits for link previews. Those are not content changes.
        if not payload.guild_id or "content" not in payload.data:
            return
        h = self.host
        channel = h.bot.get_channel(payload.channel_id)
        if channel is None or not self._tracked(channel, payload.guild_id):
            return
        try:
            async with self._lock(payload.message_id):
                # Fetch after taking the lock so uncached/out-of-order events see
                # the latest text, not an incomplete raw payload.
                message = await channel.fetch_message(payload.message_id)
                await self._remember(message, edited=True)
        except discord.NotFound:
            await self.deleted(payload)
        except Exception as exc:
            h.record_operational_error("deadline_message_edit", exc, payload.guild_id)

    async def deleted(self, payload):
        if not payload.guild_id:
            return
        message_ids = getattr(payload, "message_ids", None) or [payload.message_id]
        for message_id in message_ids:
            async with self._lock(message_id):
                await asyncio.to_thread(self.host.deadline_store().remove_source,
                                        payload.guild_id, payload.channel_id, message_id)
                link = f"https://discord.com/channels/{payload.guild_id}/{payload.channel_id}/{message_id}"
                await asyncio.to_thread(self._delete_notes, payload.guild_id, payload.channel_id, link)
