import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot Token
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Domain & Platform Configuration
DOMAIN = os.getenv("DOMAIN", "muellfach.de")
MUELLFACH_BASE_URL = os.getenv("MUELLFACH_BASE_URL", f"https://www.{DOMAIN}")
MUELLFACH_SYNC_URL = f"{MUELLFACH_BASE_URL}/sync.php"
MUELLFACH_API_URL = f"{MUELLFACH_BASE_URL}/api.php"

# Database Path (configurable for persistent volume on Render/Railway)
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "mailfuetch.db"))

# Polling Interval (in seconds)
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "3.0"))

# Email Retention Days (10 days as requested)
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "10"))
