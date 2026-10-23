import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot Token (can be set in .env or hardcoded here)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Database Path
DB_PATH = os.path.join(os.path.dirname(__file__), "mailfuetch.db")

# Muellfach API Configuration
MUELLFACH_BASE_URL = "https://www.muellfach.de"
MUELLFACH_SYNC_URL = f"{MUELLFACH_BASE_URL}/sync.php"
MUELLFACH_API_URL = f"{MUELLFACH_BASE_URL}/api.php"
DOMAIN = "muellfach.de"

# Polling Interval (in seconds)
POLL_INTERVAL = 3.0

# Email Retention Days
RETENTION_DAYS = 10
