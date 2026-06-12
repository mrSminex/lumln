import aiosqlite
from config import DB_PATH
from tz_utils import now_msk


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                name        TEXT    NOT NULL,
                phone       TEXT    NOT NULL,
                consent     INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS formulas (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id   INTEGER NOT NULL,
                title       TEXT    NOT NULL,
                content     TEXT    NOT NULL,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                created_by  TEXT,
                FOREIGN KEY (client_id) REFERENCES clients(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS broadcasts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                message     TEXT    NOT NULL,
                sent_at     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                sent_by     TEXT,
                recipients  INTEGER NOT NULL DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                message     TEXT    NOT NULL,
                send_at     TEXT    NOT NULL,
                sent        INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS review_requests (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id   INTEGER NOT NULL,
                formula_id  INTEGER NOT NULL,
                sent_at     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                UNIQUE(client_id, formula_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id   INTEGER NOT NULL,
                formula_id  INTEGER NOT NULL,
                q1_score    INTEGER NOT NULL,
                q2_score    INTEGER,
                feedback    TEXT,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS certificate_requests (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                client_name TEXT    NOT NULL,
                cert_type   TEXT    NOT NULL,
                persons     INTEGER NOT NULL,
                tariff      TEXT    NOT NULL,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reorder_requests (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id   INTEGER NOT NULL,
                client_name   TEXT    NOT NULL,
                formula_id    INTEGER,
                formula_title TEXT    NOT NULL,
                volume        INTEGER NOT NULL,
                created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
        """)
        await db.commit()


# ─── Клиенты ──────────────────────────────────────────────────────────────────

async def get_client_by_telegram_id(telegram_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM clients WHERE telegram_id = ?", (telegram_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_client_by_id(client_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM clients WHERE id = ?", (client_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def create_client(telegram_id: int, name: str, phone: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO clients (telegram_id, name, phone, consent) VALUES (?, ?, ?, 1)",
            (telegram_id, name, phone),
        )
        await db.commit()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM clients WHERE telegram_id = ?", (telegram_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row)


async def get_all_clients() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM clients ORDER BY created_at DESC"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def search_clients(query: str) -> list[dict]:
    q = f"%{query}%"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM clients WHERE name LIKE ? OR phone LIKE ? ORDER BY name",
            (q, q),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_all_telegram_ids() -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT telegram_id FROM clients") as cur:
            rows = await cur.fetchall()
            return [r[0] for r in rows]


# ─── Формулы ──────────────────────────────────────────────────────────────────

async def get_formulas_by_client(client_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM formulas WHERE client_id = ? ORDER BY created_at DESC",
            (client_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_formula_by_id(formula_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM formulas WHERE id = ?", (formula_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def add_formula(client_id: int, title: str, content: str, created_by: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO formulas (client_id, title, content, created_by) VALUES (?, ?, ?, ?)",
            (client_id, title, content, created_by),
        )
        await db.commit()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM formulas WHERE client_id = ? ORDER BY id DESC LIMIT 1",
            (client_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row)


async def update_formula_content(formula_id: int, content: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE formulas SET content = ? WHERE id = ?", (content, formula_id)
        )
        await db.commit()


async def delete_formula(formula_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM formulas WHERE id = ?", (formula_id,))
        await db.commit()


# ─── Рассылки ─────────────────────────────────────────────────────────────────

async def save_broadcast(message: str, sent_by: str, recipients: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO broadcasts (message, sent_by, recipients) VALUES (?, ?, ?)",
            (message, sent_by, recipients),
        )
        await db.commit()


async def get_broadcasts() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM broadcasts ORDER BY sent_at DESC LIMIT 20"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


# ─── Отложенные сообщения ─────────────────────────────────────────────────────

async def create_scheduled_message(telegram_id: int, message: str, send_at: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO scheduled_messages (telegram_id, message, send_at) VALUES (?, ?, ?)",
            (telegram_id, message, send_at),
        )
        await db.commit()


async def get_pending_scheduled_messages() -> list[dict]:
    """Возвращает сообщения, у которых send_at <= текущего времени и ещё не отправлены."""
    now = now_msk().strftime("%Y-%m-%d %H:%M")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM scheduled_messages WHERE sent = 0 AND send_at <= ?", (now,)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def mark_scheduled_sent(msg_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE scheduled_messages SET sent = 1 WHERE id = ?", (msg_id,)
        )
        await db.commit()


async def get_scheduled_messages_pending_list() -> list[dict]:
    """Список запланированных (не отправленных) для отображения в боте."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM scheduled_messages WHERE sent = 0 ORDER BY send_at"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


# ─── Отзывы ───────────────────────────────────────────────────────────────────

async def get_formulas_needing_review() -> list[dict]:
    """
    Формулы, добавленные 23–25 часов назад, по которым ещё не отправлялся запрос отзыва.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT f.*
            FROM formulas f
            LEFT JOIN review_requests rr
                ON rr.formula_id = f.id AND rr.client_id = f.client_id
            WHERE rr.id IS NULL
              AND f.created_at BETWEEN
                  datetime('now', 'localtime', '-25 hours') AND
                  datetime('now', 'localtime', '-23 hours')
        """) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def create_review_request(client_id: int, formula_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO review_requests (client_id, formula_id) VALUES (?, ?)",
            (client_id, formula_id),
        )
        await db.commit()


async def save_review(
    client_id: int, formula_id: int, q1: int, q2: int | None, feedback: str | None
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO reviews (client_id, formula_id, q1_score, q2_score, feedback) "
            "VALUES (?, ?, ?, ?, ?)",
            (client_id, formula_id, q1, q2, feedback),
        )
        await db.commit()


# ─── Заявки: сертификат и повтор ──────────────────────────────────────────────

async def save_certificate_request(
    telegram_id: int, client_name: str, cert_type: str, persons: int, tariff: str
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO certificate_requests (telegram_id, client_name, cert_type, persons, tariff) "
            "VALUES (?, ?, ?, ?, ?)",
            (telegram_id, client_name, cert_type, persons, tariff),
        )
        await db.commit()
        return cur.lastrowid


async def save_reorder_request(
    telegram_id: int, client_name: str,
    formula_id: int | None, formula_title: str, volume: int
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO reorder_requests (telegram_id, client_name, formula_id, formula_title, volume) "
            "VALUES (?, ?, ?, ?, ?)",
            (telegram_id, client_name, formula_id, formula_title, volume),
        )
        await db.commit()
        return cur.lastrowid
