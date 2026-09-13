import time
import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT,
    username TEXT,
    is_premium INTEGER DEFAULT 0,
    phone TEXT,
    source_chat TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_phone ON users(phone);
CREATE INDEX IF NOT EXISTS idx_source ON users(source_chat);

CREATE TABLE IF NOT EXISTS user_settings (
    tg_id INTEGER PRIMARY KEY,
    only_phone INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS membership (
    chat TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    first_seen REAL DEFAULT 0,
    last_seen REAL DEFAULT 0,
    PRIMARY KEY (chat, user_id)
);
CREATE INDEX IF NOT EXISTS idx_mem_user ON membership(user_id);
CREATE INDEX IF NOT EXISTS idx_mem_chat_seen ON membership(chat, last_seen);

CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat TEXT NOT NULL,
    started_at REAL DEFAULT 0,
    finished_at REAL DEFAULT 0,
    seen INTEGER DEFAULT 0,
    is_first INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_scans_chat ON scans(chat, finished_at);

CREATE TABLE IF NOT EXISTS watchlist (
    chat TEXT PRIMARY KEY,
    interval_h REAL DEFAULT 24,
    next_run REAL DEFAULT 0,
    owner_id INTEGER DEFAULT 0
);
"""

async def init_db(db_path: str):
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        async with db.execute("PRAGMA table_info(users)") as cur:
            cols = [r[1] for r in await cur.fetchall()]
        if "has_photo" not in cols:
            await db.execute("ALTER TABLE users ADD COLUMN has_photo INTEGER DEFAULT 0")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_photo ON users(has_photo)")
        await db.commit()

async def upsert_user(db_path: str, uid: int, name: str, username: str | None,
                      is_premium: bool, phone: str | None, source: str, has_photo: bool = False):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO users (id, name, username, is_premium, phone, source_chat, has_photo, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                username=excluded.username,
                is_premium=excluded.is_premium,
                phone=COALESCE(excluded.phone, users.phone),
                source_chat=excluded.source_chat,
                has_photo=max(users.has_photo, excluded.has_photo),
                updated_at=CURRENT_TIMESTAMP
            """,
            (uid, name, username, int(bool(is_premium)), phone, source, int(bool(has_photo))),
        )
        await db.commit()

async def upsert_many(db_path: str, rows: list[tuple]):
    if not rows:
        return
    norm = [(r + (0,))[:7] if len(r) == 6 else r[:7] for r in rows]
    async with aiosqlite.connect(db_path) as db:
        await db.executemany(
            """
            INSERT INTO users (id, name, username, is_premium, phone, source_chat, has_photo, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                username=excluded.username,
                is_premium=excluded.is_premium,
                phone=COALESCE(excluded.phone, users.phone),
                source_chat=excluded.source_chat,
                has_photo=max(users.has_photo, excluded.has_photo),
                updated_at=CURRENT_TIMESTAMP
            """,
            norm,
        )
        await db.commit()

async def record_membership_many(db_path: str, chat: str, user_ids: list[int], now: float | None = None):
    if not user_ids:
        return
    now = now or time.time()
    async with aiosqlite.connect(db_path) as db:
        await db.executemany(
            """INSERT INTO membership (chat, user_id, first_seen, last_seen)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(chat, user_id) DO UPDATE SET last_seen=excluded.last_seen""",
            [(chat, uid, now, now) for uid in user_ids],
        )
        await db.commit()

async def record_scan(db_path: str, chat: str, t0: float, seen: int, is_first: bool):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO scans (chat, started_at, finished_at, seen, is_first) VALUES (?,?,?,?,?)",
            (chat, t0, time.time(), seen, int(is_first)),
        )
        await db.commit()

async def chat_has_history(db_path: str, chat: str) -> bool:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM scans WHERE chat=?", (chat,)) as c:
            return (await c.fetchone())[0] > 0

async def new_members(db_path: str, chat: str, t0: float, limit: int = 20) -> tuple[int, list[dict]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT COUNT(*) FROM membership WHERE chat=? AND first_seen>=?",
                              (chat, t0)) as c:
            total = (await c.fetchone())[0]
        async with db.execute(
            """SELECT u.id, u.name, u.username FROM membership m
               JOIN users u ON u.id=m.user_id
               WHERE m.chat=? AND m.first_seen>=? ORDER BY u.id LIMIT ?""",
            (chat, t0, limit)) as c:
            return total, [dict(r) for r in await c.fetchall()]

async def left_members(db_path: str, chat: str, t0: float, limit: int = 20) -> tuple[int, list[dict]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT COUNT(*) FROM membership WHERE chat=? AND first_seen<? AND last_seen<?",
                              (chat, t0, t0)) as c:
            total = (await c.fetchone())[0]
        async with db.execute(
            """SELECT u.id, u.name, u.username FROM membership m
               JOIN users u ON u.id=m.user_id
               WHERE m.chat=? AND m.first_seen<? AND m.last_seen<? ORDER BY u.id LIMIT ?""",
            (chat, t0, t0, limit)) as c:
            return total, [dict(r) for r in await c.fetchall()]

async def cross_joiners(db_path: str, chat: str, t0: float, limit: int = 15) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT u.id, u.name, u.username,
                      (SELECT GROUP_CONCAT(m2.chat, ', ') FROM membership m2
                        WHERE m2.user_id=u.id AND m2.chat!=?) AS other_chats
               FROM membership m JOIN users u ON u.id=m.user_id
               WHERE m.chat=? AND m.first_seen>=?
                 AND EXISTS (SELECT 1 FROM membership m3 WHERE m3.user_id=u.id AND m3.chat!=?)
               ORDER BY u.id LIMIT ?""",
            (chat, chat, t0, chat, limit)) as c:
            return [dict(r) for r in await c.fetchall()]

async def anchors(db_path: str, min_chats: int = 3, limit: int = 25) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT COUNT(*) FROM membership m1 JOIN membership m2 ON m2.user_id=m1.user_id "
            "WHERE m1.chat=? AND m2.chat=?", (a, b)) as c:
            total = (await c.fetchone())[0]
        async with db.execute(
            """SELECT u.id, u.name, u.username FROM membership m1
               JOIN membership m2 ON m2.user_id=m1.user_id
               JOIN users u ON u.id=m1.user_id
               WHERE m1.chat=? AND m2.chat=? ORDER BY u.id LIMIT ?""", (a, b, limit)) as c:
            return total, [dict(r) for r in await c.fetchall()]

async def known_chats(db_path: str, limit: int = 50) -> list[tuple[str, int]]:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT chat, COUNT(*) FROM membership GROUP BY chat ORDER BY 2 DESC LIMIT ?", (limit,)) as c:
            return await c.fetchall()

async def overlap(db_path: str, a: str, b: str, limit: int = 20) -> tuple[int, list[dict]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT u.id, u.name, u.username, COUNT(*) AS n,
                      GROUP_CONCAT(m.chat, ', ') AS chats
               FROM membership m JOIN users u ON u.id=m.user_id
               GROUP BY u.id HAVING n>=? ORDER BY n DESC, u.id LIMIT ?""",
            (min_chats, limit)) as c:
            return [dict(r) for r in await c.fetchall()]

async def warm_segment(db_path: str, min_chats: int = 2, need_username: bool = True,
                       limit: int = 5000) -> list[dict]:
    q = """SELECT u.id, u.name, u.username, u.phone, u.is_premium, COUNT(*) AS n,
                  GROUP_CONCAT(m.chat, ', ') AS chats
           FROM membership m JOIN users u ON u.id=m.user_id
           GROUP BY u.id HAVING n>=?"""
    if need_username:
        q += " AND u.username IS NOT NULL AND u.username!=''"
    q += " ORDER BY n DESC, u.id LIMIT ?"
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(q, (min_chats, limit)) as c:
            return [dict(r) for r in await c.fetchall()]

async def segments(db_path: str) -> dict:
    async with aiosqlite.connect(db_path) as db:
        s = {}
        async with db.execute("SELECT COUNT(*) FROM users") as c:
            s["total"] = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE username IS NOT NULL AND username!=''") as c:
            s["username"] = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE has_photo=1") as c:
            s["photo"] = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE is_premium=1") as c:
            s["premium"] = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE phone IS NOT NULL AND phone!=''") as c:
            s["phone"] = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM (SELECT user_id FROM membership GROUP BY user_id HAVING COUNT(*)>=2)") as c:
            s["multi"] = (await c.fetchone())[0]
        return s

async def scan_history(db_path: str, chat: str, limit: int = 30) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT finished_at, seen FROM scans WHERE chat=? ORDER BY finished_at LIMIT ?", (chat, limit)) as c:
            return [dict(r) for r in await c.fetchall()]

async def last_scan(db_path: str, chat: str) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT started_at, finished_at, seen FROM scans WHERE chat=? ORDER BY finished_at DESC LIMIT 1",
            (chat,)) as c:
            r = await c.fetchone()
            return dict(r) if r else None

async def search_users(db_path: str, query: str, limit: int = 10) -> list[dict]:
    q = query.strip().lstrip("@")
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        if q.isdigit():
            async with db.execute(
                """SELECT u.*, (SELECT GROUP_CONCAT(chat, ', ') FROM membership m WHERE m.user_id=u.id) AS chats
                   FROM users u WHERE u.id=? LIMIT ?""", (int(q), limit)) as c:
                return [dict(r) for r in await c.fetchall()]
        like = f"%{q}%"
        async with db.execute(
            """SELECT u.*, (SELECT GROUP_CONCAT(chat, ', ') FROM membership m WHERE m.user_id=u.id) AS chats
               FROM users u WHERE u.username LIKE ? OR u.name LIKE ? OR CAST(u.id AS TEXT) LIKE ?
               ORDER BY u.id LIMIT ?""", (like, like, like, limit)) as c:
            return [dict(r) for r in await c.fetchall()]

async def user_chats(db_path: str, uid: int) -> list[str]:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT chat FROM membership WHERE user_id=?", (uid,)) as c:
            return [r[0] for r in await c.fetchall()]

async def get_only_phone(db_path: str, tg_id: int) -> bool:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT only_phone FROM user_settings WHERE tg_id=?", (tg_id,)) as cur:
            row = await cur.fetchone()
            return bool(row[0]) if row else False

async def toggle_only_phone(db_path: str, tg_id: int) -> bool:
    cur_val = await get_only_phone(db_path, tg_id)
    new_val = int(not cur_val)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO user_settings (tg_id, only_phone) VALUES (?, ?) "
            "ON CONFLICT(tg_id) DO UPDATE SET only_phone=excluded.only_phone",
            (tg_id, new_val),
        )
        await db.commit()
    return bool(new_val)

async def get_stats(db_path: str) -> dict:
    async with aiosqlite.connect(db_path) as db:
        stats = {}
        async with db.execute("SELECT COUNT(*) FROM users") as c:
            stats["total"] = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE username IS NOT NULL AND username!=''") as c:
            stats["with_username"] = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE phone IS NOT NULL AND phone!=''") as c:
            stats["with_phone"] = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE is_premium=1") as c:
            stats["premium"] = (await c.fetchone())[0]
        async with db.execute("SELECT source_chat, COUNT(*) FROM users GROUP BY source_chat ORDER BY 2 DESC LIMIT 10") as c:
            stats["top_sources"] = await c.fetchall()
        return stats

async def fetch_all(db_path: str, only_phone: bool = False) -> list[dict]:
    q = "SELECT id, name, username, is_premium, phone, source_chat, updated_at FROM users"
    if only_phone:
        q += " WHERE phone IS NOT NULL AND phone!=''"
    q += " ORDER BY id"
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(q) as cur:
            return [dict(r) for r in await cur.fetchall()]

async def watch_add(db_path: str, chat: str, interval_h: float, owner_id: int):
    import time as _t
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO watchlist (chat, interval_h, next_run, owner_id) VALUES (?,?,?,?) "
            "ON CONFLICT(chat) DO UPDATE SET interval_h=excluded.interval_h, "
            "next_run=excluded.next_run, owner_id=excluded.owner_id",
            (chat, interval_h, _t.time() + interval_h * 3600, owner_id),
        )
        await db.commit()

async def watch_del(db_path: str, chat: str) -> bool:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("DELETE FROM watchlist WHERE chat=?", (chat,))
        await db.commit()
        return cur.rowcount > 0

async def watch_list(db_path: str) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT chat, interval_h, next_run, owner_id FROM watchlist ORDER BY next_run") as c:
            return [dict(r) for r in await c.fetchall()]

async def watch_due(db_path: str) -> list[dict]:
    import time as _t
    due = [dict(r) for r in await watch_list(db_path) if r["next_run"] <= _t.time()]
    if due:
        async with aiosqlite.connect(db_path) as db:
            for w in due:
                await db.execute("UPDATE watchlist SET next_run=? WHERE chat=?",
                                 (_t.time() + w["interval_h"] * 3600, w["chat"]))
            await db.commit()
    return due
