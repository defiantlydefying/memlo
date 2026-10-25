"""Deadline storage, source updates, and conservative duplicate handling.

Each contribution keeps its own row and attribution. Read views collapse exact
copies into one assignment, so deleting a contributor's data cannot orphan a
shared deadline. No fuzzy matching or destructive duplicate migration is used.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone


def identity(summary, audience=""):
    def normalize(text):
        return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", str(text or "")).casefold()))
    return normalize(summary) + "|" + normalize(audience or "")


def valid_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def version_time(value=None):
    value = value or datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def migrate(conn):
    """Add source/revision metadata without deleting existing contributions."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(deadlines)")}
    for name, definition in (
        ("identity_key", "TEXT"), ("status", "TEXT NOT NULL DEFAULT 'active'"),
        ("revision", "INTEGER NOT NULL DEFAULT 0"), ("updated_at", "TEXT"),
        ("source_version", "TEXT"), ("source_message_id", "INTEGER"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE deadlines ADD COLUMN {name} {definition}")
    for row in conn.execute("SELECT id, summary, audience, link FROM deadlines "
                            "WHERE identity_key IS NULL").fetchall():
        match = re.fullmatch(r"https://(?:\w+\.)?discord(?:app)?\.com/channels/\d+/\d+/(\d+)",
                             row["link"] or "")
        key = identity(row["summary"], row["audience"])
        if not (row["summary"] or "").strip():
            key = f"legacy-untitled-{row['id']}"
        conn.execute("UPDATE deadlines SET identity_key = ?, source_message_id = ? WHERE id = ?",
                     (key,
                      int(match.group(1)) if match else None, row["id"]))
    # Earlier versions could save the same message repeatedly. Keep every row for
    # attribution/deletion, but let only its newest snapshot participate in memory.
    conn.execute("""UPDATE deadlines SET status = 'superseded'
        WHERE status = 'active' AND link IS NOT NULL AND link != '' AND EXISTS (
            SELECT 1 FROM deadlines newer WHERE newer.link = deadlines.link
            AND newer.channel_id = deadlines.channel_id
            AND newer.guild_id IS deadlines.guild_id AND newer.author_id IS deadlines.author_id
            AND newer.id > deadlines.id)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_deadlines_source "
                 "ON deadlines(guild_id, channel_id, source_message_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_deadlines_identity "
                 "ON deadlines(channel_id, identity_key, due_date, status)")
    # Repair only links whose channel/message IDs match stored attribution.
    # Do not rewrite arbitrary URLs or guess a server for legacy records.
    for row in conn.execute("SELECT id, guild_id, channel_id, source_message_id, link "
                            "FROM deadlines WHERE guild_id > 0 AND source_message_id > 0").fetchall():
        suffix = f"{row['channel_id']}/{row['source_message_id']}"
        if row["link"] == f"https://discord.com/channels/@me/{suffix}":
            conn.execute("UPDATE deadlines SET link = ? WHERE id = ?",
                         (f"https://discord.com/channels/{row['guild_id']}/{suffix}", row["id"]))


@dataclass(frozen=True)
class Change:
    action: str
    deadline_id: int | None = None
    old_date: str | None = None
    new_date: str | None = None


class DeadlineNotFound(ValueError):
    pass


class DeadlinePermissionError(PermissionError):
    pass


class DeadlineStore:
    def __init__(self, connect, now, today):
        self.connect = connect
        self.now = now
        self.today = today

    def _group(self, conn, row):
        return conn.execute(
            "SELECT * FROM deadlines WHERE guild_id IS ? AND channel_id = ? "
            "AND identity_key = ? AND due_date = ? AND status = 'active'",
            (row["guild_id"], row["channel_id"], row["identity_key"], row["due_date"])).fetchall()

    def _can_change(self, conn, row, actor_id, admin):
        group = self._group(conn, row)
        owner = min(group, key=lambda item: item["id"])["author_id"] if group else None
        return admin or (actor_id is not None and actor_id == owner)

    def _stale_for_group(self, conn, row, stamp):
        return any(item["source_version"] and stamp <= item["source_version"]
                   for item in self._group(conn, row))

    def _update_group(self, conn, row, *, due_date=None, status="active", version=None,
                      summary=None, audience=None):
        local_now = self.now(row["guild_id"]).isoformat()
        summary = row["summary"] if summary is None else summary
        audience = row["audience"] if audience is None else audience
        conn.execute(
            "UPDATE deadlines SET due_date = ?, status = ?, reminded = 0, "
            "created_at = ?, revision = revision + 1, source_version = ?, "
            "summary = ?, audience = ?, identity_key = ? "
            "WHERE guild_id IS ? AND channel_id = ? AND identity_key = ? "
            "AND due_date = ? AND status = 'active'",
            (due_date or row["due_date"], status, local_now, version or version_time(),
             summary, audience, identity(summary, audience),
             row["guild_id"], row["channel_id"], row["identity_key"], row["due_date"]))

    def save(self, summary, due_date, author, channel_id, link, audience="",
             guild_id=None, author_id=None, *, source_message_id=None,
             source_version=None, correction=False, target_message_id=None, admin=False):
        if not isinstance(summary, str) or not summary.strip() or not valid_date(due_date):
            raise ValueError("A deadline needs a title and a real YYYY-MM-DD date")
        key = identity(summary, audience)
        stamp = version_time(source_version)
        conn = self.connect()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                current = None
                if link:
                    current = conn.execute(
                        "SELECT * FROM deadlines WHERE guild_id IS ? AND channel_id = ? "
                        "AND link = ? AND (author_id IS ? OR author_id IS NULL) ORDER BY id DESC LIMIT 1",
                        (guild_id, channel_id, link, author_id)).fetchone()
                if current and current["source_version"] and stamp <= current["source_version"]:
                    return Change("stale", current["id"])
                old_date = current["due_date"] if current else None
                if current:
                    changed = (current["identity_key"] != key or old_date != due_date
                               or current["status"] != "active")
                    group = self._group(conn, current)
                    shared_update = (changed and current["status"] == "active"
                                     and self._can_change(conn, current, author_id, admin))
                    if shared_update:
                        if self._stale_for_group(conn, current, stamp):
                            return Change("stale", current["id"])
                        self._update_group(conn, current, due_date=due_date, version=stamp,
                                           summary=summary, audience=audience)
                    # A repost edited to an unrelated title becomes its own task;
                    # only the original contributor can rename the shared assignment.
                    conn.execute(
                        "UPDATE deadlines SET summary = ?, due_date = ?, author = ?, "
                        "author_id = ?, audience = ?, identity_key = ?, status = 'active', "
                        "reminded = CASE WHEN ? THEN 0 ELSE reminded END, "
                        "created_at = CASE WHEN ? THEN ? ELSE created_at END, "
                        "revision = revision + CASE WHEN ? THEN 1 ELSE 0 END, updated_at = ?, source_version = ?, "
                        "source_message_id = COALESCE(?, source_message_id) WHERE id = ?",
                        (summary, due_date, author, author_id, audience, key, changed, changed,
                         self.now(guild_id).isoformat(), changed, stamp, stamp, source_message_id, current["id"]))
                    conn.execute("UPDATE deadlines SET status = 'superseded' "
                                 "WHERE guild_id IS ? AND channel_id = ? AND link = ? AND id != ?",
                                 (guild_id, channel_id, link, current["id"]))
                    action = "updated" if changed else "unchanged"
                    if changed and not shared_update:
                        action = "replaced"
                    return Change(action, current["id"], old_date, due_date)

                if correction:
                    if target_message_id:
                        candidates = conn.execute(
                            "SELECT * FROM deadlines WHERE guild_id IS ? AND channel_id = ? "
                            "AND source_message_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
                            (guild_id, channel_id, target_message_id)).fetchall()
                    else:
                        candidates = conn.execute(
                            "SELECT * FROM deadlines WHERE guild_id IS ? AND channel_id = ? "
                            "AND identity_key = ? AND status = 'active' GROUP BY due_date",
                            (guild_id, channel_id, key)).fetchall()
                    if len(candidates) != 1:
                        return Change("needs_choice")
                    target = candidates[0]
                    if not self._can_change(conn, target, author_id, admin):
                        return Change("forbidden", target["id"])
                    if self._stale_for_group(conn, target, stamp):
                        return Change("stale", target["id"])
                    old_date = target["due_date"]
                    if old_date != due_date:
                        self._update_group(conn, target, due_date=due_date, version=stamp)
                    # Explicit replies can disambiguate a shortened title. Keep
                    # the assignment's established title instead of splitting it.
                    summary, audience, key = target["summary"], target["audience"], target["identity_key"]

                duplicate = conn.execute(
                    "SELECT * FROM deadlines WHERE guild_id IS ? AND channel_id = ? "
                    "AND identity_key = ? AND due_date = ? AND status = 'active' "
                    "ORDER BY reminded DESC, id DESC LIMIT 1",
                    (guild_id, channel_id, key, due_date)).fetchone()
                cur = conn.execute(
                    "INSERT INTO deadlines (summary, due_date, author, channel_id, link, "
                    "created_at, audience, guild_id, author_id, identity_key, updated_at, "
                    "source_version, source_message_id, reminded) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (summary, due_date, author, channel_id, link,
                     duplicate["created_at"] if duplicate else self.now(guild_id).isoformat(),
                     audience, guild_id, author_id, key, stamp, stamp, source_message_id,
                     duplicate["reminded"] if duplicate else 0))
                action = "rescheduled" if correction else "duplicate" if duplicate else "created"
                return Change(action, cur.lastrowid, old_date, due_date)
        finally:
            conn.close()

    def _read(self, where, parameters, *, unreminded=False, quiet_until=None):
        conn = self.connect()
        try:
            # Exact duplicates collapse only within the same class and audience.
            query = """WITH grouped AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY guild_id, channel_id, identity_key, due_date
                    ORDER BY COALESCE(updated_at, '') DESC, id DESC) AS position,
                MAX(reminded) OVER (
                    PARTITION BY guild_id, channel_id, identity_key, due_date) AS group_reminded,
                MIN(COALESCE(created_at, '')) OVER (
                    PARTITION BY guild_id, channel_id, identity_key, due_date) AS first_created
                FROM deadlines WHERE status = 'active' AND """ + where + ") "
            query += "SELECT * FROM grouped WHERE position = 1"
            if unreminded:
                query += " AND group_reminded = 0 AND first_created <= ?"
                parameters = (*parameters, quiet_until)
            return conn.execute(query + " ORDER BY due_date, id", parameters).fetchall()
        finally:
            conn.close()

    def upcoming(self, channel_id, guild_id=None):
        return self._read("channel_id = ? AND due_date >= ?",
                          (channel_id, self.today(guild_id).isoformat()))

    def active(self, guild_id, channel_id):
        return self._read("guild_id = ? AND channel_id = ?", (guild_id, channel_id))

    def inspect(self, deadline_id, guild_id, channel_id, actor_id, *, admin=False):
        conn = self.connect()
        try:
            row = conn.execute("SELECT * FROM deadlines WHERE id = ? AND guild_id = ? "
                               "AND channel_id = ? AND status = 'active'",
                               (deadline_id, guild_id, channel_id)).fetchone()
            if not row:
                raise DeadlineNotFound("That deadline is no longer active in this channel")
            if not self._can_change(conn, row, actor_id, admin):
                raise DeadlinePermissionError("Only the original contributor or someone with Manage Server can change this deadline")
            return row
        finally:
            conn.close()

    def due_soon(self, guild_id, within_days=2, quiet_hours=1):
        today = self.today(guild_id)
        return self._read("guild_id = ? AND due_date >= ? AND due_date <= ?",
                          (guild_id, today.isoformat(), (today + timedelta(days=within_days)).isoformat()),
                          unreminded=True,
                          quiet_until=(self.now(guild_id) - timedelta(hours=quiet_hours)).isoformat())

    def mark_reminded(self, deadline_id, revision=None):
        conn = self.connect()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM deadlines WHERE id = ? AND status = 'active'",
                                   (deadline_id,)).fetchone()
                if not row or (revision is not None and row["revision"] != revision):
                    return False
                conn.execute("UPDATE deadlines SET reminded = 1 WHERE guild_id IS ? "
                             "AND channel_id = ? AND identity_key = ? AND due_date = ? "
                             "AND status = 'active'",
                             (row["guild_id"], row["channel_id"], row["identity_key"], row["due_date"]))
                return True
        finally:
            conn.close()

    def change(self, deadline_id, guild_id, channel_id, actor_id, *, new_date=None,
               admin=False, expected_revision=None):
        """Only the original contributor or a server admin may correct a shared task."""
        if new_date is not None and not valid_date(new_date):
            raise ValueError("Use a real date in YYYY-MM-DD form")
        conn = self.connect()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM deadlines WHERE id = ? AND guild_id = ? "
                                   "AND channel_id = ? AND status = 'active'",
                                   (deadline_id, guild_id, channel_id)).fetchone()
                if not row:
                    raise DeadlineNotFound("That deadline is no longer active in this channel")
                if not self._can_change(conn, row, actor_id, admin):
                    raise DeadlinePermissionError("Only the original contributor or someone with Manage Server can change this deadline")
                if expected_revision is not None and row["revision"] != expected_revision:
                    raise ValueError("That deadline changed since you opened this confirmation. Run the command again")
                if new_date == row["due_date"]:
                    return Change("unchanged", deadline_id, row["due_date"], new_date)
                stamp = version_time()
                self._update_group(conn, row, due_date=new_date,
                                   status="active" if new_date else "cancelled", version=stamp)
                conn.execute("UPDATE deadlines SET updated_at = ? WHERE id = ?", (stamp, deadline_id))
                return Change("rescheduled" if new_date else "cancelled", deadline_id, row["due_date"], new_date)
        finally:
            conn.close()

    def source(self, guild_id, channel_id, message_id):
        conn = self.connect()
        try:
            return conn.execute("SELECT * FROM deadlines WHERE guild_id = ? AND channel_id = ? "
                                "AND source_message_id = ? ORDER BY id DESC LIMIT 1",
                                (guild_id, channel_id, message_id)).fetchone()
        finally:
            conn.close()

    def link_source(self, deadline_id, guild_id, channel_id, pending_link, message_id, jump_url):
        """Replace a form's internal source token with its real Discord receipt."""
        conn = self.connect()
        try:
            with conn:
                return conn.execute(
                    "UPDATE deadlines SET link = ?, source_message_id = ? "
                    "WHERE id = ? AND guild_id = ? AND channel_id = ? AND link = ?",
                    (jump_url, message_id, deadline_id, guild_id, channel_id, pending_link)).rowcount == 1
        finally:
            conn.close()

    def remove_source(self, guild_id, channel_id, message_id, *, source_version=None):
        conn = self.connect()
        try:
            with conn:
                stamp = version_time(source_version)
                cur = conn.execute(
                    "UPDATE deadlines SET status = 'removed', revision = revision + 1, "
                    "source_version = ?, updated_at = ? WHERE guild_id = ? AND channel_id = ? "
                    "AND source_message_id = ? AND status = 'active' "
                    "AND (source_version IS NULL OR source_version < ?)",
                    (stamp, stamp, guild_id, channel_id, message_id, stamp))
                return cur.rowcount
        finally:
            conn.close()
