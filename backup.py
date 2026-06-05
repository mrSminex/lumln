"""
Модуль резервного копирования базы данных.

Схема работы:
  1. Создаём «горячую» копию SQLite через sqlite3 API (без блокировки основной БД).
  2. Сохраняем файл в папку backups/ с датой в имени.
  3. Удаляем старые копии, оставляя только BACKUP_KEEP штук.
  4. Отправляем файл в Telegram администратору (BACKUP_CHAT_ID).
  5. Запускается автоматически каждый день в BACKUP_HOUR:00 UTC.
"""

import asyncio
import logging
import os
import sqlite3
import shutil
from datetime import datetime, timezone
from pathlib import Path

from config import DB_PATH, BACKUP_DIR, BACKUP_KEEP, BACKUP_CHAT_ID, BACKUP_HOUR

log = logging.getLogger(__name__)


# ─── Создание локальной копии ─────────────────────────────────────────────────

def _make_local_backup() -> Path:
    """
    Копирует БД через sqlite3.connect().backup() — безопасно даже пока
    бот пишет в базу. Возвращает Path к новому файлу.
    """
    backup_dir = Path(BACKUP_DIR)
    backup_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"lumln_{timestamp}.db"

    src_conn = sqlite3.connect(DB_PATH)
    dst_conn = sqlite3.connect(str(dest))
    try:
        src_conn.backup(dst_conn)
    finally:
        src_conn.close()
        dst_conn.close()

    log.info(f"Backup created: {dest} ({dest.stat().st_size // 1024} KB)")
    return dest


def _rotate_local_backups() -> None:
    """Удаляет старые бэкапы, оставляя BACKUP_KEEP самых свежих."""
    backup_dir = Path(BACKUP_DIR)
    if not backup_dir.exists():
        return

    files = sorted(backup_dir.glob("lumln_*.db"), key=lambda p: p.stat().st_mtime)
    to_delete = files[:-BACKUP_KEEP] if len(files) > BACKUP_KEEP else []
    for f in to_delete:
        f.unlink()
        log.info(f"Old backup removed: {f.name}")


# ─── Отправка в Telegram ──────────────────────────────────────────────────────

async def _send_to_telegram(backup_path: Path) -> bool:
    """Отправляет файл бэкапа администратору в Telegram. Возвращает True при успехе."""
    if not BACKUP_CHAT_ID:
        return False

    try:
        from bot import bot
        from aiogram.types import FSInputFile

        size_kb = backup_path.stat().st_size // 1024
        caption = (
            f"🗄 <b>Резервная копия базы LUM'N</b>\n\n"
            f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            f"💾 Размер: {size_kb} KB\n"
            f"📁 Файл: <code>{backup_path.name}</code>\n\n"
            f"Для восстановления: скачайте файл и замените им <code>lumln.db</code> на сервере."
        )
        await bot.send_document(
            BACKUP_CHAT_ID,
            document=FSInputFile(str(backup_path), filename=backup_path.name),
            caption=caption,
            parse_mode="HTML",
        )
        log.info(f"Backup sent to Telegram chat {BACKUP_CHAT_ID}")
        return True
    except Exception as e:
        log.error(f"Failed to send backup to Telegram: {e}")
        return False


# ─── Основная функция бэкапа ──────────────────────────────────────────────────

async def run_backup(initiated_by: str = "scheduler") -> dict:
    """
    Выполняет полный цикл бэкапа.
    Возвращает словарь с результатом для отображения в UI.
    """
    log.info(f"Starting backup (initiated by: {initiated_by})")
    result = {"ok": False, "file": "", "size_kb": 0, "telegram": False, "error": ""}

    try:
        backup_path = _make_local_backup()
        _rotate_local_backups()

        result["file"] = backup_path.name
        result["size_kb"] = backup_path.stat().st_size // 1024

        tg_sent = await _send_to_telegram(backup_path)
        result["telegram"] = tg_sent
        result["ok"] = True

        log.info(f"Backup complete: {backup_path.name}, telegram={tg_sent}")
    except Exception as e:
        result["error"] = str(e)
        log.error(f"Backup failed: {e}")

    return result


# ─── Планировщик (запускается в фоне вместе с ботом) ─────────────────────────

async def backup_scheduler() -> None:
    """
    Бесконечный цикл: ждёт наступления BACKUP_HOUR:00 UTC и запускает бэкап.
    Засыпает каждые 60 секунд и проверяет время — надёжнее чем считать секунды.
    """
    log.info(f"Backup scheduler started (daily at {BACKUP_HOUR:02d}:00 UTC)")
    last_backup_date: str = ""

    while True:
        await asyncio.sleep(60)
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")

        if now.hour == BACKUP_HOUR and today != last_backup_date:
            last_backup_date = today
            await run_backup(initiated_by="scheduler")


# ─── Список локальных бэкапов ─────────────────────────────────────────────────

def list_local_backups() -> list[dict]:
    """Возвращает список локальных бэкапов для отображения в веб-панели."""
    backup_dir = Path(BACKUP_DIR)
    if not backup_dir.exists():
        return []

    files = sorted(backup_dir.glob("lumln_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    result = []
    for f in files:
        stat = f.stat()
        result.append({
            "name": f.name,
            "size_kb": stat.st_size // 1024,
            "created": datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m.%Y %H:%M"),
            "path": str(f),
        })
    return result
