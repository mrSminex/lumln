import aiosqlite
from config import DB_PATH


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
        await db.commit()


# --- Клиенты ---

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


# --- Формулы ---

async def get_formulas_by_client(client_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM formulas WHERE client_id = ? ORDER BY created_at DESC",
            (client_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


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


async def delete_formula(formula_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM formulas WHERE id = ?", (formula_id,))
        await db.commit()


# --- Рассылки ---

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
