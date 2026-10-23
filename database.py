import aiosqlite
from datetime import datetime, timedelta
from config import DB_PATH, RETENTION_DAYS

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA busy_timeout=5000;")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                active_email TEXT,
                auto_forward INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS saved_emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                email TEXT,
                username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, email)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS seen_messages (
                email TEXT,
                message_id INTEGER,
                seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (email, message_id)
            )
        """)
        await db.commit()

async def get_or_create_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, active_email, auto_forward FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return {"user_id": row[0], "active_email": row[1], "auto_forward": row[2]}
            
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await db.commit()
        return {"user_id": user_id, "active_email": None, "auto_forward": 1}

async def set_active_email(user_id: int, email: str | None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET active_email = ? WHERE user_id = ?",
            (email, user_id)
        )
        await db.commit()

async def toggle_auto_forward(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT auto_forward FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            current = row[0] if row else 1
        new_val = 0 if current == 1 else 1
        await db.execute("UPDATE users SET auto_forward = ? WHERE user_id = ?", (new_val, user_id))
        await db.commit()
        return new_val

async def is_username_taken(username: str) -> bool:
    """Checks if a username is already taken within the last 10 days"""
    cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT 1 FROM saved_emails
            WHERE lower(username) = lower(?) AND datetime(created_at) >= datetime(?)
        """, (username, cutoff_str)) as cursor:
            return (await cursor.fetchone()) is not None

async def save_email(user_id: int, email: str, username: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO saved_emails (user_id, email, username, created_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, email) DO UPDATE SET created_at = CURRENT_TIMESTAMP
        """, (user_id, email, username))
        await db.execute(
            "UPDATE users SET active_email = ? WHERE user_id = ?",
            (email, user_id)
        )
        await db.commit()

async def get_saved_emails(user_id: int):
    # Returns emails saved within RETENTION_DAYS
    cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT id, email, username, created_at
            FROM saved_emails
            WHERE user_id = ? AND datetime(created_at) >= datetime(?)
            ORDER BY datetime(created_at) DESC
        """, (user_id, cutoff_str)) as cursor:
            rows = await cursor.fetchall()
            return [
                {"id": r[0], "email": r[1], "username": r[2], "created_at": r[3]}
                for r in rows
            ]

async def get_saved_email_by_id(email_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, email, username FROM saved_emails WHERE id = ? AND user_id = ?",
            (email_id, user_id)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {"id": row[0], "email": row[1], "username": row[2]}
            return None

async def delete_saved_email(user_id: int, email: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM saved_emails WHERE user_id = ? AND email = ?", (user_id, email))
        await db.execute(
            "UPDATE users SET active_email = NULL WHERE user_id = ? AND active_email = ?",
            (user_id, email)
        )
        await db.commit()

async def clear_all_saved_emails(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM saved_emails WHERE user_id = ?", (user_id,))
        await db.execute("UPDATE users SET active_email = NULL WHERE user_id = ?", (user_id,))
        await db.commit()

async def is_message_seen(email: str, message_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM seen_messages WHERE email = ? AND message_id = ?",
            (email, message_id)
        ) as cursor:
            return (await cursor.fetchone()) is not None

async def mark_message_seen(email: str, message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO seen_messages (email, message_id) VALUES (?, ?)",
            (email, message_id)
        )
        await db.commit()

async def get_email_by_message_id(message_id: int) -> str | None:
    """Finds the email address associated with a seen message_id"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT email FROM seen_messages WHERE message_id = ? LIMIT 1",
            (message_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def get_active_users_to_poll():
    """Returns list of (user_id, active_email) for users with active_email and auto_forward=1"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT user_id, active_email
            FROM users
            WHERE active_email IS NOT NULL AND active_email != '' AND auto_forward = 1
        """) as cursor:
            return await cursor.fetchall()

