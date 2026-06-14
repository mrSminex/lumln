"""
Фоновые задачи, запускаемые параллельно с ботом:
  - scheduled_messages_sender  — отправка отложенных сообщений
  - review_requester           — запрос отзывов через 24ч после формулы
"""

import asyncio
import logging
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

log = logging.getLogger(__name__)


async def scheduled_messages_sender() -> None:
    """Каждую минуту проверяет таблицу scheduled_messages и отправляет готовые."""
    log.info("Scheduled messages sender started")
    while True:
        await asyncio.sleep(60)
        try:
            import database as db
            from bot import bot
            messages = await db.get_pending_scheduled_messages()
            for msg in messages:
                try:
                    try:
                        await bot.send_message(msg["telegram_id"], msg["message"], parse_mode="HTML")
                    except TelegramRetryAfter as e:
                        await asyncio.sleep(e.retry_after + 0.5)
                        await bot.send_message(msg["telegram_id"], msg["message"], parse_mode="HTML")
                    await db.mark_scheduled_sent(msg["id"])
                    log.info(f"Scheduled msg #{msg['id']} sent to tg_id={msg['telegram_id']}")
                except Exception as e:
                    log.error(f"Scheduled msg #{msg['id']} failed: {e}")
        except Exception as e:
            log.error(f"scheduled_messages_sender loop error: {e}")


async def review_requester() -> None:
    """
    Каждые 30 минут ищет формулы, добавленные 23–25 часов назад,
    по которым ещё не отправлялся запрос отзыва, и отправляет его.
    """
    log.info("Review requester started")
    while True:
        await asyncio.sleep(1800)  # 30 минут
        try:
            import database as db
            from bot import bot
            formulas = await db.get_formulas_needing_review()
            for f in formulas:
                try:
                    client = await db.get_client_by_id(f["client_id"])
                    if not client:
                        continue

                    kb = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(
                            text=str(i),
                            callback_data=f"rev_q1_{f['id']}_{i}"
                        )
                        for i in range(1, 6)
                    ]])

                    text = (
                        f"🌸 <b>{client['name']}</b>, вы были у нас вчера на парфюмерной сессии в LUM'N!\n\n"
                        "Нам очень важно продолжать становиться лучше, и мы хотели бы попросить вас "
                        "оставить честный отзыв — всего 2 вопроса.\n\n"
                        "<b>Вопрос 1:</b> Как прошла сессия и в целом как вам такой опыт?\n"
                        "Оцените от 1 до 5 👇"
                    )
                    try:
                        await bot.send_message(client["telegram_id"], text, reply_markup=kb, parse_mode="HTML")
                    except TelegramRetryAfter as e:
                        await asyncio.sleep(e.retry_after + 0.5)
                        await bot.send_message(client["telegram_id"], text, reply_markup=kb, parse_mode="HTML")

                    await db.create_review_request(client["id"], f["id"])
                    log.info(f"Review request sent: client={client['id']} formula={f['id']}")
                    await asyncio.sleep(0.05)

                except Exception as e:
                    log.error(f"Review request error (formula={f['id']}): {e}")

        except Exception as e:
            log.error(f"review_requester loop error: {e}")