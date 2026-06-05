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
