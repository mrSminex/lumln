"""
Запуск бота и веб-панели в одном процессе.
"""
import asyncio
import uvicorn
import logging
from config import ADMIN_PORT
from aiogram.client.session.aiohttp import AiohttpSession

log = logging.getLogger(__name__)


async def main():
    # Инициализация БД
    import database as db
    await db.init_db()
    log.info("Database ready")

    # Запускаем бот и веб-панель параллельно
    from bot import run_bot
    from admin_panel import app

    web_config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=ADMIN_PORT,
        log_level="warning",
    )
    web_server = uvicorn.Server(web_config)

    await asyncio.gather(
        run_bot(),
        web_server.serve(),
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    asyncio.run(main())
