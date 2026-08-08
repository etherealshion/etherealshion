"""
database.py
------------
This is the ONLY file that talks directly to the SQLite database.
Every other file (bot.py, cogs/write.py) calls the functions defined
here instead of writing raw SQL themselves. That keeps the SQL in one
place, so if something breaks or the schema needs to change, there's
exactly one file to look at.

We use aiosqlite instead of the built-in sqlite3 module because our
bot runs on an "asyncio" event loop. If we used a regular blocking
database library, the ENTIRE bot would freeze every time it touched
the database - no other user could get a response while we were
saving one idea. aiosqlite lets other things keep happening while a
database call is "in flight."
"""

import os

import aiosqlite
from datetime import datetime, timezone

# DB_PATH is normally just "marketplace.db" (a file right next to this
# script) - fine for local development. In production on a platform
# like Railway, the regular filesystem gets wiped on every redeploy, so
# the database needs to live on a persistent Volume instead - set
# DB_PATH=/data/marketplace.db (or wherever your Volume is mounted) as
# an environment variable there, and this picks it up automatically.
DB_PATH = os.getenv("DB_PATH", "marketplace.db")


async def init_db():
    """
    Creates the three tables (users, ideas, transactions) if they
    don't already exist. Safe to call every time the bot starts -
    "IF NOT EXISTS" means it does nothing if they're already there.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                display_name TEXT,
                ideas_written INTEGER DEFAULT 0,
                ideas_published INTEGER DEFAULT 0,
                ideas_sold INTEGER DEFAULT 0,
                ideas_purchased INTEGER DEFAULT 0,
                joined_at TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS ideas (
                idea_id INTEGER PRIMARY KEY AUTOINCREMENT,
                writer_id INTEGER,
                title TEXT,
                category TEXT,
                full_text TEXT,
                preview_text TEXT,
                ai_score INTEGER,
                ai_feedback TEXT,
                status TEXT DEFAULT 'draft',
                price INTEGER DEFAULT 10,
                buyer_id INTEGER,
                created_at TEXT,
                published_at TEXT,
                sold_at TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                tx_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                type TEXT,
                amount INTEGER,
                idea_id INTEGER,
                timestamp TEXT,
                payment_status TEXT
            )
        """)

        await db.commit()


def _now() -> str:
    """Small helper so every timestamp we store is formatted the same way."""
    return datetime.now(timezone.utc).isoformat()


async def ensure_user(user_id: int, display_name: str):
    """
    Makes sure a row exists for this Discord user before we do anything
    else with them. 'INSERT OR IGNORE' means: insert it, but if a row
    with this user_id already exists, do nothing instead of erroring.
    We call this at the start of every command a user runs.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, display_name, joined_at) VALUES (?, ?, ?)",
            (user_id, display_name, _now()),
        )
        # Keep display_name up to date in case they changed their nickname.
        await db.execute(
            "UPDATE users SET display_name = ? WHERE user_id = ?",
            (display_name, user_id),
        )
        await db.commit()


async def increment_user_stat(user_id: int, field: str):
    """
    Bumps one of the counter columns (ideas_written, ideas_published,
    etc.) on the users table by 1.

    'field' is checked against a whitelist before we use it, because
    it's about to go directly into a SQL string. We can safely use
    '?' placeholders for VALUES, but not for column names - so we
    guard this manually instead.
    """
    allowed_fields = {"ideas_written", "ideas_published", "ideas_sold", "ideas_purchased"}
    if field not in allowed_fields:
        raise ValueError(f"'{field}' is not an allowed stat field")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE users SET {field} = {field} + 1 WHERE user_id = ?",
            (user_id,),
        )
        await db.commit()


async def create_idea(
    writer_id: int,
    title: str,
    category: str,
    full_text: str,
    preview_text: str,
    ai_score: int | None = None,
    ai_feedback: str | None = None,
) -> int:
    """
    Inserts a brand-new idea with status='draft' and returns its new
    idea_id, so the calling code can reference it later (e.g. to put
    it in a button's custom_id, or look it up again after Publish).

    ai_score/ai_feedback are optional and unused for now (AI scoring
    was removed) - the columns stay in the schema in case you want to
    turn scoring back on later, they just get stored as NULL for now.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO ideas
               (writer_id, title, category, full_text, preview_text,
                ai_score, ai_feedback, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?)""",
            (writer_id, title, category, full_text, preview_text, ai_score, ai_feedback, _now()),
        )
        await db.commit()
        return cursor.lastrowid


async def get_idea(idea_id: int):
    """
    Returns one idea as a dict-like row (so you can do row['title'])
    or None if no idea with that id exists.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM ideas WHERE idea_id = ?", (idea_id,)) as cursor:
            return await cursor.fetchone()


async def set_idea_status(idea_id: int, status: str, timestamp_field: str | None = None):
    """
    Updates an idea's status (e.g. to 'published'). If timestamp_field
    is given (must be 'published_at' or 'sold_at'), also stamps the
    current time into that column - so we know exactly when it happened.
    """
    allowed_timestamp_fields = {"published_at", "sold_at"}
    async with aiosqlite.connect(DB_PATH) as db:
        if timestamp_field:
            if timestamp_field not in allowed_timestamp_fields:
                raise ValueError(f"'{timestamp_field}' is not an allowed timestamp field")
            await db.execute(
                f"UPDATE ideas SET status = ?, {timestamp_field} = ? WHERE idea_id = ?",
                (status, _now(), idea_id),
            )
        else:
            await db.execute(
                "UPDATE ideas SET status = ? WHERE idea_id = ?",
                (status, idea_id),
            )
        await db.commit()


async def delete_idea(idea_id: int):
    """Used by Discard and Rewrite - permanently removes a draft idea."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM ideas WHERE idea_id = ?", (idea_id,))
        await db.commit()


async def log_transaction(
    user_id: int,
    tx_type: str,
    amount: int,
    idea_id: int | None = None,
    payment_status: str = "confirmed",
):
    """
    Records a payment event. Through Phases 1-3, payment_status will
    just be 'confirmed' (our manual placeholder). In Phase 4, a real
    Stripe webhook will set this properly once money actually moves.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO transactions (user_id, type, amount, idea_id, timestamp, payment_status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, tx_type, amount, idea_id, _now(), payment_status),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Phase 2 additions: browsing, buying, and per-user dashboards
# ---------------------------------------------------------------------------

async def get_published_ideas(limit: int = 25):
    """
    Returns published (not-yet-sold) ideas, newest first, for the
    /marketplace browse command. Only columns needed for a preview
    card are relevant here - buyers don't see full_text until they buy.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM ideas WHERE status = 'published' ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ) as cursor:
            return await cursor.fetchall()


async def get_random_published_idea():
    """
    Picks one published idea entirely at random, for /random. Returns
    None if nothing is currently published (empty marketplace).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM ideas WHERE status = 'published' ORDER BY RANDOM() LIMIT 1"
        ) as cursor:
            return await cursor.fetchone()


async def try_buy_idea(idea_id: int, buyer_id: int) -> bool:
    """
    Attempts to buy an idea ATOMICALLY. Returns True if the purchase
    succeeded, False if it didn't (already sold, or doesn't exist).

    Why atomic matters: imagine two editors click Buy on the same idea
    within the same second. If we did "check status, then update" as
    two separate steps, both could pass the check before either update
    lands - selling the same idea twice. Instead we do the check AND
    the update in a single SQL statement (`WHERE status = 'published'`),
    so the database itself guarantees only the first one can succeed.
    We know which one that was by checking `cursor.rowcount` - it's 1
    if a row actually matched and got updated, 0 if not.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """UPDATE ideas
               SET status = 'sold', buyer_id = ?, sold_at = ?
               WHERE idea_id = ? AND status = 'published'""",
            (buyer_id, _now(), idea_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_ideas_by_writer(writer_id: int):
    """All ideas (any status) belonging to one writer, for /myideas."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM ideas WHERE writer_id = ? ORDER BY created_at DESC",
            (writer_id,),
        ) as cursor:
            return await cursor.fetchall()


async def get_purchases_by_buyer(buyer_id: int):
    """Everything one editor has bought (full text included), for /mypurchases."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM ideas WHERE buyer_id = ? ORDER BY sold_at DESC",
            (buyer_id,),
        ) as cursor:
            return await cursor.fetchall()


# ---------------------------------------------------------------------------
# Phase 3 additions: leaderboard and admin stats
# ---------------------------------------------------------------------------

async def get_top_writers(limit: int = 5):
    """Ranks writers by how many ideas they've published."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT user_id, display_name, ideas_published, ideas_sold
               FROM users
               WHERE ideas_published > 0
               ORDER BY ideas_published DESC
               LIMIT ?""",
            (limit,),
        ) as cursor:
            return await cursor.fetchall()


async def get_top_editors(limit: int = 5):
    """Ranks editors by how many ideas they've purchased."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT user_id, display_name, ideas_purchased
               FROM users
               WHERE ideas_purchased > 0
               ORDER BY ideas_purchased DESC
               LIMIT ?""",
            (limit,),
        ) as cursor:
            return await cursor.fetchall()


async def get_admin_stats() -> dict:
    """
    One-stop shop for /admin-stats: total revenue, how many distinct
    people have written vs. bought at least once, and the funnel from
    ideas written -> published -> sold.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        async with db.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM transactions WHERE payment_status = 'confirmed'"
        ) as cursor:
            total_revenue = (await cursor.fetchone())["total"]

        async with db.execute("SELECT COUNT(*) AS count FROM users WHERE ideas_written > 0") as cursor:
            active_writers = (await cursor.fetchone())["count"]

        async with db.execute("SELECT COUNT(*) AS count FROM users WHERE ideas_purchased > 0") as cursor:
            active_editors = (await cursor.fetchone())["count"]

        async with db.execute("SELECT COUNT(*) AS count FROM ideas") as cursor:
            total_written = (await cursor.fetchone())["count"]

        async with db.execute(
            "SELECT COUNT(*) AS count FROM ideas WHERE status IN ('published', 'sold')"
        ) as cursor:
            total_published = (await cursor.fetchone())["count"]

        async with db.execute("SELECT COUNT(*) AS count FROM ideas WHERE status = 'sold'") as cursor:
            total_sold = (await cursor.fetchone())["count"]

    return {
        "total_revenue": total_revenue,
        "active_writers": active_writers,
        "active_editors": active_editors,
        "total_written": total_written,
        "total_published": total_published,
        "total_sold": total_sold,
    }