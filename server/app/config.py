import os
from datetime import datetime
from zoneinfo import ZoneInfo


PUBLIC_PATH_PREFIX = os.getenv("PUBLIC_PATH_PREFIX", "/gate-control").rstrip("/")
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")

DEVICE_ID = os.getenv("DEVICE_ID", "gate-main")
DEVICE_SECRET = os.getenv("DEVICE_SECRET", os.getenv("DEVICE_TOKEN", ""))

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

COMMAND_RELAY_TIME_MS = int(os.getenv("COMMAND_RELAY_TIME_MS", "700"))
TOKEN_DEFAULT_VALID_HOURS = int(os.getenv("TOKEN_DEFAULT_VALID_HOURS", "72"))
OPEN_COOLDOWN_SECONDS = int(os.getenv("OPEN_COOLDOWN_SECONDS", "5"))
APP_TIMEZONE = ZoneInfo(os.getenv("APP_TIMEZONE", "Europe/Warsaw"))

# SQLite has valid_to as NOT NULL. Forever-valid pilots use a distant
# technical date plus the valid_forever flag.
FOREVER_VALID_TO = datetime(9999, 12, 31, 23, 59, 59)
