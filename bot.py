import asyncio
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
)
import database as db
from config import BOT_TOKEN, ADMIN_IDS, YCLIENTS_URL, PRIVACY_URL, OFFER_URL, PROXY

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _make_bot() -> Bot:
    """Создаёт экземпляр Bot — с прокси или без, в зависимости от .env."""
    if PROXY:
        from aiogram.client.session.aiohttp import AiohttpSession
        session = AiohttpSession(proxy=PROXY)
        log.info(f"Using proxy: {PROXY}")
        return Bot(token=BOT_TOKEN, session=session)
    return Bot(token=BOT_TOKEN)


bot = _make_bot()
dp = Dispatcher(storage=MemoryStorage())


# ─── Состояния FSM ────────────────────────────────────────────────────────────

class Registration(StatesGroup):
    waiting_name    = State()
    waiting_phone   = State()
    waiting_consent = State()

class AdminAddFormula(StatesGroup):
    waiting_client_id = State()
    waiting_title     = State()
    waiting_content   = State()

class AdminBroadcast(StatesGroup):
    waiting_message = State()


# ─── Клавиатуры ───────────────────────────────────────────────────────────────

def kb_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧪 Мои формулы",    callback_data="my_formulas")],
        [InlineKeyboardButton(text="📅 Записаться",     url=YCLIENTS_URL)],
        [InlineKeyboardButton(text="📞 Связаться с нами", callback_data="contacts")],
    ])

def kb_consent() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Согласен(а)", callback_data="consent_yes"),
            InlineKeyboardButton(text="❌ Не согласен(а)", callback_data="consent_no"),
        ]
    ])

def kb_admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Список клиентов",   callback_data="adm_clients")],
        [InlineKeyboardButton(text="➕ Добавить формулу",  callback_data="adm_add_formula")],
        [InlineKeyboardButton(text="📢 Рассылка",          callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="🗄 Создать бэкап",     callback_data="adm_backup")],
        [InlineKeyboardButton(text="📊 Скачать Excel",     callback_data="adm_excel_menu")],
        [InlineKeyboardButton(text="🌐 Открыть веб-панель", callback_data="adm_webpanel")],
    ])

def kb_excel_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Только клиенты",         callback_data="adm_excel_clients")],
        [InlineKeyboardButton(text="🧪 Только формулы",         callback_data="adm_excel_formulas")],
        [InlineKeyboardButton(text="📦 Всё (клиенты + формулы)", callback_data="adm_excel_full")],
        [InlineKeyboardButton(text="← Назад",                   callback_data="adm_back")],
    ])

def kb_backup_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, создать", callback_data="adm_backup_confirm"),
            InlineKeyboardButton(text="❌ Отмена",      callback_data="adm_backup_cancel"),
        ]
    ])

def kb_phone() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить мой номер", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


# ─── Старт / регистрация ─────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    client = await db.get_client_by_telegram_id(message.from_user.id)
    if client:
        await message.answer(
            f"👋 Добро пожаловать обратно, <b>{client['name']}</b>!\n\n"
            "Что вас интересует?",
            reply_markup=kb_main_menu(),
            parse_mode="HTML",
        )
        return

    await message.answer(
        "👋 Добро пожаловать в <b>LUM'N</b> — студию персонального парфюма!\n\n"
        "Давайте познакомимся. Как вас зовут? (имя и фамилия)",
        parse_mode="HTML",
    )
    await state.set_state(Registration.waiting_name)


@dp.message(Registration.waiting_name)
async def reg_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    if len(name) < 2:
        await message.answer("Пожалуйста, введите имя (минимум 2 символа).")
        return
    await state.update_data(name=name)
    await message.answer(
        f"Приятно познакомиться, <b>{name}</b>! 🌸\n\n"
        "Укажите ваш номер телефона. Можно нажать кнопку ниже или написать вручную в формате <code>+79001234567</code>",
        reply_markup=kb_phone(),
        parse_mode="HTML",
    )
    await state.set_state(Registration.waiting_phone)


@dp.message(Registration.waiting_phone, F.contact)
async def reg_phone_contact(message: Message, state: FSMContext) -> None:
    phone = message.contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone
    await _ask_consent(message, state, phone)


@dp.message(Registration.waiting_phone, F.text)
async def reg_phone_text(message: Message, state: FSMContext) -> None:
    phone = message.text.strip().replace(" ", "").replace("-", "")
    if not (phone.startswith("+") and len(phone) >= 11):
        await message.answer(
            "Номер должен быть в формате <code>+79001234567</code>. Попробуйте ещё раз.",
            parse_mode="HTML",
        )
        return
    await _ask_consent(message, state, phone)


async def _ask_consent(message: Message, state: FSMContext, phone: str) -> None:
    await state.update_data(phone=phone)
    await message.answer(
        "Для завершения регистрации необходимо согласие на обработку персональных данных.\n\n"
        f"📄 <a href='{PRIVACY_URL}'>Политика конфиденциальности</a>\n"
        f"📄 <a href='{OFFER_URL}'>Публичная оферта</a>\n\n"
        "Вы согласны с условиями?",
        reply_markup=kb_consent(),
        parse_mode="HTML",
    )
    await state.set_state(Registration.waiting_consent)


@dp.callback_query(Registration.waiting_consent, F.data == "consent_yes")
async def reg_consent_yes(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    client = await db.create_client(
        telegram_id=callback.from_user.id,
        name=data["name"],
        phone=data["phone"],
    )
    await state.clear()
    await callback.message.edit_text(
        f"✅ <b>Регистрация завершена!</b>\n\n"
        f"👤 <b>Имя:</b> {client['name']}\n"
        f"📱 <b>Телефон:</b> {client['phone']}\n"
        f"🆔 <b>Номер клиента:</b> {client['id']}\n\n"
        "Ваши формулы будут здесь — как только мастер их добавит после сессии. 🌿",
        reply_markup=kb_main_menu(),
        parse_mode="HTML",
    )
    await callback.answer()
    log.info(f"New client registered: id={client['id']} name={client['name']}")


@dp.callback_query(Registration.waiting_consent, F.data == "consent_no")
async def reg_consent_no(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "Без согласия мы не можем сохранить ваши данные. "
        "Если передумаете — просто напишите /start 🌸"
    )
    await callback.answer()


# ─── Главное меню (клиент) ────────────────────────────────────────────────────

@dp.callback_query(F.data == "my_formulas")
async def show_formulas(callback: CallbackQuery) -> None:
    client = await db.get_client_by_telegram_id(callback.from_user.id)
    if not client:
        await callback.answer("Сначала нужно зарегистрироваться. Напишите /start", show_alert=True)
        return

    formulas = await db.get_formulas_by_client(client["id"])
    if not formulas:
        await callback.message.edit_text(
            "🧪 У вас пока нет сохранённых формул.\n\n"
            "После сессии мастер добавит вашу формулу, и она появится здесь. 🌸",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="← Назад", callback_data="main_menu")]
            ]),
        )
        await callback.answer()
        return

    text = f"🧪 <b>Ваши формулы</b> (клиент #{client['id']}):\n\n"
    for f in formulas:
        text += f"<b>#{f['id']} — {f['title']}</b>\n"
        text += f"<i>Добавлена: {f['created_at'][:10]}</i>\n"
        text += f"{f['content']}\n"
        text += "─" * 20 + "\n"

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="← Назад", callback_data="main_menu")]
        ]),
        parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data == "contacts")
async def show_contacts(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "📞 <b>Связаться с нами:</b>\n\n"
        "Instagram: @lumln_studio\n"
        "WhatsApp / Telegram: +7 900 000 00 00\n\n"
        "Будем рады ответить на ваши вопросы! 🌸",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="← Назад", callback_data="main_menu")]
        ]),
        parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data == "main_menu")
async def back_to_main(callback: CallbackQuery) -> None:
    client = await db.get_client_by_telegram_id(callback.from_user.id)
    name = client["name"] if client else "Гость"
    await callback.message.edit_text(
        f"👋 <b>{name}</b>, что вас интересует?",
        reply_markup=kb_main_menu(),
        parse_mode="HTML",
    )
    await callback.answer()


# ─── Админ-панель (через бот) ─────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


@dp.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("❌ Доступ запрещён.")
        return
    await message.answer("🔧 <b>Панель администратора</b>", reply_markup=kb_admin_menu(), parse_mode="HTML")


@dp.callback_query(F.data == "adm_clients")
async def adm_clients(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    clients = await db.get_all_clients()
    if not clients:
        await callback.message.edit_text("Клиентов пока нет.", reply_markup=kb_admin_menu())
        await callback.answer()
        return

    text = f"👥 <b>Клиенты ({len(clients)}):</b>\n\n"
    for c in clients[:20]:
        text += f"#{c['id']} <b>{c['name']}</b> — {c['phone']} — {c['created_at'][:10]}\n"
    if len(clients) > 20:
        text += f"\n...и ещё {len(clients) - 20}. Все клиенты — в веб-панели."

    await callback.message.edit_text(text, reply_markup=kb_admin_menu(), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "adm_add_formula")
async def adm_add_formula_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    await callback.message.answer(
        "➕ <b>Добавление формулы</b>\n\nВведите <b>номер клиента</b> (ID).\n"
        "Посмотреть ID клиента можно в списке клиентов или в веб-панели.",
        parse_mode="HTML",
    )
    await state.set_state(AdminAddFormula.waiting_client_id)
    await callback.answer()


@dp.message(AdminAddFormula.waiting_client_id)
async def adm_formula_client_id(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    try:
        client_id = int(message.text.strip())
    except ValueError:
        await message.answer("Введите числовой ID клиента.")
        return

    client = await db.get_client_by_id(client_id)
    if not client:
        await message.answer(f"Клиент #{client_id} не найден. Проверьте ID.")
        return

    await state.update_data(client_id=client_id)
    await message.answer(
        f"Клиент: <b>{client['name']}</b> ({client['phone']})\n\n"
        "Введите <b>название формулы</b> (например: «Сессия 14.06.2025 — Летний вечер»):",
        parse_mode="HTML",
    )
    await state.set_state(AdminAddFormula.waiting_title)


@dp.message(AdminAddFormula.waiting_title)
async def adm_formula_title(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.update_data(title=message.text.strip())
    await message.answer(
        "Теперь введите <b>состав формулы</b>.\n\n"
        "Можно в любом формате, например:\n"
        "<code>Бергамот — 30%\nЖасмин — 25%\nСандал — 20%\nМускус — 15%\nВаниль — 10%</code>",
        parse_mode="HTML",
    )
    await state.set_state(AdminAddFormula.waiting_content)


@dp.message(AdminAddFormula.waiting_content)
async def adm_formula_content(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return

    data = await state.get_data()
    formula = await db.add_formula(
        client_id=data["client_id"],
        title=data["title"],
        content=message.text.strip(),
        created_by=message.from_user.full_name,
    )
    await state.clear()

    client = await db.get_client_by_id(data["client_id"])

    # Уведомляем клиента
    try:
        await bot.send_message(
            client["telegram_id"],
            f"🌸 <b>Ваша формула готова!</b>\n\n"
            f"<b>{formula['title']}</b>\n\n"
            f"{formula['content']}\n\n"
            "Всегда доступна в разделе «Мои формулы».",
            parse_mode="HTML",
        )
    except Exception:
        pass  # клиент мог заблокировать бота

    await message.answer(
        f"✅ Формула добавлена для клиента <b>{client['name']}</b>.\n"
        "Клиент получил уведомление.",
        reply_markup=kb_admin_menu(),
        parse_mode="HTML",
    )
    log.info(f"Formula added: client_id={data['client_id']} title={data['title']}")


@dp.callback_query(F.data == "adm_broadcast")
async def adm_broadcast_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    clients = await db.get_all_clients()
    await callback.message.answer(
        f"📢 <b>Рассылка</b>\n\nВсего клиентов: {len(clients)}\n\n"
        "Введите текст сообщения (поддерживается разметка <b>жирный</b>, <i>курсив</i>):",
        parse_mode="HTML",
    )
    await state.set_state(AdminBroadcast.waiting_message)
    await callback.answer()


@dp.message(AdminBroadcast.waiting_message)
async def adm_broadcast_send(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return

    text = message.text.strip()
    await state.clear()

    await message.answer("⏳ Отправляю рассылку...")

    ids = await db.get_all_telegram_ids()
    sent = 0
    for tid in ids:
        try:
            await bot.send_message(tid, text, parse_mode="HTML")
            sent += 1
            await asyncio.sleep(0.05)  # rate limit
        except Exception:
            pass

    await db.save_broadcast(text, message.from_user.full_name, sent)
    await message.answer(
        f"✅ Рассылка отправлена: {sent}/{len(ids)} клиентов",
        reply_markup=kb_admin_menu(),
    )
    log.info(f"Broadcast sent: {sent}/{len(ids)}")


@dp.callback_query(F.data == "adm_backup")
async def adm_backup(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    from backup import list_local_backups
    local = list_local_backups()
    last_info = (
        f"Последняя копия: <b>{local[0]['name']}</b> — {local[0]['created']} ({local[0]['size_kb']} KB)"
        if local else "Локальных копий ещё нет."
    )
    await callback.message.edit_text(
        f"🗄 <b>Резервная копия базы данных</b>\n\n"
        f"{last_info}\n\n"
        "Создать бэкап прямо сейчас?\n"
        "<i>Файл сохранится локально на сервере и будет отправлен вам в Telegram.</i>",
        reply_markup=kb_backup_confirm(),
        parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data == "adm_backup_confirm")
async def adm_backup_confirm(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    await callback.message.edit_text("⏳ Создаю резервную копию...")
    await callback.answer()

    from backup import run_backup
    result = await run_backup(initiated_by=f"tg:{callback.from_user.full_name}")

    if result["ok"]:
        tg_line = (
            "📨 Файл отправлен вам в Telegram."
            if result["telegram"]
            else "⚠️ Не удалось отправить в Telegram (проверьте BACKUP_CHAT_ID в .env)."
        )
        text = (
            f"✅ <b>Бэкап создан успешно!</b>\n\n"
            f"📁 Файл: <code>{result['file']}</code>\n"
            f"💾 Размер: {result['size_kb']} KB\n"
            f"{tg_line}"
        )
    else:
        text = f"❌ <b>Ошибка при создании бэкапа:</b>\n<code>{result['error']}</code>"

    await callback.message.edit_text(text, reply_markup=kb_admin_menu(), parse_mode="HTML")


@dp.callback_query(F.data == "adm_backup_cancel")
async def adm_backup_cancel(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "🔧 <b>Панель администратора</b>",
        reply_markup=kb_admin_menu(),
        parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data == "adm_back")
async def adm_back(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await callback.message.edit_text(
        "🔧 <b>Панель администратора</b>",
        reply_markup=kb_admin_menu(),
        parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data == "adm_excel_menu")
async def adm_excel_menu(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await callback.message.edit_text(
        "📊 <b>Экспорт в Excel</b>\n\n"
        "Выберите что скачать — файл придёт прямо в этот чат:",
        reply_markup=kb_excel_menu(),
        parse_mode="HTML",
    )
    await callback.answer()


async def _send_excel(callback: CallbackQuery, builder, caption: str) -> None:
    """Общая логика: строим Excel, отправляем файлом, возвращаем меню."""
    await callback.message.edit_text("⏳ Формирую файл...")
    await callback.answer()
    try:
        from aiogram.types import BufferedInputFile
        data, filename = await builder()
        await callback.message.answer_document(
            document=BufferedInputFile(data, filename=filename),
            caption=caption,
            parse_mode="HTML",
        )
        await callback.message.edit_text(
            "🔧 <b>Панель администратора</b>",
            reply_markup=kb_admin_menu(),
            parse_mode="HTML",
        )
    except Exception as e:
        log.error(f"Excel export error: {e}")
        await callback.message.edit_text(
            f"❌ Ошибка при формировании файла:\n<code>{e}</code>",
            reply_markup=kb_admin_menu(),
            parse_mode="HTML",
        )


@dp.callback_query(F.data == "adm_excel_clients")
async def adm_excel_clients(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    from excel_export import build_excel_clients
    await _send_excel(
        callback,
        build_excel_clients,
        "👥 <b>Клиентская база LUM'N</b>\n"
        f"Дата выгрузки: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
    )


@dp.callback_query(F.data == "adm_excel_formulas")
async def adm_excel_formulas(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    from excel_export import build_excel_formulas
    await _send_excel(
        callback,
        build_excel_formulas,
        "🧪 <b>Все формулы LUM'N</b>\n"
        f"Дата выгрузки: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
    )


@dp.callback_query(F.data == "adm_excel_full")
async def adm_excel_full(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    from excel_export import build_excel_full
    await _send_excel(
        callback,
        build_excel_full,
        "📦 <b>Полная база LUM'N</b> (клиенты + формулы)\n"
        f"Дата выгрузки: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
    )


@dp.callback_query(F.data == "adm_webpanel")
async def adm_webpanel(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    from config import ADMIN_PORT
    await callback.message.answer(
        f"🌐 Веб-панель доступна по адресу:\n"
        f"<code>http://localhost:{ADMIN_PORT}</code>\n\n"
        "Логин: <code>admin</code>\n"
        "Пароль: из файла .env",
        parse_mode="HTML",
    )
    await callback.answer()


async def run_bot() -> None:
    await db.init_db()
    log.info("Bot started")
    await dp.start_polling(bot)
