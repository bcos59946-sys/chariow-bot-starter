import os
from dotenv import load_dotenv

load_dotenv()

def csv_ints(value: str) -> set[int]:
    result = set()
    for part in (value or "").split(","):
        part = part.strip()
        if part:
            try:
                result.add(int(part))
            except ValueError:
                continue
    return result

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHARIOW_API_KEY = os.getenv("CHARIOW_API_KEY", "").strip()
CHARIOW_CURRENCY = os.getenv("CHARIOW_CURRENCY", "CDF").strip().upper()
CHARIOW_API_BASE = os.getenv("CHARIOW_API_BASE", "https://api.chariow.com/v1").rstrip("/")
ADMIN_TELEGRAM_IDS = csv_ints(os.getenv("ADMIN_TELEGRAM_IDS", ""))
