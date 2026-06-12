import asyncio
import aiosqlite
from datetime import datetime
from fastapi import FastAPI, Request, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
import io
from config import ADMIN_PASSWORD, DB_PATH
from tz_utils import now_msk

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


@app.get("/formulas/{formula_id}/edit", response_class=HTMLResponse)
async def edit_formula_page(request: Request, formula_id: int):
    if not is_auth(request):
        return redirect_login()

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT f.*, c.name as client_name 
               FROM formulas f 
               JOIN clients c ON c.id = f.client_id 
               WHERE f.id = ?""",
            (formula_id,)
        ) as cur:
            row = await cur.fetchone()
            formula = dict(row) if row else None

    if not formula:
        return RedirectResponse("/formulas", status_code=302)

    return templates.TemplateResponse("formula_edit.html", {
        "request": request, 
        "active": "formulas", 
        "formula": formula,
    })


@app.post("/formulas/{formula_id}/edit")
async def edit_formula_save(request: Request, formula_id: int,
                            content: str = Form(...)):
    if not is_auth(request):
        return redirect_login()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT client_id FROM formulas WHERE id = ?", (formula_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return RedirectResponse("/formulas", status_code=302)

        client_id = row[0]
        await db.execute(
            "UPDATE formulas SET content = ? WHERE id = ?",
            (content, formula_id)
        )
        await db.commit()

    client = await _client(client_id)
    if client:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT title FROM formulas WHERE id = ?", (formula_id,)) as cur:
                formula = dict(await cur.fetchone())
        try:
            from bot import bot
            await bot.send_message(
                client["telegram_id"],
                f"📝 <b>Ваша формула была обновлена</b>\n\n"
                f"<b>{formula['title']}</b>\n\n{content}",
                parse_mode="HTML",
            )
        except Exception:
            pass

    return RedirectResponse(f"/clients/{client_id}?msg=Формула+обновлена", status_code=302)


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


# ─── Новые маршруты ───────────────────────────────────────────────────────────

@app.get("/individual-message", response_class=HTMLResponse)
async def individual_message_page(request: Request, msg: str = ""):
    if not is_auth(request):
        return redirect_login()
    clients = await _all_clients_with_counts()
    return templates.TemplateResponse("individual_message.html", {
        "request": request, "active": "messages", "clients": clients, "msg": msg,
    })


@app.post("/individual-message")
async def individual_message_send(request: Request, client_id: int = Form(...),
                                  message: str = Form(...)):
    if not is_auth(request):
        return redirect_login()

    client = await _client(client_id)
    if not client:
        return RedirectResponse("/individual-message?msg=Клиент+не+найден", status_code=302)

    try:
        from bot import bot
        await bot.send_message(client["telegram_id"], message, parse_mode="HTML")
        return RedirectResponse(
            f"/individual-message?msg=Отправлено+клиенту+{client['name']}", status_code=302
        )
    except Exception as e:
        return RedirectResponse(
            f"/individual-message?msg=Ошибка:+{str(e)[:40]}", status_code=302
        )


@app.get("/scheduled", response_class=HTMLResponse)
async def scheduled_page(request: Request, msg: str = ""):
    if not is_auth(request):
        return redirect_login()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM scheduled_messages WHERE sent = 0 ORDER BY send_at DESC"
        ) as cur:
            scheduled = [dict(r) for r in await cur.fetchall()]
    clients = await _all_clients_with_counts()
    return templates.TemplateResponse("scheduled.html", {
        "request": request, "active": "scheduled", "scheduled": scheduled,
        "clients": clients, "msg": msg,
        "now_msk": now_msk().strftime("%Y-%m-%dT%H:%M"),
    })


@app.post("/scheduled")
async def scheduled_create(request: Request, target: str = Form(...),
                          message: str = Form(...), send_at: str = Form(...)):
    if not is_auth(request):
        return redirect_login()

    try:
        dt = datetime.strptime(send_at, "%Y-%m-%dT%H:%M")
        send_at_str = dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return RedirectResponse("/scheduled?msg=Неверный+формат+даты", status_code=302)

    if dt <= now_msk():
        return RedirectResponse(
            "/scheduled?msg=Время+уже+прошло+(МСК).+Введите+дату+в+будущем",
            status_code=302,
        )

    async with aiosqlite.connect(DB_PATH) as db:
        if target == "all":
            ids = await _get_all_telegram_ids()
        else:
            client = await _client(int(target))
            ids = [client["telegram_id"]] if client else []

        for tid in ids:
            await db.execute(
                "INSERT INTO scheduled_messages (telegram_id, message, send_at) VALUES (?, ?, ?)",
                (tid, message, send_at_str),
            )
        await db.commit()

    return RedirectResponse(
        f"/scheduled?msg=Запланировано+для+{len(ids)}+получателей", status_code=302
    )


@app.post("/scheduled/{msg_id}/delete")
async def scheduled_delete(request: Request, msg_id: int):
    if not is_auth(request):
        return redirect_login()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM scheduled_messages WHERE id = ?", (msg_id,))
        await db.commit()
    return RedirectResponse("/scheduled?msg=Сообщение+удалено", status_code=302)


@app.get("/certificates", response_class=HTMLResponse)
async def certificates_page(request: Request):
    if not is_auth(request):
        return redirect_login()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM certificate_requests ORDER BY created_at DESC"
        ) as cur:
            requests = [dict(r) for r in await cur.fetchall()]
    return templates.TemplateResponse("certificates.html", {
        "request": request, "active": "certificates", "requests": requests,
    })


@app.get("/reorders", response_class=HTMLResponse)
async def reorders_page(request: Request):
    if not is_auth(request):
        return redirect_login()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM reorder_requests ORDER BY created_at DESC"
        ) as cur:
            requests = [dict(r) for r in await cur.fetchall()]
    return templates.TemplateResponse("reorders.html", {
        "request": request, "active": "reorders", "requests": requests,
    })


@app.get("/reviews", response_class=HTMLResponse)
async def reviews_page(request: Request):
    if not is_auth(request):
        return redirect_login()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT r.*, c.name as client_name, f.title as formula_title "
            "FROM reviews r "
            "JOIN clients c ON c.id = r.client_id "
            "LEFT JOIN formulas f ON f.id = r.formula_id "
            "ORDER BY r.created_at DESC"
        ) as cur:
            reviews = [dict(r) for r in await cur.fetchall()]
    return templates.TemplateResponse("reviews.html", {
        "request": request, "active": "reviews", "reviews": reviews,
    })


async def _get_all_telegram_ids():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT telegram_id FROM clients") as cur:
            return [r[0] for r in await cur.fetchall()]


# ─── Экспорт в Excel ──────────────────────────────────────────────────────────

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx_response(data: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(
        io.BytesIO(data),
        media_type=XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/export/full")
async def export_full(request: Request):
    if not is_auth(request):
        return redirect_login()
    from excel_export import build_excel_full
    return _xlsx_response(*await build_excel_full())


@app.get("/export/clients")
async def export_clients(request: Request):
    if not is_auth(request):
        return redirect_login()
    from excel_export import build_excel_clients
    return _xlsx_response(*await build_excel_clients())


@app.get("/export/formulas")
async def export_formulas(request: Request):
    if not is_auth(request):
        return redirect_login()
    from excel_export import build_excel_formulas
    return _xlsx_response(*await build_excel_formulas())


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

    safe_name = Path(filename).name
    backup_path = Path(BACKUP_DIR) / safe_name

    if not backup_path.exists() or not safe_name.startswith("lumln_"):
        return HTMLResponse("Файл не найден", status_code=404)

    return StreamingResponse(
        open(backup_path, "rb"),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )
