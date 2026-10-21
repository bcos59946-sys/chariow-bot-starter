from pathlib import Path
from dotenv import dotenv_values

env_path = Path(".env")
if not env_path.exists():
    print("ERREUR: .env absent. Copie .env.example vers .env.")
    raise SystemExit(1)
values = dotenv_values(env_path)
required = ["TELEGRAM_BOT_TOKEN", "CHARIOW_API_KEY", "ADMIN_TELEGRAM_IDS"]
missing = [key for key in required if not values.get(key)]
if missing:
    print("Variables à configurer localement :", ", ".join(missing))
    raise SystemExit(1)
print("Variables requises présentes. Les valeurs ne seront pas affichées.")
