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
        # Ensure additional columns exist on users table
        for col_def in [
            "first_name TEXT",
            "username TEXT",
            "total_mails_created INTEGER DEFAULT 0",
        ]:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col_def};")
            except Exception:
                pass

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
                otp TEXT,
                seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (email, message_id)
            )
        """)
        try:
            await db.execute("ALTER TABLE seen_messages ADD COLUMN otp TEXT")
        except Exception:
            pass
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_messages_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                email TEXT,
                message_id INTEGER,
                has_otp INTEGER DEFAULT 0,
                received_date DATE DEFAULT (DATE('now')),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

async def get_or_create_user(user_id: int, first_name: str = "", username: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, active_email, auto_forward, first_name, username, total_mails_created FROM users WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                if first_name or username:
                    await db.execute(
                        "UPDATE users SET first_name = COALESCE(NULLIF(?, ''), first_name), username = COALESCE(NULLIF(?, ''), username) WHERE user_id = ?",
                        (first_name, username, user_id)
                    )
                    await db.commit()
                return {
                    "user_id": row[0],
                    "active_email": row[1],
                    "auto_forward": row[2],
                    "first_name": row[3],
                    "username": row[4],
                    "total_mails_created": row[5] or 0,
                    "is_new": False
                }
            
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, first_name, username) VALUES (?, ?, ?)",
            (user_id, first_name, username)
        )
        await db.commit()
        return {
            "user_id": user_id,
            "active_email": None,
            "auto_forward": 1,
            "first_name": first_name,
            "username": username,
            "total_mails_created": 0,
            "is_new": True
        }

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
            "UPDATE users SET active_email = ?, total_mails_created = COALESCE(total_mails_created, 0) + 1 WHERE user_id = ?",
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

async def mark_message_seen(email: str, message_id: int, otp: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO seen_messages (email, message_id, otp) VALUES (?, ?, ?) "
            "ON CONFLICT(email, message_id) DO UPDATE SET otp = COALESCE(excluded.otp, seen_messages.otp)",
            (email, message_id, otp)
        )
        await db.commit()

async def get_saved_otp(email: str, message_id: int) -> str | None:
    """Retrieves saved OTP for a specific email and message_id if available"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT otp FROM seen_messages WHERE email = ? AND message_id = ? LIMIT 1",
            (email, message_id)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row and row[0] else None

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

async def log_user_message(user_id: int, email: str, message_id: int, has_otp: int = 0):
    """Logs incoming message/OTP for statistics tracking"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO user_messages_log (user_id, email, message_id, has_otp, received_date)
            VALUES (?, ?, ?, ?, DATE('now'))
        """, (user_id, email, message_id, has_otp))
        await db.commit()

async def get_all_users_stats():
    """Returns list of users with total mails created, daily codes and daily messages"""
    async with aiosqlite.connect(DB_PATH) as db:
        query = """
            SELECT 
                u.user_id,
                COALESCE(u.first_name, 'No Name') as first_name,
                COALESCE(u.username, '') as tg_username,
                COALESCE(u.active_email, 'None') as active_email,
                COALESCE(u.total_mails_created, 0) as total_mails,
                (SELECT COUNT(*) FROM user_messages_log m WHERE m.user_id = u.user_id AND m.received_date = DATE('now') AND m.has_otp = 1) as daily_codes,
                (SELECT COUNT(*) FROM user_messages_log m WHERE m.user_id = u.user_id AND m.received_date = DATE('now')) as daily_messages,
                (SELECT COUNT(*) FROM user_messages_log m WHERE m.user_id = u.user_id) as total_messages
            FROM users u
            ORDER BY u.created_at DESC
        """
        async with db.execute(query) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "user_id": r[0],
                    "first_name": r[1],
                    "username": r[2],
                    "active_email": r[3],
                    "total_mails": r[4],
                    "daily_codes": r[5],
                    "daily_messages": r[6],
                    "total_messages": r[7]
                }
                for r in rows
            ]

async def get_top_users(limit: int = 10):
    """Returns top users ranked by most emails created and messages received"""
    async with aiosqlite.connect(DB_PATH) as db:
        query = """
            SELECT 
                u.user_id,
                COALESCE(u.first_name, 'No Name') as first_name,
                COALESCE(u.username, '') as tg_username,
                COALESCE(u.total_mails_created, 0) as total_mails,
                (SELECT COUNT(*) FROM user_messages_log m WHERE m.user_id = u.user_id) as total_messages,
                (SELECT COUNT(*) FROM user_messages_log m WHERE m.user_id = u.user_id AND m.has_otp = 1) as total_codes
            FROM users u
            ORDER BY total_mails DESC, total_messages DESC
            LIMIT ?
        """
        async with db.execute(query, (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "user_id": r[0],
                    "first_name": r[1],
                    "username": r[2],
                    "total_mails": r[3],
                    "total_messages": r[4],
                    "total_codes": r[5]
                }
                for r in rows
            ]

async def get_all_user_ids():
    """Returns all user IDs for broadcast"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]

async def get_live_analytics():
    """Fetches real-time system analytics and statistics"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            total_users = (await cur.fetchone())[0]

        async with db.execute("SELECT COALESCE(SUM(total_mails_created), 0) FROM users") as cur:
            total_mails = (await cur.fetchone())[0]

        async with db.execute("""
            SELECT COUNT(DISTINCT user_id) FROM (
                SELECT user_id FROM user_messages_log WHERE received_date = DATE('now')
                UNION
                SELECT user_id FROM saved_emails WHERE DATE(created_at) = DATE('now')
            )
        """) as cur:
            active_today = (await cur.fetchone())[0]

        async with db.execute("SELECT COUNT(*) FROM user_messages_log WHERE received_date = DATE('now') AND has_otp = 1") as cur:
            today_otps = (await cur.fetchone())[0]

        async with db.execute("SELECT COUNT(*) FROM user_messages_log WHERE received_date = DATE('now')") as cur:
            today_messages = (await cur.fetchone())[0]

        async with db.execute("SELECT COUNT(*) FROM users WHERE active_email IS NOT NULL AND active_email != '' AND auto_forward = 1") as cur:
            listening_now = (await cur.fetchone())[0]

        return {
            "total_users": total_users,
            "total_mails": total_mails,
            "active_today": active_today,
            "today_otps": today_otps,
            "today_messages": today_messages,
            "listening_now": listening_now,
        }

