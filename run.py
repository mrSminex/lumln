"""
Запуск бота и веб-панели в одном процессе.
"""
import asyncio
import socket
import sys
import uvicorn
import logging
from config import ADMIN_PORT

log = logging.getLogger(__name__)


def _check_port(port: int) -> None:
    """Проверяет, свободен ли порт. Если занят — выводит понятную ошибку и завершает."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
        except OSError:
            log.error(
                f"\n\n"
                f"  ❌  Порт {port} уже занят!\n\n"
                f"  Освободите его командой:\n"
                f"      sudo fuser -k {port}/tcp\n\n"
                f"  Или укажите другой порт в .env:\n"
                f"      ADMIN_PORT=8081\n"
            )
            sys.exit(1)


async def main() -> None:
    # Проверяем порт ДО запуска — чтобы получить понятную ошибку
    _check_port(ADMIN_PORT)

    # Инициализация БД
    import database as db
    await db.init_db()
    log.info("Database ready")

    # Загружаем бот и веб-панель
    from bot import run_bot
    from admin_panel import app

    web_config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=ADMIN_PORT,
        log_level="warning",
        # SO_REUSEADDR позволяет занять порт сразу после перезапуска
        # без ожидания TIME_WAIT (актуально на Linux)
    )
    web_server = uvicorn.Server(web_config)

    log.info(f"Web panel → http://0.0.0.0:{ADMIN_PORT}")

    from backup import backup_scheduler
    await asyncio.gather(
        run_bot(),
        web_server.serve(),
        backup_scheduler(),
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    asyncio.run(main())
