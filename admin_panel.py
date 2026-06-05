import asyncio
import aiosqlite
from fastapi import FastAPI, Request, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
import io
from config import ADMIN_PASSWORD, DB_PATH

app = FastAPI()
templates = Jinja2Templates(directory="templates")

SESSION_COOKIE = "lumln_session"
SESSION_TOKEN  = "lumln_authenticated"


def is_auth(request: Request) -> bool:
    return request.cookies.get(SESSION_COOKIE) == SESSION_TOKEN


def redirect_login():
    return RedirectResponse("/login", status_code=302)


# ─── Auth ─────────────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        resp = RedirectResponse("/", status_code=302)
        resp.set_cookie(SESSION_COOKIE, SESSION_TOKEN, httponly=True, max_age=86400 * 30)
        return resp
    return templates.TemplateResponse("login.html", {"request": request, "error": "Неверный пароль"})


@app.get("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _stats():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM clients")   as c: clients   = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM formulas")  as c: formulas  = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM broadcasts") as c: broadcasts = (await c.fetchone())[0]
    return {"clients": clients, "formulas": formulas, "broadcasts": broadcasts}


async def _all_clients_with_counts():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT c.*, COUNT(f.id) AS formula_count
            FROM clients c
            LEFT JOIN formulas f ON f.client_id = c.id
            GROUP BY c.id
            ORDER BY c.created_at DESC
        """) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def _search_clients(q: str):
    like = f"%{q}%"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT c.*, COUNT(f.id) AS formula_count
            FROM clients c
            LEFT JOIN formulas f ON f.client_id = c.id
            WHERE c.name LIKE ? OR c.phone LIKE ?
            GROUP BY c.id ORDER BY c.name
        """, (like, like)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def _client(client_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def _formulas(client_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM formulas WHERE client_id = ? ORDER BY created_at DESC", (client_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def _all_formulas():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT f.*, c.name AS client_name
            FROM formulas f
            JOIN clients c ON c.id = f.client_id
            ORDER BY f.created_at DESC
        """) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if not is_auth(request):
        return redirect_login()
    clients = await _all_clients_with_counts()
    return templates.TemplateResponse("home.html", {
        "request": request,
        "active": "home",
        "stats": await _stats(),
        "recent_clients": clients[:10],
    })


@app.get("/clients", response_class=HTMLResponse)
async def clients_page(request: Request, q: str = ""):
    if not is_auth(request):
        return redirect_login()
    clients = await _search_clients(q) if q else await _all_clients_with_counts()
    return templates.TemplateResponse("clients.html", {
        "request": request, "active": "clients",
        "clients": clients, "query": q,
    })


@app.get("/clients/{client_id}", response_class=HTMLResponse)
async def client_detail(request: Request, client_id: int, msg: str = ""):
    if not is_auth(request):
        return redirect_login()
    client = await _client(client_id)
    if not client:
        return RedirectResponse("/clients", status_code=302)
    formulas = await _formulas(client_id)
    return templates.TemplateResponse("client_detail.html", {
        "request": request, "active": "clients",
        "client": client, "formulas": formulas, "msg": msg,
    })


@app.post("/clients/{client_id}/formula")
async def add_formula(request: Request, client_id: int,
                      title: str = Form(...), content: str = Form(...)):
    if not is_auth(request):
        return redirect_login()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO formulas (client_id, title, content, created_by) VALUES (?, ?, ?, ?)",
            (client_id, title, content, "Веб-панель"),
        )
        await db.commit()

    # Уведомляем клиента через бот
    client = await _client(client_id)
    if client:
        try:
            from bot import bot
            await bot.send_message(
                client["telegram_id"],
                f"🌸 <b>Ваша формула готова!</b>\n\n<b>{title}</b>\n\n{content}\n\n"
                "Доступна в разделе «Мои формулы».",
                parse_mode="HTML",
            )
        except Exception:
            pass

    return RedirectResponse(
        f"/clients/{client_id}?msg=Формула+добавлена", status_code=302
    )


@app.post("/formulas/{formula_id}/delete")
async def delete_formula(request: Request, formula_id: int):
    if not is_auth(request):
        return redirect_login()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT client_id FROM formulas WHERE id = ?", (formula_id,)
        ) as cur:
            row = await cur.fetchone()
        client_id = row[0] if row else 0
        await db.execute("DELETE FROM formulas WHERE id = ?", (formula_id,))
        await db.commit()
    return RedirectResponse(f"/clients/{client_id}?msg=Формула+удалена", status_code=302)


@app.get("/formulas", response_class=HTMLResponse)
async def formulas_page(request: Request):
    if not is_auth(request):
        return redirect_login()
    formulas = await _all_formulas()
    return templates.TemplateResponse("formulas.html", {
        "request": request, "active": "formulas", "formulas": formulas,
    })


@app.get("/broadcast", response_class=HTMLResponse)
async def broadcast_page(request: Request, msg: str = "", error: str = ""):
    if not is_auth(request):
        return redirect_login()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT COUNT(*) FROM clients") as c:
            client_count = (await c.fetchone())[0]
        async with db.execute(
            "SELECT * FROM broadcasts ORDER BY sent_at DESC LIMIT 20"
        ) as c:
            broadcasts = [dict(r) for r in await c.fetchall()]
    return templates.TemplateResponse("broadcast.html", {
        "request": request, "active": "broadcast",
        "client_count": client_count, "broadcasts": broadcasts,
        "msg": msg, "error": error,
    })


@app.post("/broadcast")
async def broadcast_send(request: Request, message: str = Form(...)):
    if not is_auth(request):
        return redirect_login()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT telegram_id FROM clients") as cur:
            ids = [r[0] for r in await cur.fetchall()]

    sent = 0
    try:
        from bot import bot
        for tid in ids:
            try:
                await bot.send_message(tid, message, parse_mode="HTML")
                sent += 1
                await asyncio.sleep(0.05)
            except Exception:
                pass
    except Exception as e:
        return RedirectResponse(f"/broadcast?error=Ошибка+бота:+{str(e)[:50]}", status_code=302)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO broadcasts (message, sent_by, recipients) VALUES (?, ?, ?)",
            (message, "Веб-панель", sent),
        )
        await db.commit()

    return RedirectResponse(
        f"/broadcast?msg=Отправлено+{sent}+из+{len(ids)}+клиентам", status_code=302
    )


# ─── Экспорт в Excel ──────────────────────────────────────────────────────────

@app.get("/export/excel")
async def export_excel(request: Request):
    if not is_auth(request):
        return redirect_login()

    from excel_export import build_excel
    data, filename = await build_excel()

    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─── Бэкапы ───────────────────────────────────────────────────────────────────

@app.get("/backup", response_class=HTMLResponse)
async def backup_page(request: Request, msg: str = "", error: str = ""):
    if not is_auth(request):
        return redirect_login()

    from backup import list_local_backups
    from config import BACKUP_HOUR, BACKUP_KEEP, BACKUP_CHAT_ID, BACKUP_DIR

    return templates.TemplateResponse("backup.html", {
        "request": request,
        "active": "backup",
        "backups": list_local_backups(),
        "backup_hour": BACKUP_HOUR,
        "backup_keep": BACKUP_KEEP,
        "backup_chat_id": BACKUP_CHAT_ID,
        "backup_dir": BACKUP_DIR,
        "msg": msg,
        "error": error,
    })


@app.post("/backup/now")
async def backup_now(request: Request):
    if not is_auth(request):
        return redirect_login()

    from backup import run_backup
    result = await run_backup(initiated_by="web-panel")

    if result["ok"]:
        tg_note = " + отправлен в Telegram" if result["telegram"] else ""
        msg = f"Бэкап создан: {result['file']} ({result['size_kb']} KB{tg_note})"
        return RedirectResponse(f"/backup?msg={msg}", status_code=302)
    else:
        return RedirectResponse(f"/backup?error=Ошибка: {result['error'][:80]}", status_code=302)


@app.get("/backup/download/{filename}")
async def backup_download(request: Request, filename: str):
    if not is_auth(request):
        return redirect_login()

    from pathlib import Path
    from config import BACKUP_DIR

    # Защита от path traversal
    safe_name = Path(filename).name
    backup_path = Path(BACKUP_DIR) / safe_name

    if not backup_path.exists() or not safe_name.startswith("lumln_"):
        return HTMLResponse("Файл не найден", status_code=404)

    return StreamingResponse(
        open(backup_path, "rb"),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )
