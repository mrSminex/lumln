import asyncio
import io
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove,
)

import database as db
from config import BOT_TOKEN, ADMIN_IDS, YCLIENTS_URL, PRIVACY_URL, OFFER_URL, PROXY
from tz_utils import now_msk
from file_extract import extract_text, FileExtractionError, MAX_FILE_SIZE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

PER_PAGE = 10  # клиентов на страницу в списке


def _make_bot() -> Bot:
    if PROXY:
        from aiogram.client.session.aiohttp import AiohttpSession
        log.info(f"Using proxy: {PROXY}")
        return Bot(token=BOT_TOKEN, session=AiohttpSession(proxy=PROXY))
    return Bot(token=BOT_TOKEN)


bot = _make_bot()
dp  = Dispatcher(storage=MemoryStorage())


# ══════════════════════════════════════════════════════════════════════════════
# FSM STATES
# ══════════════════════════════════════════════════════════════════════════════

class Registration(StatesGroup):
    waiting_name    = State()
    waiting_phone   = State()
    waiting_consent = State()

class Certificate(StatesGroup):
    waiting_type    = State()
    waiting_persons = State()
    waiting_tariff  = State()

class Reorder(StatesGroup):
    waiting_formula = State()
    waiting_volume  = State()

class ReviewFlow(StatesGroup):
    waiting_feedback = State()   # текстовый отзыв после плохих оценок

class AdminAddFormula(StatesGroup):
    waiting_client_id = State()
    waiting_title     = State()
    waiting_content   = State()

class AdminEditFormula(StatesGroup):
    waiting_client_id   = State()
    waiting_formula_id  = State()
    waiting_new_content = State()

class AdminAddAdmin(StatesGroup):
    waiting_id = State()

class AdminBroadcastAll(StatesGroup):
    waiting_message = State()
    # подтверждение — inline-кнопки, не отдельный State

class AdminIndividualMsg(StatesGroup):
    waiting_client_id = State()
    waiting_message   = State()

class AdminSchedule(StatesGroup):
    waiting_target   = State()
    waiting_message  = State()
    waiting_datetime = State()


# ══════════════════════════════════════════════════════════════════════════════
# KEYBOARDS
# ══════════════════════════════════════════════════════════════════════════════

def kb_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧪 Мои формулы",          callback_data="my_formulas")],
        [InlineKeyboardButton(text="🔁 Повторить мой парфюм", callback_data="reorder")],
        [InlineKeyboardButton(text="🎁 Сертификат в подарок", callback_data="certificate")],
        [InlineKeyboardButton(text="📅 Записаться",            url=YCLIENTS_URL)],
        [InlineKeyboardButton(text="📞 Связаться с нами",      callback_data="contacts")],
        [InlineKeyboardButton(text="⚙️ Настройки",             callback_data="settings")],
    ])

def kb_consent() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Согласен(а)", callback_data="consent_yes"),
        InlineKeyboardButton(text="❌ Отказываюсь", callback_data="consent_no"),
    ]])

def kb_back_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Главное меню", callback_data="main_menu")]
    ])

def kb_settings(unsubscribed: bool) -> InlineKeyboardMarkup:
    unsub_text = "🔔 Подписаться на рассылки" if unsubscribed else "🔕 Отписаться от рассылок"
    unsub_data = "unsub_subscribe" if unsubscribed else "unsub_unsubscribe"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=unsub_text,            callback_data=unsub_data)],
        [InlineKeyboardButton(text="🗑 Удалить мои данные", callback_data="delete_data_ask")],
        [InlineKeyboardButton(text="← Назад",              callback_data="main_menu")],
    ])

def kb_delete_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить",  callback_data="delete_data_confirm")],
        [InlineKeyboardButton(text="❌ Отмена",        callback_data="settings")],
    ])

def kb_admin_delete_confirm(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить удаление", callback_data=f"adm_delete_confirm_{telegram_id}")],
        [InlineKeyboardButton(text="❌ Отклонить",             callback_data=f"adm_delete_reject_{telegram_id}")],
    ])

# ── Сертификат ────────────────────────────────────────────────────────────────

def kb_cert_type() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📧 Электронный", callback_data="cert_type_digital")],
        [InlineKeyboardButton(text="📦 Физический",  callback_data="cert_type_physical")],
        [InlineKeyboardButton(text="← Отмена",       callback_data="main_menu")],
    ])

def kb_cert_persons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1 человек",  callback_data="cert_persons_1"),
            InlineKeyboardButton(text="2 человека", callback_data="cert_persons_2"),
            InlineKeyboardButton(text="3 человека", callback_data="cert_persons_3"),
        ],
        [InlineKeyboardButton(text="← Назад", callback_data="certificate")],
    ])

def kb_cert_tariff() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌿 Стандартная сессия",    callback_data="cert_tariff_standard")],
        [InlineKeyboardButton(text="👤 Индивидуальная сессия", callback_data="cert_tariff_individual")],
        [InlineKeyboardButton(text="👫 Парная сессия",         callback_data="cert_tariff_pair")],
        [InlineKeyboardButton(text="👨‍👩‍👦 Сессия на троих",      callback_data="cert_tariff_triple")],
        [InlineKeyboardButton(text="← Назад",                  callback_data="cert_back_persons")],
    ])

# ── Повтор парфюма ────────────────────────────────────────────────────────────

def kb_volume() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="15 мл",  callback_data="vol_15"),
            InlineKeyboardButton(text="30 мл",  callback_data="vol_30"),
            InlineKeyboardButton(text="50 мл",  callback_data="vol_50"),
            InlineKeyboardButton(text="100 мл", callback_data="vol_100"),
        ],
        [InlineKeyboardButton(text="← Отмена", callback_data="main_menu")],
    ])

def kb_formula_select(formulas: list[dict]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(
            text=f"#{f['id']} — {f['title'][:35]}",
            callback_data=f"reorder_f_{f['id']}"
        )]
        for f in formulas
    ]
    buttons.append([InlineKeyboardButton(text="← Отмена", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def kb_formula_select_edit(formulas: list[dict]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(
            text=f"#{f['id']} — {f['title'][:35]}",
            callback_data=f"edit_f_{f['id']}"
        )]
        for f in formulas
    ]
    buttons.append([InlineKeyboardButton(text="← Отмена", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ── Рейтинг 1–5 ──────────────────────────────────────────────────────────────

def kb_rating(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=str(i), callback_data=f"{prefix}_{i}")
        for i in range(1, 6)
    ]])

# ── Админ ─────────────────────────────────────────────────────────────────────

def kb_admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Все клиенты",            callback_data="adm_clients_p_0")],
        [InlineKeyboardButton(text="➕ Добавить формулу",       callback_data="adm_add_formula")],
        [InlineKeyboardButton(text="✏️ Редактировать формулу",  callback_data="adm_edit_formula")],
        [InlineKeyboardButton(text="📢 Рассылка всем",          callback_data="adm_broadcast_all")],
        [InlineKeyboardButton(text="✉️ Написать клиенту",       callback_data="adm_individual_msg")],
        [InlineKeyboardButton(text="⏰ Отложенное сообщение",   callback_data="adm_schedule")],
        [InlineKeyboardButton(text="🗄 Создать бэкап",          callback_data="adm_backup")],
        [InlineKeyboardButton(text="📊 Скачать Excel",          callback_data="adm_excel_menu")],
        [InlineKeyboardButton(text="👤 Администраторы",         callback_data="adm_admins")],
        [InlineKeyboardButton(text="🌐 Открыть веб-панель",     callback_data="adm_webpanel")],
    ])

def kb_admin_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Меню", callback_data="adm_back")]
    ])

def kb_broadcast_confirm(n: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Отправить всем ({n} чел.)", callback_data="bcast_send_all")],
        [InlineKeyboardButton(text="📤 Тест — отправить себе",      callback_data="bcast_test")],
        [InlineKeyboardButton(text="❌ Отмена",                     callback_data="bcast_cancel")],
    ])

def kb_individual_confirm(client_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить",  callback_data=f"imsg_send_{client_id}")],
        [InlineKeyboardButton(text="❌ Отмена",     callback_data="adm_back")],
    ])

def kb_schedule_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Запланировать", callback_data="sched_confirm")],
        [InlineKeyboardButton(text="❌ Отмена",        callback_data="adm_back")],
    ])

def kb_clients_nav(page: int, total: int) -> InlineKeyboardMarkup:
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="← Назад", callback_data=f"adm_clients_p_{page-1}"))
    if (page + 1) * PER_PAGE < total:
        nav.append(InlineKeyboardButton(text="Далее →", callback_data=f"adm_clients_p_{page+1}"))
    rows = []
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔧 Меню", callback_data="adm_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_backup_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, создать", callback_data="adm_backup_confirm"),
        InlineKeyboardButton(text="❌ Отмена",      callback_data="adm_backup_cancel"),
    ]])

def kb_excel_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Только клиенты",           callback_data="adm_excel_clients")],
        [InlineKeyboardButton(text="🧪 Только формулы",           callback_data="adm_excel_formulas")],
        [InlineKeyboardButton(text="📦 Всё (клиенты + формулы)",  callback_data="adm_excel_full")],
        [InlineKeyboardButton(text="← Назад",                     callback_data="adm_back")],
    ])

def kb_admins_menu(extra_admins: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for a in extra_admins:
        label = a["name"] or str(a["telegram_id"])
        rows.append([InlineKeyboardButton(
            text=f"❌ {label} ({a['telegram_id']})",
            callback_data=f"adm_admin_del_{a['telegram_id']}",
        )])
    rows.append([InlineKeyboardButton(text="➕ Добавить администратора", callback_data="adm_admin_add")])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="adm_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

# Доп. админы, добавленные через бота (помимо ADMIN_IDS из .env).
# Загружаются из БД при старте и обновляются при добавлении/удалении.
EXTRA_ADMIN_IDS: set[int] = set()


async def load_extra_admins() -> None:
    global EXTRA_ADMIN_IDS
    admins = await db.get_extra_admins()
    EXTRA_ADMIN_IDS = {a["telegram_id"] for a in admins}


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS or user_id in EXTRA_ADMIN_IDS


def is_super_admin(user_id: int) -> bool:
    """Только админы из .env (ADMIN_IDS) могут управлять списком администраторов."""
    return user_id in ADMIN_IDS


async def get_text_from_message(message: Message) -> tuple[str | None, str | None]:
    """
    Возвращает (текст, ошибка). Если в сообщении документ — скачивает его
    и извлекает текст (.txt/.docx/.pdf). Если обычный текст — возвращает его.
    Если ни того ни другого, или файл не получилось обработать — (None, сообщение_об_ошибке).
    """
    if message.document:
        doc = message.document
        if doc.file_size and doc.file_size > MAX_FILE_SIZE:
            return None, "Файл слишком большой (максимум 5 МБ). Пришлите файл поменьше или текст вручную."
        try:
            file = await bot.get_file(doc.file_id)
            buf = io.BytesIO()
            await bot.download_file(file.file_path, destination=buf)
            file_bytes = buf.getvalue()
        except Exception:
            return None, "Не удалось скачать файл. Попробуйте ещё раз или пришлите текст вручную."
        try:
            text = extract_text(file_bytes, doc.file_name or "")
        except FileExtractionError as e:
            return None, str(e)
        return text, None

    if message.text:
        text = message.text.strip()
        if not text:
            return None, "Сообщение пустое. Введите состав формулы текстом или пришлите файл."
        return text, None

    return None, "Пришлите состав формулы текстом, либо файлом (.txt, .docx, .pdf)."



TARIFF_NAMES = {
    "standard":   "Стандартная сессия",
    "individual": "Индивидуальная сессия",
    "pair":       "Парная сессия",
    "triple":     "Сессия на троих",
}
CERT_TYPE_NAMES = {"digital": "Электронный", "physical": "Физический"}


# ══════════════════════════════════════════════════════════════════════════════
# РЕГИСТРАЦИЯ
# ══════════════════════════════════════════════════════════════════════════════

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    client = await db.get_client_by_telegram_id(message.from_user.id)
    if client:
        await message.answer(
            f"👋 Добро пожаловать обратно, <b>{client['name']}</b>!\n\nЧто вас интересует?",
            reply_markup=kb_main_menu(), parse_mode="HTML",
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
        "Укажите ваш номер телефона в формате: <code>+79001234567</code>",
        reply_markup=ReplyKeyboardRemove(), parse_mode="HTML",
    )
    await state.set_state(Registration.waiting_phone)


@dp.message(Registration.waiting_phone, F.text)
async def reg_phone_text(message: Message, state: FSMContext) -> None:
    phone = message.text.strip().replace(" ", "").replace("-", "")
    if not (phone.startswith("+") and len(phone) >= 11):
        await message.answer("Формат: <code>+79001234567</code>. Попробуйте ещё раз.", parse_mode="HTML")
        return
    await _ask_consent(message, state, phone)


async def _ask_consent(message: Message, state: FSMContext, phone: str) -> None:
    await state.update_data(phone=phone)
    await message.answer(
        "Для завершения регистрации необходимо согласие на обработку персональных данных.\n\n"
        f"📄 <a href='{PRIVACY_URL}'>Политика конфиденциальности</a>\n"
        f"📄 <a href='{OFFER_URL}'>Публичная оферта</a>\n\n"
        "Вы согласны с условиями?",
        reply_markup=kb_consent(), parse_mode="HTML",
    )
    await state.set_state(Registration.waiting_consent)


@dp.callback_query(Registration.waiting_consent, F.data == "consent_yes")
async def reg_consent_yes(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    client = await db.create_client(
        telegram_id=callback.from_user.id, name=data["name"], phone=data["phone"],
    )
    await state.clear()
    await callback.message.edit_text(
        f"✅ <b>Регистрация завершена!</b>\n\n"
        f"👤 <b>Имя:</b> {client['name']}\n"
        f"📱 <b>Телефон:</b> {client['phone']}\n"
        f"🆔 <b>Номер клиента:</b> {client['id']}\n\n"
        "Ваши формулы появятся здесь после сессии. 🌿",
        reply_markup=kb_main_menu(), parse_mode="HTML",
    )
    await callback.answer()
    log.info(f"New client #{client['id']}: {client['name']}")


@dp.callback_query(Registration.waiting_consent, F.data == "consent_no")
async def reg_consent_no(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "Без согласия мы не можем сохранить ваши данные. Если передумаете — напишите /start 🌸"
    )
    await callback.answer()


# ══════════════════════════════════════════════════════════════════════════════
# КЛИЕНТСКОЕ МЕНЮ
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data == "main_menu")
async def back_to_main(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    client = await db.get_client_by_telegram_id(callback.from_user.id)
    name = client["name"] if client else "Гость"
    await callback.message.edit_text(
        f"👋 <b>{name}</b>, что вас интересует?",
        reply_markup=kb_main_menu(), parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data == "my_formulas")
async def show_formulas(callback: CallbackQuery) -> None:
    client = await db.get_client_by_telegram_id(callback.from_user.id)
    if not client:
        await callback.answer("Сначала нужно зарегистрироваться. Напишите /start", show_alert=True)
        return
    formulas = await db.get_formulas_by_client(client["id"])
    if not formulas:
        await callback.message.edit_text(
            "🧪 У вас пока нет сохранённых формул.\n\nПосле сессии мастер добавит вашу формулу — она появится здесь. 🌸",
            reply_markup=kb_back_main(),
        )
        await callback.answer()
        return
    text = f"🧪 <b>Ваши формулы</b> (клиент #{client['id']}):\n\n"
    for f in formulas:
        text += f"<b>#{f['id']} — {f['title']}</b>\n<i>{f['created_at'][:10]}</i>\n{f['content']}\n{'─'*20}\n"
    await callback.message.edit_text(text, reply_markup=kb_back_main(), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "contacts")
async def show_contacts(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "📞 <b>Связаться с нами:</b>\n\n"
        "Instagram: @lumln_studio\n"
        "WhatsApp / Telegram: +7 900 000 00 00\n\nБудем рады ответить на ваши вопросы! 🌸",
        reply_markup=kb_back_main(), parse_mode="HTML",
    )
    await callback.answer()


# ══════════════════════════════════════════════════════════════════════════════
# СЕРТИФИКАТ В ПОДАРОК
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data == "certificate")
async def cert_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "🎁 <b>Сертификат в подарок</b>\n\n"
        "Выберите формат сертификата:",
        reply_markup=kb_cert_type(), parse_mode="HTML",
    )
    await state.set_state(Certificate.waiting_type)
    await callback.answer()


@dp.callback_query(Certificate.waiting_type, F.data.startswith("cert_type_"))
async def cert_type(callback: CallbackQuery, state: FSMContext) -> None:
    ctype = callback.data.split("cert_type_")[1]
    await state.update_data(cert_type=ctype)
    await callback.message.edit_text(
        f"🎁 Сертификат: <b>{CERT_TYPE_NAMES[ctype]}</b>\n\n"
        "На сколько человек?",
        reply_markup=kb_cert_persons(), parse_mode="HTML",
    )
    await state.set_state(Certificate.waiting_persons)
    await callback.answer()


@dp.callback_query(Certificate.waiting_persons, F.data.startswith("cert_persons_"))
async def cert_persons(callback: CallbackQuery, state: FSMContext) -> None:
    n = int(callback.data.split("cert_persons_")[1])
    await state.update_data(persons=n)
    await callback.message.edit_text(
        f"🎁 Сертификат на <b>{n} чел.</b>\n\n"
        "Выберите формат сессии:",
        reply_markup=kb_cert_tariff(), parse_mode="HTML",
    )
    await state.set_state(Certificate.waiting_tariff)
    await callback.answer()


@dp.callback_query(Certificate.waiting_persons, F.data == "certificate")
async def cert_back_to_type(callback: CallbackQuery, state: FSMContext) -> None:
    await cert_start(callback, state)


@dp.callback_query(Certificate.waiting_tariff, F.data.startswith("cert_tariff_"))
async def cert_tariff(callback: CallbackQuery, state: FSMContext) -> None:
    tariff_key = callback.data.split("cert_tariff_")[1]
    data = await state.get_data()
    await state.clear()

    client = await db.get_client_by_telegram_id(callback.from_user.id)
    name = client["name"] if client else callback.from_user.full_name
    ctype = data.get("cert_type", "digital")
    persons = data.get("persons", 1)

    req_id = await db.save_certificate_request(
        telegram_id=callback.from_user.id,
        client_name=name,
        cert_type=ctype,
        persons=persons,
        tariff=tariff_key,
    )

    # Уведомляем всех админов
    admin_text = (
        f"🎁 <b>Новая заявка на сертификат #{req_id}</b>\n\n"
        f"👤 Клиент: <b>{name}</b>\n"
        f"📱 TG: tg://user?id={callback.from_user.id}\n"
        f"🎫 Тип: {CERT_TYPE_NAMES[ctype]}\n"
        f"👥 Человек: {persons}\n"
        f"🌿 Тариф: {TARIFF_NAMES[tariff_key]}\n"
        f"🕐 Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_text, parse_mode="HTML")
        except Exception:
            pass

    await callback.message.edit_text(
        f"✅ <b>Заявка принята!</b>\n\n"
        f"🎫 Сертификат: {CERT_TYPE_NAMES[ctype]}\n"
        f"👥 На {persons} чел. · {TARIFF_NAMES[tariff_key]}\n\n"
        "Наш менеджер свяжется с вами в ближайшее время и расскажет все детали. 🌸",
        reply_markup=kb_back_main(), parse_mode="HTML",
    )
    await callback.answer()
    log.info(f"Certificate request #{req_id} from {name}")


@dp.callback_query(Certificate.waiting_tariff, F.data == "cert_back_persons")
async def cert_back_persons(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    ctype = data.get("cert_type", "digital")
    await callback.message.edit_text(
        f"🎁 Сертификат: <b>{CERT_TYPE_NAMES[ctype]}</b>\n\nНа сколько человек?",
        reply_markup=kb_cert_persons(), parse_mode="HTML",
    )
    await state.set_state(Certificate.waiting_persons)
    await callback.answer()


# ══════════════════════════════════════════════════════════════════════════════
# ПОВТОР ПАРФЮМА
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data == "reorder")
async def reorder_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    client = await db.get_client_by_telegram_id(callback.from_user.id)
    if not client:
        await callback.answer("Сначала нужно зарегистрироваться. Напишите /start", show_alert=True)
        return

    formulas = await db.get_formulas_by_client(client["id"])
    if not formulas:
        await callback.message.edit_text(
            "🔁 У вас пока нет сохранённых формул.\n\nСначала посетите сессию — и ваша формула появится здесь. 🌸",
            reply_markup=kb_back_main(),
        )
        await callback.answer()
        return

    if len(formulas) == 1:
        f = formulas[0]
        await state.update_data(formula_id=f["id"], formula_title=f["title"])
        await callback.message.edit_text(
            f"🔁 <b>Повтор парфюма</b>\n\n"
            f"Формула: <b>{f['title']}</b>\n\n"
            "Какой объём вы хотите?",
            reply_markup=kb_volume(), parse_mode="HTML",
        )
        await state.set_state(Reorder.waiting_volume)
    else:
        await callback.message.edit_text(
            "🔁 <b>Повтор парфюма</b>\n\nУ вас несколько формул — выберите какую повторить:",
            reply_markup=kb_formula_select(formulas), parse_mode="HTML",
        )
        await state.set_state(Reorder.waiting_formula)
    await callback.answer()


@dp.callback_query(Reorder.waiting_formula, F.data.startswith("reorder_f_"))
async def reorder_formula_select(callback: CallbackQuery, state: FSMContext) -> None:
    formula_id = int(callback.data.split("reorder_f_")[1])
    formula = await db.get_formula_by_id(formula_id)
    if not formula:
        await callback.answer("Формула не найдена", show_alert=True)
        return
    await state.update_data(formula_id=formula_id, formula_title=formula["title"])
    await callback.message.edit_text(
        f"🔁 <b>Повтор парфюма</b>\n\n"
        f"Формула: <b>{formula['title']}</b>\n\nКакой объём вы хотите?",
        reply_markup=kb_volume(), parse_mode="HTML",
    )
    await state.set_state(Reorder.waiting_volume)
    await callback.answer()


@dp.callback_query(Reorder.waiting_volume, F.data.startswith("vol_"))
async def reorder_volume(callback: CallbackQuery, state: FSMContext) -> None:
    volume = int(callback.data.split("vol_")[1])
    data = await state.get_data()
    await state.clear()

    client = await db.get_client_by_telegram_id(callback.from_user.id)
    name = client["name"] if client else callback.from_user.full_name

    req_id = await db.save_reorder_request(
        telegram_id=callback.from_user.id,
        client_name=name,
        formula_id=data.get("formula_id"),
        formula_title=data.get("formula_title", "—"),
        volume=volume,
    )

    admin_text = (
        f"🔁 <b>Заявка на повтор парфюма #{req_id}</b>\n\n"
        f"👤 Клиент: <b>{name}</b>\n"
        f"📱 TG: tg://user?id={callback.from_user.id}\n"
        f"🧪 Формула: {data.get('formula_title', '—')}\n"
        f"💧 Объём: {volume} мл\n"
        f"🕐 Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_text, parse_mode="HTML")
        except Exception:
            pass

    await callback.message.edit_text(
        f"✅ <b>Заявка принята!</b>\n\n"
        f"🧪 Формула: {data.get('formula_title', '—')}\n"
        f"💧 Объём: {volume} мл\n\n"
        "Наш менеджер свяжется с вами и уточнит детали заказа. 🌸",
        reply_markup=kb_back_main(), parse_mode="HTML",
    )
    await callback.answer()
    log.info(f"Reorder request #{req_id} from {name}, {volume}ml")


# ══════════════════════════════════════════════════════════════════════════════
# ОТЗЫВЫ (ответы клиента на запрос, инициируемый фоновой задачей)
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data.regexp(r"^rev_q1_\d+_\d+$"))
async def review_q1(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, fid, score_str = callback.data.split("_")
    formula_id = int(fid)
    q1 = int(score_str)
    await state.update_data(formula_id=formula_id, q1=q1)

    await callback.message.edit_text(
        f"Спасибо! Вы оценили сессию на <b>{q1}/5</b> 🌸\n\n"
        "<b>Вопрос 2:</b> Насколько вам понравился получившийся аромат?\nОцените от 1 до 5 👇",
        reply_markup=kb_rating(f"rev_q2_{formula_id}_{q1}"),
        parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data.regexp(r"^rev_q2_\d+_\d+_\d+$"))
async def review_q2(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split("_")  # rev, q2, fid, q1, q2
    formula_id = int(parts[2])
    q1 = int(parts[3])
    q2 = int(parts[4])

    client = await db.get_client_by_telegram_id(callback.from_user.id)
    client_id = client["id"] if client else 0

    # Счастливый сценарий: сессия и аромат понравились
    if q1 == 5 and q2 >= 4:
        await db.save_review(client_id, formula_id, q1, q2, None)
        await state.clear()
        await callback.message.edit_text(
            f"🌸 <b>Спасибо за ваш отзыв!</b>\n\n"
            f"Мы рады, что сессия прошла на отлично ({q1}/5) и аромат вам понравился ({q2}/5).\n\n"
            "Будем рады видеть вас снова в LUM'N! ✨",
            parse_mode="HTML",
        )
        await callback.answer()
        return

    # Нужна обратная связь
    await state.update_data(formula_id=formula_id, q1=q1, q2=q2, client_id=client_id)
    await state.set_state(ReviewFlow.waiting_feedback)

    if q1 == 5:
        ask = (
            f"Рады, что сессия прошла отлично ({q1}/5)!\n"
            f"Жаль, что аромат не совсем угодил ({q2}/5). "
            "Расскажите, что именно не понравилось в аромате и что стоило бы изменить?"
        )
    else:
        ask = (
            f"Спасибо за честную оценку ({q1}/5 за сессию, {q2}/5 за аромат).\n\n"
            "Что конкретно вам не понравилось и что, на ваш взгляд, стоило бы улучшить?"
        )

    await callback.message.edit_text(
        f"🌸 {ask}\n\n<i>Напишите ответ в свободной форме — нам важно каждое слово.</i>",
        parse_mode="HTML",
    )
    await callback.answer()


@dp.message(ReviewFlow.waiting_feedback)
async def review_feedback(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()

    await db.save_review(
        client_id=data.get("client_id", 0),
        formula_id=data.get("formula_id", 0),
        q1=data.get("q1", 0),
        q2=data.get("q2"),
        feedback=message.text.strip(),
    )

    # Пересылаем отзыв админам
    client = await db.get_client_by_telegram_id(message.from_user.id)
    name = client["name"] if client else message.from_user.full_name
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"📝 <b>Отзыв клиента</b>\n\n"
                f"👤 {name} | Q1={data.get('q1')} Q2={data.get('q2')}\n\n"
                f"💬 {message.text.strip()}",
                parse_mode="HTML",
            )
        except Exception:
            pass

    await message.answer(
        "🌸 <b>Спасибо за ваш честный отзыв!</b>\n\n"
        "Мы обязательно учтём его и будем становиться лучше. "
        "Надеемся снова увидеть вас в LUM'N! ✨",
        reply_markup=kb_main_menu(), parse_mode="HTML",
    )


# ══════════════════════════════════════════════════════════════════════════════
# АДМИН — ГЛАВНАЯ
# ══════════════════════════════════════════════════════════════════════════════

@dp.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("❌ Доступ запрещён.")
        return
    await message.answer("🔧 <b>Панель администратора</b>", reply_markup=kb_admin_menu(), parse_mode="HTML")


@dp.callback_query(F.data == "adm_back")
async def adm_back(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "🔧 <b>Панель администратора</b>", reply_markup=kb_admin_menu(), parse_mode="HTML",
    )
    await callback.answer()


# ══════════════════════════════════════════════════════════════════════════════
# АДМИН — КЛИЕНТЫ (постраничный список)
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data.startswith("adm_clients_p_"))
async def adm_clients_page(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    page = int(callback.data.split("adm_clients_p_")[1])
    clients = await db.get_all_clients()
    total = len(clients)
    start = page * PER_PAGE
    chunk = clients[start: start + PER_PAGE]

    if not chunk:
        await callback.message.edit_text("Клиентов пока нет.", reply_markup=kb_admin_back())
        await callback.answer()
        return

    text = f"👥 <b>Клиенты</b> — стр. {page+1}/{(total-1)//PER_PAGE+1} (всего {total}):\n\n"
    for c in chunk:
        text += f"#{c['id']} <b>{c['name']}</b> — {c['phone']} — {c['created_at'][:10]}\n"

    await callback.message.edit_text(
        text, reply_markup=kb_clients_nav(page, total), parse_mode="HTML",
    )
    await callback.answer()


# ══════════════════════════════════════════════════════════════════════════════
# АДМИН — ДОБАВИТЬ ФОРМУЛУ
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data == "adm_add_formula")
async def adm_add_formula_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await callback.message.answer(
        "➕ <b>Добавление формулы</b>\n\nВведите <b>ID клиента</b>:",
        parse_mode="HTML",
    )
    await state.set_state(AdminAddFormula.waiting_client_id)
    await callback.answer()


@dp.message(AdminAddFormula.waiting_client_id)
async def adm_formula_client_id(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id): return
    try:
        cid = int(message.text.strip())
    except ValueError:
        await message.answer("Введите числовой ID.")
        return
    client = await db.get_client_by_id(cid)
    if not client:
        await message.answer(f"Клиент #{cid} не найден.")
        return
    await state.update_data(client_id=cid)
    await message.answer(
        f"Клиент: <b>{client['name']}</b> ({client['phone']})\n\nВведите <b>название формулы</b>:",
        parse_mode="HTML",
    )
    await state.set_state(AdminAddFormula.waiting_title)


@dp.message(AdminAddFormula.waiting_title)
async def adm_formula_title(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id): return
    await state.update_data(title=message.text.strip())
    await message.answer(
        "Введите <b>состав формулы</b> текстом:\n\n"
        "<code>Бергамот — 30%\nЖасмин — 25%\nСандал — 20%\nМускус — 15%\nВаниль — 10%</code>\n\n"
        "Либо пришлите файл с составом — <b>.txt, .docx или .pdf</b>.",
        parse_mode="HTML",
    )
    await state.set_state(AdminAddFormula.waiting_content)


@dp.message(AdminAddFormula.waiting_content, F.text | F.document)
async def adm_formula_content(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id): return

    content, error = await get_text_from_message(message)
    if error:
        await message.answer(f"⚠️ {error}")
        return

    data = await state.get_data()
    formula = await db.add_formula(
        client_id=data["client_id"], title=data["title"],
        content=content, created_by=message.from_user.full_name,
    )
    await state.clear()
    client = await db.get_client_by_id(data["client_id"])
    try:
        await bot.send_message(
            client["telegram_id"],
            f"🌸 <b>Ваша формула готова!</b>\n\n<b>{formula['title']}</b>\n\n{formula['content']}\n\n"
            "Всегда доступна в разделе «Мои формулы».",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await message.answer(
        f"✅ Формула добавлена клиенту <b>{client['name']}</b>. Уведомление отправлено.",
        reply_markup=kb_admin_menu(), parse_mode="HTML",
    )
    log.info(f"Formula added: client={data['client_id']} title={data['title']}")


# ══════════════════════════════════════════════════════════════════════════════
# АДМИН — РЕДАКТИРОВАТЬ ФОРМУЛУ
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data == "adm_edit_formula")
async def adm_edit_formula_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await callback.message.answer(
        "✏️ <b>Редактирование формулы</b>\n\nВведите <b>ID клиента</b>:",
        parse_mode="HTML",
    )
    await state.set_state(AdminEditFormula.waiting_client_id)
    await callback.answer()


@dp.message(AdminEditFormula.waiting_client_id)
async def adm_edit_formula_client_id(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id): return
    try:
        cid = int(message.text.strip())
    except ValueError:
        await message.answer("Введите числовой ID клиента.")
        return
    client = await db.get_client_by_id(cid)
    if not client:
        await message.answer(f"Клиент #{cid} не найден.")
        return
    formulas = await db.get_formulas_by_client(cid)
    if not formulas:
        await message.answer(
            f"У клиента <b>{client['name']}</b> (#{cid}) пока нет формул.",
            reply_markup=kb_admin_menu(), parse_mode="HTML",
        )
        await state.clear()
        return
    await state.update_data(client_id=cid)
    text = f"Клиент: <b>{client['name']}</b> ({client['phone']})\n\n<b>Формулы клиента:</b>\n"
    for f in formulas:
        text += f"#{f['id']} — {f['title']}\n"
    text += "\nВыберите формулу для редактирования:"
    await message.answer(text, reply_markup=kb_formula_select_edit(formulas), parse_mode="HTML")
    await state.set_state(AdminEditFormula.waiting_formula_id)


@dp.callback_query(AdminEditFormula.waiting_formula_id, F.data.startswith("edit_f_"))
async def adm_edit_formula_select(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    fid = int(callback.data.split("edit_f_")[1])
    formula = await db.get_formula_by_id(fid)
    data = await state.get_data()
    if not formula or formula["client_id"] != data.get("client_id"):
        await callback.answer("Формула не найдена.", show_alert=True)
        return
    await state.update_data(formula_id=fid)
    await callback.message.answer(
        f"✏️ Формула <b>#{fid}</b>\n"
        f"Название: <i>{formula['title']}</i>\n\n"
        f"<b>Текущий состав:</b>\n<code>{formula['content']}</code>\n\n"
        "Введите <b>новый состав</b> текстом (название формулы не меняется), "
        "либо пришлите файл — <b>.txt, .docx или .pdf</b>.",
        parse_mode="HTML",
    )
    await state.set_state(AdminEditFormula.waiting_new_content)
    await callback.answer()


@dp.message(AdminEditFormula.waiting_new_content, F.text | F.document)
async def adm_edit_formula_save(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id): return

    content, error = await get_text_from_message(message)
    if error:
        await message.answer(f"⚠️ {error}")
        return

    data = await state.get_data()
    await state.clear()
    await db.update_formula_content(data["formula_id"], content)

    client = await db.get_client_by_id(data["client_id"])
    formula = await db.get_formula_by_id(data["formula_id"])
    try:
        await bot.send_message(
            client["telegram_id"],
            f"📝 <b>Ваша формула была обновлена</b>\n\n"
            f"<b>{formula['title']}</b>\n\n{formula['content']}",
            parse_mode="HTML",
        )
    except Exception:
        pass

    await message.answer(
        f"✅ Формула #{data['formula_id']} обновлена. Клиент уведомлён.",
        reply_markup=kb_admin_menu(),
    )
    log.info(f"Formula #{data['formula_id']} edited by {message.from_user.full_name}")


# ══════════════════════════════════════════════════════════════════════════════
# АДМИН — РАССЫЛКА ВСЕМ (с подтверждением и тестом)
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data == "adm_broadcast_all")
async def adm_broadcast_all_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    clients = await db.get_all_clients()
    await callback.message.answer(
        f"📢 <b>Рассылка всем клиентам</b>\n\nВсего клиентов: <b>{len(clients)}</b>\n\n"
        "Введите текст сообщения (поддерживается HTML: <code>&lt;b&gt;</code>, <code>&lt;i&gt;</code>):",
        parse_mode="HTML",
    )
    await state.set_state(AdminBroadcastAll.waiting_message)
    await callback.answer()


@dp.message(AdminBroadcastAll.waiting_message)
async def adm_broadcast_preview(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id): return
    text = message.text.strip()
    await state.update_data(message=text)
    clients = await db.get_all_clients()
    await message.answer(
        f"📢 <b>Предпросмотр рассылки:</b>\n\n"
        f"{'─'*25}\n{text}\n{'─'*25}\n\n"
        f"Что делаем?",
        reply_markup=kb_broadcast_confirm(len(clients)),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "bcast_send_all")
async def bcast_send_all(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    data = await state.get_data()
    text = data.get("message", "")
    await state.clear()
    await callback.message.edit_text("⏳ Отправляю рассылку...")
    ids = await db.get_all_telegram_ids(marketing=True)
    sent = 0
    for tid in ids:
        for attempt in range(2):
            try:
                await bot.send_message(tid, text, parse_mode="HTML")
                sent += 1
                break
            except TelegramRetryAfter as e:
                # Telegram попросил подождать — ждём и пробуем ещё раз
                await asyncio.sleep(e.retry_after + 0.5)
            except Exception:
                break
        await asyncio.sleep(0.05)
    await db.save_broadcast(text, callback.from_user.full_name, sent)
    await callback.message.edit_text(
        f"✅ Рассылка отправлена: {sent}/{len(ids)} клиентов",
        reply_markup=kb_admin_menu(),
    )
    log.info(f"Broadcast sent: {sent}/{len(ids)}")
    await callback.answer()


@dp.callback_query(F.data == "bcast_test")
async def bcast_test(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    data = await state.get_data()
    text = data.get("message", "")
    try:
        await bot.send_message(callback.from_user.id, text, parse_mode="HTML")
        await callback.answer("✅ Тестовое сообщение отправлено вам", show_alert=True)
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)


@dp.callback_query(F.data == "bcast_cancel")
async def bcast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "❌ Рассылка отменена.", reply_markup=kb_admin_menu(),
    )
    await callback.answer()


# ══════════════════════════════════════════════════════════════════════════════
# АДМИН — НАПИСАТЬ КОНКРЕТНОМУ КЛИЕНТУ
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data == "adm_individual_msg")
async def adm_individual_msg_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await callback.message.answer(
        "✉️ <b>Написать клиенту</b>\n\nВведите <b>ID клиента</b> (число):",
        parse_mode="HTML",
    )
    await state.set_state(AdminIndividualMsg.waiting_client_id)
    await callback.answer()


@dp.message(AdminIndividualMsg.waiting_client_id)
async def adm_individual_msg_client(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id): return
    try:
        cid = int(message.text.strip())
    except ValueError:
        await message.answer("Введите числовой ID клиента.")
        return
    client = await db.get_client_by_id(cid)
    if not client:
        await message.answer(f"Клиент #{cid} не найден.")
        return
    await state.update_data(client_id=cid)
    await message.answer(
        f"✉️ Получатель: <b>{client['name']}</b> ({client['phone']})\n\nВведите текст сообщения:",
        parse_mode="HTML",
    )
    await state.set_state(AdminIndividualMsg.waiting_message)


@dp.message(AdminIndividualMsg.waiting_message)
async def adm_individual_msg_preview(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id): return
    text = message.text.strip()
    data = await state.get_data()
    cid = data["client_id"]
    client = await db.get_client_by_id(cid)
    await state.update_data(message=text)
    await message.answer(
        f"✉️ Кому: <b>{client['name']}</b>\n\n"
        f"{'─'*25}\n{text}\n{'─'*25}\n\nОтправить?",
        reply_markup=kb_individual_confirm(cid),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("imsg_send_"))
async def adm_individual_msg_send(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    cid = int(callback.data.split("imsg_send_")[1])
    data = await state.get_data()
    text = data.get("message", "")
    await state.clear()

    client = await db.get_client_by_id(cid)
    try:
        await bot.send_message(client["telegram_id"], text, parse_mode="HTML")
        await callback.message.edit_text(
            f"✅ Сообщение отправлено клиенту <b>{client['name']}</b>.",
            reply_markup=kb_admin_menu(), parse_mode="HTML",
        )
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Не удалось отправить: {e}", reply_markup=kb_admin_menu(),
        )
    await callback.answer()


# ══════════════════════════════════════════════════════════════════════════════
# АДМИН — ОТЛОЖЕННЫЕ СООБЩЕНИЯ
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data == "adm_schedule")
async def adm_schedule_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    pending = await db.get_scheduled_messages_pending_list()
    pending_info = ""
    if pending:
        pending_info = f"\n\n📋 <b>Запланировано ({len(pending)}):</b>\n"
        for m in pending[:5]:
            tid = m["telegram_id"]
            pending_info += f"• {m['send_at'][:16]} → tg_id {tid}: {m['message'][:30]}…\n"

    await callback.message.answer(
        f"⏰ <b>Отложенное сообщение</b>{pending_info}\n\n"
        "Введите <b>ID клиента</b> или напишите <code>все</code> для рассылки всем:",
        parse_mode="HTML",
    )
    await state.set_state(AdminSchedule.waiting_target)
    await callback.answer()


@dp.message(AdminSchedule.waiting_target)
async def adm_schedule_target(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id): return
    raw = message.text.strip().lower()
    if raw in ("все", "all", "всем"):
        await state.update_data(target="all", target_label="Все клиенты")
        await message.answer("Введите <b>текст сообщения</b>:", parse_mode="HTML")
        await state.set_state(AdminSchedule.waiting_message)
        return
    try:
        cid = int(raw)
    except ValueError:
        await message.answer("Введите числовой ID или слово «все».")
        return
    client = await db.get_client_by_id(cid)
    if not client:
        await message.answer(f"Клиент #{cid} не найден.")
        return
    await state.update_data(target=str(cid), target_label=f"{client['name']} ({client['phone']})")
    await message.answer(
        f"Получатель: <b>{client['name']}</b>\n\nВведите <b>текст сообщения</b>:",
        parse_mode="HTML",
    )
    await state.set_state(AdminSchedule.waiting_message)


@dp.message(AdminSchedule.waiting_message)
async def adm_schedule_message(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id): return
    await state.update_data(message=message.text.strip())
    await message.answer(
        "Введите <b>дату и время отправки по московскому времени (МСК)</b> в формате:\n"
        "<code>ДД.ММ.ГГГГ ЧЧ:ММ</code>\n\n"
        f"Например: <code>{now_msk().strftime('%d.%m.%Y')} 14:00</code>",
        parse_mode="HTML",
    )
    await state.set_state(AdminSchedule.waiting_datetime)


@dp.message(AdminSchedule.waiting_datetime)
async def adm_schedule_datetime(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id): return
    raw = message.text.strip()
    try:
        send_dt = datetime.strptime(raw, "%d.%m.%Y %H:%M")
    except ValueError:
        await message.answer("Неверный формат. Введите: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>", parse_mode="HTML")
        return
    if send_dt <= now_msk():
        await message.answer("Время уже прошло (по МСК). Введите дату в будущем.")
        return

    await state.update_data(send_at=send_dt.strftime("%Y-%m-%d %H:%M"))
    data = await state.get_data()
    await message.answer(
        f"⏰ <b>Подтвердите отложенное сообщение:</b>\n\n"
        f"👤 Кому: <b>{data['target_label']}</b>\n"
        f"📅 Когда: <b>{send_dt.strftime('%d.%m.%Y в %H:%M')} (МСК)</b>\n\n"
        f"{'─'*25}\n{data['message']}\n{'─'*25}",
        reply_markup=kb_schedule_confirm(),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "sched_confirm")
async def adm_schedule_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    data = await state.get_data()
    await state.clear()

    target = data["target"]
    msg_text = data["message"]
    send_at = data["send_at"]

    if target == "all":
        ids = await db.get_all_telegram_ids(marketing=True)
    else:
        client = await db.get_client_by_id(int(target))
        ids = [client["telegram_id"]] if client else []

    for tid in ids:
        await db.create_scheduled_message(tid, msg_text, send_at)

    await callback.message.edit_text(
        f"✅ Запланировано для {len(ids)} получателей на <b>{send_at[:16].replace('-', '.').replace('T', ' ')}</b>",
        reply_markup=kb_admin_menu(), parse_mode="HTML",
    )
    await callback.answer()
    log.info(f"Scheduled {len(ids)} messages at {send_at}")


# ══════════════════════════════════════════════════════════════════════════════
# АДМИН — БЭКАП
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data == "adm_backup")
async def adm_backup(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    from backup import list_local_backups
    local = list_local_backups()
    last = (
        f"Последняя копия: <b>{local[0]['name']}</b> — {local[0]['created']} ({local[0]['size_kb']} KB)"
        if local else "Локальных копий ещё нет."
    )
    await callback.message.edit_text(
        f"🗄 <b>Резервная копия базы данных</b>\n\n{last}\n\nСоздать бэкап прямо сейчас?",
        reply_markup=kb_backup_confirm(), parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data == "adm_backup_confirm")
async def adm_backup_confirm_handler(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await callback.message.edit_text("⏳ Создаю резервную копию...")
    await callback.answer()
    from backup import run_backup
    result = await run_backup(initiated_by=f"tg:{callback.from_user.full_name}")
    if result["ok"]:
        tg_line = "📨 Файл отправлен в Telegram." if result["telegram"] else "⚠️ Telegram-отправка не настроена."
        text = (
            f"✅ <b>Бэкап создан!</b>\n\n"
            f"📁 <code>{result['file']}</code>\n"
            f"💾 {result['size_kb']} KB\n{tg_line}"
        )
    else:
        text = f"❌ Ошибка: <code>{result['error']}</code>"
    await callback.message.edit_text(text, reply_markup=kb_admin_menu(), parse_mode="HTML")


@dp.callback_query(F.data == "adm_backup_cancel")
async def adm_backup_cancel_handler(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "🔧 <b>Панель администратора</b>", reply_markup=kb_admin_menu(), parse_mode="HTML",
    )
    await callback.answer()


# ══════════════════════════════════════════════════════════════════════════════
# АДМИН — EXCEL
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data == "adm_excel_menu")
async def adm_excel_menu_handler(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await callback.message.edit_text(
        "📊 <b>Экспорт в Excel</b>\n\nВыберите что скачать — файл придёт в этот чат:",
        reply_markup=kb_excel_menu(), parse_mode="HTML",
    )
    await callback.answer()


async def _send_excel(callback: CallbackQuery, builder, caption: str) -> None:
    await callback.message.edit_text("⏳ Формирую файл...")
    await callback.answer()
    try:
        from aiogram.types import BufferedInputFile
        data, filename = await builder()
        await callback.message.answer_document(
            document=BufferedInputFile(data, filename=filename),
            caption=caption, parse_mode="HTML",
        )
        await callback.message.edit_text(
            "🔧 <b>Панель администратора</b>", reply_markup=kb_admin_menu(), parse_mode="HTML",
        )
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Ошибка: <code>{e}</code>", reply_markup=kb_admin_menu(), parse_mode="HTML",
        )


@dp.callback_query(F.data == "adm_excel_clients")
async def adm_excel_clients(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True); return
    from excel_export import build_excel_clients
    await _send_excel(callback, build_excel_clients,
                      f"👥 <b>Клиентская база LUM'N</b>\n{datetime.now().strftime('%d.%m.%Y %H:%M')}")


@dp.callback_query(F.data == "adm_excel_formulas")
async def adm_excel_formulas(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True); return
    from excel_export import build_excel_formulas
    await _send_excel(callback, build_excel_formulas,
                      f"🧪 <b>Все формулы LUM'N</b>\n{datetime.now().strftime('%d.%m.%Y %H:%M')}")


@dp.callback_query(F.data == "adm_excel_full")
async def adm_excel_full(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True); return
    from excel_export import build_excel_full
    await _send_excel(callback, build_excel_full,
                      f"📦 <b>Полная база LUM'N</b>\n{datetime.now().strftime('%d.%m.%Y %H:%M')}")


# ══════════════════════════════════════════════════════════════════════════════
# АДМИН — УПРАВЛЕНИЕ АДМИНИСТРАТОРАМИ
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data == "adm_admins")
async def adm_admins_list(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    extra_admins = await db.get_extra_admins()

    text = "👤 <b>Администраторы</b>\n\n<b>Из конфигурации (.env):</b>\n"
    for aid in ADMIN_IDS:
        text += f"• <code>{aid}</code>\n"

    if extra_admins:
        text += "\n<b>Добавлены через бота:</b>\n"
        for a in extra_admins:
            name = a["name"] or "—"
            text += f"• <code>{a['telegram_id']}</code> ({name})\n"
    else:
        text += "\n<i>Добавленных через бота администраторов пока нет.</i>"

    if not is_super_admin(callback.from_user.id):
        text += "\n\n<i>Добавлять/удалять администраторов могут только главные администраторы (из .env).</i>"
        await callback.message.answer(text, reply_markup=kb_admin_back(), parse_mode="HTML")
    else:
        await callback.message.answer(text, reply_markup=kb_admins_menu(extra_admins), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "adm_admin_add")
async def adm_admin_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_super_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await callback.message.answer(
        "➕ <b>Добавление администратора</b>\n\n"
        "Перешлите сообщение от нужного пользователя или введите его <b>Telegram ID</b> "
        "(числом).\n\n<i>Узнать свой ID можно, например, у бота @userinfobot.</i>",
        parse_mode="HTML",
    )
    await state.set_state(AdminAddAdmin.waiting_id)
    await callback.answer()


@dp.message(AdminAddAdmin.waiting_id)
async def adm_admin_add_save(message: Message, state: FSMContext) -> None:
    if not is_super_admin(message.from_user.id):
        await state.clear()
        return

    new_id: int | None = None
    name = ""

    if message.forward_from:
        new_id = message.forward_from.id
        name = message.forward_from.full_name
    else:
        try:
            new_id = int(message.text.strip())
        except (ValueError, AttributeError):
            await message.answer("Введите числовой Telegram ID или перешлите сообщение от пользователя.")
            return

    if new_id in ADMIN_IDS or new_id in EXTRA_ADMIN_IDS:
        await message.answer(
            f"Пользователь <code>{new_id}</code> уже является администратором.",
            reply_markup=kb_admin_menu(), parse_mode="HTML",
        )
        await state.clear()
        return

    await db.add_admin(new_id, name, message.from_user.id)
    EXTRA_ADMIN_IDS.add(new_id)
    await state.clear()

    await message.answer(
        f"✅ Пользователь <code>{new_id}</code> добавлен в администраторы.",
        reply_markup=kb_admin_menu(), parse_mode="HTML",
    )
    try:
        await bot.send_message(
            new_id,
            "🔧 Вам выдан доступ администратора в боте LUM'N.\nКоманда: /admin",
        )
    except Exception:
        pass
    log.info(f"Admin added: {new_id} by {message.from_user.id}")


@dp.callback_query(F.data.startswith("adm_admin_del_"))
async def adm_admin_remove(callback: CallbackQuery) -> None:
    if not is_super_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    del_id = int(callback.data.split("adm_admin_del_")[1])
    await db.remove_admin(del_id)
    EXTRA_ADMIN_IDS.discard(del_id)
    await callback.answer("Администратор удалён")

    extra_admins = await db.get_extra_admins()
    text = "👤 <b>Администраторы</b>\n\n<b>Из конфигурации (.env):</b>\n"
    for aid in ADMIN_IDS:
        text += f"• <code>{aid}</code>\n"
    if extra_admins:
        text += "\n<b>Добавлены через бота:</b>\n"
        for a in extra_admins:
            name = a["name"] or "—"
            text += f"• <code>{a['telegram_id']}</code> ({name})\n"
    else:
        text += "\n<i>Добавленных через бота администраторов пока нет.</i>"
    await callback.message.edit_text(text, reply_markup=kb_admins_menu(extra_admins), parse_mode="HTML")


# ══════════════════════════════════════════════════════════════════════════════
# КЛИЕНТ — НАСТРОЙКИ / ОТПИСКА / УДАЛЕНИЕ ДАННЫХ
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data == "settings")
async def settings_page(callback: CallbackQuery) -> None:
    client = await db.get_client_by_telegram_id(callback.from_user.id)
    if not client:
        await callback.answer("Сначала нужно зарегистрироваться. Напишите /start", show_alert=True)
        return
    unsub = bool(client.get("unsubscribed", 0))
    status = "🔕 Вы отписаны от маркетинговых рассылок." if unsub else "🔔 Вы подписаны на рассылки."
    await callback.message.edit_text(
        f"⚙️ <b>Настройки</b>\n\n{status}\n\n"
        "<i>Сервисные уведомления (готовность формулы, ответы на заявки) приходят всегда.</i>",
        reply_markup=kb_settings(unsub), parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data == "unsub_unsubscribe")
async def unsub_do(callback: CallbackQuery) -> None:
    client = await db.get_client_by_telegram_id(callback.from_user.id)
    if not client:
        await callback.answer("Вы не зарегистрированы.", show_alert=True)
        return
    await db.set_unsubscribed(callback.from_user.id, True)
    await callback.message.edit_text(
        "🔕 <b>Вы отписаны от маркетинговых рассылок.</b>\n\n"
        "Сервисные уведомления (готовность формулы, ответы на заявки) будут приходить по-прежнему.\n\n"
        "Вы всегда можете подписаться снова в разделе <b>Настройки</b>.",
        reply_markup=kb_settings(True), parse_mode="HTML",
    )
    await callback.answer("Вы отписались от рассылок")
    log.info(f"Unsubscribed: tg_id={callback.from_user.id}")


@dp.callback_query(F.data == "unsub_subscribe")
async def unsub_resubscribe(callback: CallbackQuery) -> None:
    client = await db.get_client_by_telegram_id(callback.from_user.id)
    if not client:
        await callback.answer("Вы не зарегистрированы.", show_alert=True)
        return
    await db.set_unsubscribed(callback.from_user.id, False)
    await callback.message.edit_text(
        "🔔 <b>Вы снова подписаны на рассылки.</b>\n\n"
        "Теперь вы будете получать новости и специальные предложения от LUM'N.",
        reply_markup=kb_settings(False), parse_mode="HTML",
    )
    await callback.answer("Вы подписались на рассылки")
    log.info(f"Resubscribed: tg_id={callback.from_user.id}")


@dp.callback_query(F.data == "delete_data_ask")
async def delete_data_ask(callback: CallbackQuery) -> None:
    client = await db.get_client_by_telegram_id(callback.from_user.id)
    if not client:
        await callback.answer("Вы не зарегистрированы.", show_alert=True)
        return
    await callback.message.edit_text(
        "🗑 <b>Удаление данных</b>\n\n"
        "Вы запрашиваете удаление вашей карточки клиента, включая:\n"
        "• имя и номер телефона\n"
        "• все ваши персональные формулы\n"
        "• историю заявок\n\n"
        "Запрос будет отправлен администратору. После подтверждения все данные будут безвозвратно удалены.\n\n"
        "<b>Вы уверены?</b>",
        reply_markup=kb_delete_confirm(), parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data == "delete_data_confirm")
async def delete_data_confirm(callback: CallbackQuery) -> None:
    client = await db.get_client_by_telegram_id(callback.from_user.id)
    if not client:
        await callback.answer("Вы не зарегистрированы.", show_alert=True)
        return
    await db.set_delete_requested(callback.from_user.id, True)

    # Уведомляем всех главных администраторов
    for admin_id in list(ADMIN_IDS) + list(EXTRA_ADMIN_IDS):
        try:
            await bot.send_message(
                admin_id,
                f"🗑 <b>Запрос на удаление данных</b>\n\n"
                f"Клиент <b>{client['name']}</b> (#{client['id']}) запросил удаление своей карточки.\n"
                f"📱 Телефон: {client['phone']}\n"
                f"🆔 Telegram: <code>{callback.from_user.id}</code>",
                reply_markup=kb_admin_delete_confirm(callback.from_user.id),
                parse_mode="HTML",
            )
        except Exception:
            pass

    await callback.message.edit_text(
        "✅ <b>Запрос отправлен</b>\n\n"
        "Ваш запрос на удаление данных передан администратору. "
        "После подтверждения все ваши данные будут удалены.",
        reply_markup=kb_back_main(), parse_mode="HTML",
    )
    await callback.answer()
    log.info(f"Delete requested: tg_id={callback.from_user.id} client_id={client['id']}")


@dp.callback_query(F.data.startswith("adm_delete_confirm_"))
async def adm_delete_confirm(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    tg_id = int(callback.data.split("adm_delete_confirm_")[1])
    client = await db.get_client_by_telegram_id(tg_id)
    if not client:
        await callback.message.edit_text("Клиент уже удалён или не найден.")
        await callback.answer()
        return
    name, cid = client["name"], client["id"]
    await db.delete_client_by_telegram_id(tg_id)
    await callback.message.edit_text(
        f"✅ Данные клиента <b>{name}</b> (#{cid}) удалены.",
        parse_mode="HTML",
    )
    await callback.answer("Данные удалены")
    log.info(f"Client deleted by admin: tg_id={tg_id} client_id={cid} by={callback.from_user.id}")
    try:
        await bot.send_message(
            tg_id,
            "🗑 Ваши данные удалены из системы LUM'N по вашему запросу.\n\n"
            "Если вы захотите снова воспользоваться нашими услугами — напишите /start.",
        )
    except Exception:
        pass


@dp.callback_query(F.data.startswith("adm_delete_reject_"))
async def adm_delete_reject(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    tg_id = int(callback.data.split("adm_delete_reject_")[1])
    await db.set_delete_requested(tg_id, False)
    client = await db.get_client_by_telegram_id(tg_id)
    name = client["name"] if client else str(tg_id)
    await callback.message.edit_text(
        f"❌ Запрос на удаление данных клиента <b>{name}</b> отклонён.",
        parse_mode="HTML",
    )
    await callback.answer("Запрос отклонён")
    try:
        await bot.send_message(
            tg_id,
            "❌ Ваш запрос на удаление данных был отклонён администратором.\n\n"
            "Если у вас есть вопросы — напишите нам.",
        )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# АДМИН — ВЕБ-ПАНЕЛЬ
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data == "adm_webpanel")
async def adm_webpanel(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    from config import ADMIN_PORT
    await callback.message.answer(
        f"🌐 Веб-панель: <code>http://localhost:{ADMIN_PORT}</code>\n\nПароль: из файла .env",
        parse_mode="HTML",
    )
    await callback.answer()


# ══════════════════════════════════════════════════════════════════════════════
# ЗАПУСК
# ══════════════════════════════════════════════════════════════════════════════

async def run_bot() -> None:
    await db.init_db()
    await load_extra_admins()
    log.info("Bot started")
    await dp.start_polling(bot)