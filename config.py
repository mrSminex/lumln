import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
ADMIN_IDS: list[int] = [
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
]
ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "lumln2024")
YCLIENTS_URL: str = os.getenv("YCLIENTS_URL", "https://yclients.com")
PRIVACY_URL: str = os.getenv("PRIVACY_URL", "https://telegra.ph/privacy")
OFFER_URL: str = os.getenv("OFFER_URL", "https://telegra.ph/offer")
ADMIN_PORT: int = int(os.getenv("ADMIN_PORT", "8080"))
DB_PATH: str = "lumln.db"

# Прокси для aiogram (пустая строка = без прокси)
PROXY: str = os.getenv("PROXY", "")

# ── Бэкапы ────────────────────────────────────────────────────────────────────
# Telegram chat_id, куда слать бэкап (обычно = ваш личный ADMIN_IDS[0])
# Оставьте пустым чтобы отключить отправку в Telegram
BACKUP_CHAT_ID: int | None = (
    int(os.getenv("BACKUP_CHAT_ID")) if os.getenv("BACKUP_CHAT_ID") else None
)
# Час суток для автобэкапа (0-23, по UTC)
BACKUP_HOUR: int = int(os.getenv("BACKUP_HOUR", "3"))
# Сколько локальных копий хранить на сервере
BACKUP_KEEP: int = int(os.getenv("BACKUP_KEEP", "7"))
# Папка для локальных бэкапов
BACKUP_DIR: str = os.getenv("BACKUP_DIR", "backups")
